"""
Shared persistent `httpx.Client(http2=True)` per provider and connection warm-up (§4.7).
Reuses TLS sessions across triggers to save 100-300 ms per call.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Final

import httpx

from promptcore.llm.providers import PRESETS, get_preset
from promptcore.secrets import get as get_secret

logger = logging.getLogger(__name__)

IDLE_REWARM_SECONDS: Final[float] = 240.0  # 4 minutes (§4.7)


class ConnectionManager:
    """
    Manages one persistent HTTP/2 client per provider and tracks last-activity
    timestamps so connections idle > 4 minutes are re-warmed on trigger press.
    """

    def __init__(self, key_getter: callable | None = None) -> None:
        self._clients: dict[str, httpx.Client] = {}
        self._last_used: dict[str, float] = {}
        self._healthy: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._key_getter = key_getter or get_secret

    def get_client(self, provider: str) -> httpx.Client:
        """Return or create the persistent HTTP/2 client for `provider`."""
        norm = provider.strip().lower()
        with self._lock:
            client = self._clients.get(norm)
            if client is not None and not client.is_closed:
                return client

            preset = get_preset(norm)
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                **preset.extra_headers,
            }
            api_key = self._key_getter(norm)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            try:
                client = httpx.Client(
                    base_url=preset.base_url,
                    headers=headers,
                    http2=True,
                    timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
                    limits=httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=300.0),
                )
            except ImportError:
                # Fallback if h2 is somehow unavailable in a minimal test environment
                client = httpx.Client(
                    base_url=preset.base_url,
                    headers=headers,
                    timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
                )

            self._clients[norm] = client
            return client

    def touch(self, provider: str) -> None:
        """Record successful activity on a provider's connection."""
        norm = provider.strip().lower()
        with self._lock:
            self._last_used[norm] = time.monotonic()
            self._healthy[norm] = True

    def is_idle(self, provider: str, threshold_s: float = IDLE_REWARM_SECONDS) -> bool:
        """Return True if the provider has never been warmed or has been idle > `threshold_s`."""
        norm = provider.strip().lower()
        with self._lock:
            last = self._last_used.get(norm)
        if last is None:
            return True
        return (time.monotonic() - last) > threshold_s

    def warmup_provider(self, provider: str, timeout_s: float = 2.0) -> bool:
        """
        Send a lightweight `GET /models` (costs 0 tokens) to establish TLS + HTTP/2 stream.
        """
        norm = provider.strip().lower()
        if norm not in PRESETS:
            return False
        api_key = self._key_getter(norm)
        if not api_key:
            return False

        try:
            client = self.get_client(norm)
            resp = client.get("/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout_s)
            ok = resp.status_code < 400
            with self._lock:
                self._last_used[norm] = time.monotonic()
                self._healthy[norm] = ok
            return ok
        except Exception as exc:
            logger.debug("Warmup failed for %s: %s", norm, exc)
            with self._lock:
                self._healthy[norm] = False
            return False

    def prewarm_if_idle(self, provider: str, background: bool = True) -> None:
        """
        Called on hotkey/push-to-talk press (§4.7): warms the primary provider
        if its connection hasn't been used in > 4 minutes.
        """
        if not self.is_idle(provider):
            return
        if background:
            t = threading.Thread(
                target=self.warmup_provider,
                args=(provider,),
                daemon=True,
                name=f"prewarm-{provider}",
            )
            t.start()
        else:
            self.warmup_provider(provider)

    def healthy(self, provider: str) -> bool:
        with self._lock:
            return self._healthy.get(provider.strip().lower(), True)

    def close_all(self) -> None:
        with self._lock:
            for client in self._clients.values():
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()