"""
Rate limiter with token buckets, live `x-ratelimit-*` header sync, and cross-process
persistence in `%APPDATA%\\PromptCore\\usage.json` protected by `filelock` (§4.6, Task 3.2).
Both TextPrompt AI and VoicePrompt AI share this file so daily quotas are tracked accurately.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from promptcore.config import ProviderLimits, get_appdata_dir

logger = logging.getLogger(__name__)


def _utc_day_str(now_ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(now_ts if now_ts is not None else time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _parse_int_header(headers: dict[str, str], *keys: str) -> int | None:
    for k in keys:
        val = headers.get(k.lower())
        if val is not None:
            try:
                return int(float(val))
            except ValueError:
                continue
    return None


class RateLimiter:
    """
    Tracks per-provider minute and daily request/token usage across both processes.
    """

    def __init__(
        self,
        limits_by_provider: dict[str, ProviderLimits] | None = None,
        usage_file: Path | str | None = None,
        safety_margin: float = 0.10,
    ) -> None:
        self.limits: dict[str, ProviderLimits] = limits_by_provider or {}
        self.safety_margin = max(0.0, min(0.50, safety_margin))
        self.usage_path = Path(usage_file) if usage_file else (get_appdata_dir() / "usage.json")
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.usage_path.with_suffix(".json.lock")
        self._file_lock = FileLock(str(self.lock_path), timeout=2.0)
        self._mem_lock = threading.Lock()

        # In-memory sliding 60-second windows: provider -> list of (timestamp, tokens)
        self._minute_events: dict[str, list[tuple[float, int]]] = {}
        # Live remaining overrides from provider response headers: provider -> (timestamp, rem_reqs, rem_toks)
        self._header_overrides: dict[str, tuple[float, int | None, int | None]] = {}

    def _prune_minute_window(self, provider: str, now: float) -> list[tuple[float, int]]:
        events = self._minute_events.get(provider, [])
        cutoff = now - 60.0
        kept = [(ts, tok) for ts, tok in events if ts > cutoff]
        self._minute_events[provider] = kept
        return kept

    def _read_usage_disk(self, today: str) -> dict[str, Any]:
        """Read shared usage.json inside FileLock, resetting if UTC date rolled over."""
        if not self.usage_path.exists():
            return {"date": today, "providers": {}}
        try:
            raw = self.usage_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict) or data.get("date") != today:
                return {"date": today, "providers": {}}
            if "providers" not in data or not isinstance(data["providers"], dict):
                data["providers"] = {}
            return data
        except Exception as exc:
            logger.warning("Corrupt or unreadable usage.json (%s), resetting for %s.", exc, today)
            return {"date": today, "providers": {}}

    def _write_usage_disk(self, data: dict[str, Any]) -> None:
        tmp_path = self.usage_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(self.usage_path)

    def has_capacity(
        self,
        provider: str,
        estimated_tokens: int = 250,
        safety_margin: float | None = None,
    ) -> bool:
        """
        Return True if `provider` has both per-minute and per-day capacity
        above `safety_margin` (default 10%).
        """
        norm = provider.strip().lower()
        lim = self.limits.get(norm, ProviderLimits())
        margin = self.safety_margin if safety_margin is None else safety_margin
        keep_ratio = max(0.05, 1.0 - margin)

        now = time.time()
        with self._mem_lock:
            # 1. Check live header overrides if fresh (< 45 seconds old)
            override = self._header_overrides.get(norm)
            if override is not None:
                ts, rem_req, rem_tok = override
                if (now - ts) < 45.0:
                    if rem_req is not None and rem_req <= max(1, int(lim.rpm * margin)):
                        return False
                    if rem_tok is not None and rem_tok < max(estimated_tokens, int(lim.tpm * margin)):
                        return False

            # 2. Check in-memory 60s window (RPM / TPM)
            events = self._prune_minute_window(norm, now)
            used_rpm = len(events)
            used_tpm = sum(tok for _, tok in events)
            if used_rpm + 1 > max(1, int(lim.rpm * keep_ratio)):
                return False
            if used_tpm + estimated_tokens > max(estimated_tokens, int(lim.tpm * keep_ratio)):
                return False

        # 3. Check shared daily usage file (RPD / TPD)
        today = _utc_day_str(now)
        try:
            with self._file_lock:
                data = self._read_usage_disk(today)
                p_stats = data["providers"].get(norm, {"requests": 0, "tokens": 0})
                used_rpd = int(p_stats.get("requests", 0))
                used_tpd = int(p_stats.get("tokens", 0))
                if used_rpd + 1 > max(1, int(lim.rpd * keep_ratio)):
                    return False
                if used_tpd + estimated_tokens > max(estimated_tokens, int(lim.tpd * keep_ratio)):
                    return False
        except Exception as exc:
            logger.debug("FileLock read skipped in has_capacity: %s", exc)

        return True

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Record a completed (or hedged) API call in the minute bucket, update live
        header overrides, and atomically increment `%APPDATA%\\PromptCore\\usage.json`.
        """
        norm = provider.strip().lower()
        total_tokens = max(1, input_tokens + output_tokens)
        now = time.time()

        with self._mem_lock:
            events = self._prune_minute_window(norm, now)
            events.append((now, total_tokens))

            if headers:
                rem_req = _parse_int_header(
                    headers,
                    "x-ratelimit-remaining-requests",
                    "x-ratelimit-remaining-requests-day",
                )
                rem_tok = _parse_int_header(
                    headers,
                    "x-ratelimit-remaining-tokens",
                    "x-ratelimit-remaining-tokens-minute",
                )
                if rem_req is not None or rem_tok is not None:
                    self._header_overrides[norm] = (now, rem_req, rem_tok)

        today = _utc_day_str(now)
        try:
            with self._file_lock:
                data = self._read_usage_disk(today)
                p_entry = data["providers"].setdefault(
                    norm,
                    {"requests": 0, "tokens": 0, "models": {}},
                )
                p_entry["requests"] = int(p_entry.get("requests", 0)) + 1
                p_entry["tokens"] = int(p_entry.get("tokens", 0)) + total_tokens
                models_map = p_entry.setdefault("models", {})
                m_entry = models_map.setdefault(model, {"requests": 0, "tokens": 0})
                m_entry["requests"] = int(m_entry.get("requests", 0)) + 1
                m_entry["tokens"] = int(m_entry.get("tokens", 0)) + total_tokens
                self._write_usage_disk(data)
        except Exception as exc:
            logger.warning("Failed to update shared usage.json: %s", exc)

    def get_daily_summary(self) -> dict[str, dict[str, Any]]:
        """Return today's usage per provider for the system tray menu (Task 5.2)."""
        today = _utc_day_str()
        try:
            with self._file_lock:
                data = self._read_usage_disk(today)
                return dict(data.get("providers", {}))
        except Exception:
            return {}