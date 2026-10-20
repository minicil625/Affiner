"""
Task 6.1 & Task 6.2 — Chaos, robustness, and privacy hardening tests:
  1. Offline / all-providers-failing chaos test -> clean rules-only fallback & recovery.
  2. Privacy `excluded_apps` (e.g. `KeePassXC.exe`) and `allow_cloud_text=False` -> zero network calls.
  3. KeyRedactingFormatter ensures API keys are never written to logs even when raised in exceptions.
  4. Elevated (admin) target window detection -> safe abort without corrupting clipboard.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

import textprompt.text_capture as capture_mod
from promptcore.llm.base import LLMServerError
from promptcore.llm.rate_limiter import RateLimiter
from promptcore.prompt_engine import PromptEngine
from textprompt.config import AppSettings
from textprompt.main import KeyRedactingFormatter
from textprompt.text_capture import ClipboardBackup, capture_selection


class FailingChaosBackend:
    """Simulates complete network outage or 503 errors across all cloud providers."""

    def __init__(self) -> None:
        self.attempts = 0

    def warmup(self, provider: str) -> bool:
        return False

    def healthy(self, provider: str) -> bool:
        return False

    def generate(self, system: str, user: str, **kwargs: Any) -> Any:
        self.attempts += 1
        raise LLMServerError(
            "Simulated network disconnect / 503 outage",
            provider=kwargs.get("provider", "groq"),
            model=kwargs.get("model", "fast"),
            status_code=503,
        )


def test_chaos_all_providers_offline_falls_back_cleanly_in_all_3_modes(tmp_path: Path) -> None:
    """
    Task 6.1 AC: When Wi-Fi is disconnected or all providers fail, Modes 1, 2, and 3
    never raise an unhandled exception and always return usable deterministic fallback output.
    """
    settings = AppSettings()
    settings.routing.clean_only.hedge.enabled = False
    chaos_backend = FailingChaosBackend()

    engine = PromptEngine(
        settings=settings,
        input_type="typed",
        backend=chaos_backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )
    engine.router._key_getter = lambda _: "valid-key"

    raw = "send `report.json` to John wait actually send it to Sarah tomorrow"

    # Mode 1 fallback -> deterministic rule output
    res1 = engine.process(raw, mode="clean_only")
    assert res1.path == "fallback"
    assert "`report.json`" in res1.output
    assert res1.notice is not None

    # Mode 2 fallback -> Mode 1 rule output + notice
    res2 = engine.process(raw, mode="clean_plus_improved")
    assert res2.path == "fallback"
    assert "`report.json`" in res2.output

    # Mode 3 fallback -> valid JSON `{"goal": <clean text>, ...}`
    res3 = engine.process(raw, mode="structured_json")
    assert res3.path == "fallback"
    assert '"goal":' in res3.output
    assert "`report.json`" in res3.output


def test_privacy_excluded_apps_and_allow_cloud_false_make_zero_network_calls(tmp_path: Path) -> None:
    """
    Task 6.2 AC: `excluded_apps` (`local_only=True`) and `allow_cloud_text=False`
    send zero network traffic even in Modes 2 and 3.
    """
    settings = AppSettings()
    chaos_backend = FailingChaosBackend()
    engine = PromptEngine(
        settings=settings,
        input_type="typed",
        backend=chaos_backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )
    engine.router._key_getter = lambda _: "valid-key"

    # 1. Excluded app (e.g., KeePassXC.exe sets local_only=True)
    assert settings.is_app_excluded("KeePassXC.exe") is True
    res_excluded = engine.process(
        "send `secret_vault.kdbx` to John wait actually Sarah",
        mode="clean_plus_improved",
        local_only=True,
    )
    assert chaos_backend.attempts == 0
    assert "`secret_vault.kdbx`" in res_excluded.output

    # 2. Global privacy switch `allow_cloud_text = False`
    settings.privacy.allow_cloud_text = False
    res_offline = engine.process(
        "send `secret_vault.kdbx` to John wait actually Sarah",
        mode="structured_json",
        local_only=False,
    )
    assert chaos_backend.attempts == 0
    assert '"goal":' in res_offline.output


def test_elevated_target_window_aborts_cleanly_without_losing_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Task 6.1 AC: Elevated (admin) target window returns `is_elevated=True` immediately
    without overwriting the user's clipboard.
    """
    monkeypatch.setattr(capture_mod, "get_foreground_window_info", lambda: (555, "Taskmgr.exe", True))
    monkeypatch.setattr(
        capture_mod,
        "backup_clipboard",
        lambda mode="all": ClipboardBackup(formats={13: "safe_clipboard_data"}),
    )

    res = capture_selection(AppSettings())
    assert res.is_elevated is True
    assert res.empty is True
    assert res.backup.formats[13] == "safe_clipboard_data"


def test_key_redacting_formatter_scrubs_secrets_from_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Task 6.2 AC: API keys are never written to logs even if echoed in an error message.
    """
    import promptcore.secrets as secrets_mod

    fake_secret = "gsk_LiveSecretGroqKey987654321"
    monkeypatch.setattr(
        secrets_mod,
        "get",
        lambda prov: fake_secret if prov == "groq" else None,
    )

    formatter = KeyRedactingFormatter("%(levelname)s: %(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg=f"Auth error with Bearer {fake_secret} on groq",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert fake_secret not in formatted
    assert "[REDACTED:groq:gsk_...321]" in formatted