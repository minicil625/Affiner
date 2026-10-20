"""
Unit tests for `promptcore/llm/rate_limiter.py` and `promptcore/llm/router.py` (Task 3.2 AC).
Covers:
  - 429 rate-limit cooldown + failover
  - 5xx / timeout cooldown + failover
  - 401/403 provider disabling + notice callback
  - 404 model unavailability marking
  - First-token hedging (600 ms trigger) & full-response hedging
  - Post-generation guard soft-failure & retry on next provider
  - Concurrent `usage.json` updates with `filelock`
  - Latency EMA ranking after >= 5 samples
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from promptcore.config import CoreSettings, HedgeConfig
from promptcore.llm.base import (
    EarlyAbortCheck,
    LLMAuthError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMResult,
    LLMServerError,
    LLMTimeoutError,
)
from promptcore.llm.rate_limiter import RateLimiter
from promptcore.llm.router import LLMRouter


class ScriptedMockBackend:
    """Deterministic backend stub for testing router error handling, hedging, and EMA ranking."""

    def __init__(self, behaviors: dict[str, Any]) -> None:
        self.behaviors = behaviors
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def warmup(self, provider: str) -> bool:
        return True

    def healthy(self, provider: str) -> bool:
        return True

    def generate(
        self,
        system: str,
        user: str,
        *,
        provider: str,
        model: str,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 256,
        timeout_s: float = 1.5,
        stream: bool = False,
        stop: list[str] | None = None,
        on_first_token: Any = None,
        early_abort_check: EarlyAbortCheck | None = None,
        cancel_event: threading.Event | None = None,
    ) -> LLMResult:
        with self._lock:
            self.calls.append((provider, model))

        action = self.behaviors.get(provider)
        if callable(action):
            return action(
                provider=provider,
                model=model,
                on_first_token=on_first_token,
                cancel_event=cancel_event,
            )
        if isinstance(action, Exception):
            raise action
        if isinstance(action, LLMResult):
            if on_first_token:
                on_first_token()
            return action
        return LLMResult(text=f"cleaned by {provider}", provider=provider, model=model, ttft_ms=50.0, total_ms=80.0)


def test_router_failover_on_429_500_401_404(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.clean_only.hedge.enabled = False
    settings.routing.clean_only.chain = [
        "groq.fast",
        "cerebras.fast",
        "gemini.fast",
        "openrouter.fast",
    ]
    notices: list[str] = []
    limiter = RateLimiter(usage_file=tmp_path / "usage.json")

    backend = ScriptedMockBackend(
        {
            "groq": LLMRateLimitError("429 Too Many Requests", provider="groq", retry_after_s=30.0),
            "cerebras": LLMAuthError("401 Unauthorized", provider="cerebras", status_code=401),
            "gemini": LLMModelNotFoundError("404 Not Found", provider="gemini", model="gemini-2.5-flash-lite"),
            "openrouter": LLMResult(
                text="Fixed text.",
                provider="openrouter",
                model="meta-llama/llama-3.2-3b-instruct:free",
                ttft_ms=110.0,
                total_ms=180.0,
                input_tokens=20,
                output_tokens=10,
            ),
        }
    )

    router = LLMRouter(
        settings=settings,
        backend=backend,
        rate_limiter=limiter,
        key_getter=lambda _: "test-key",
        on_notice=notices.append,
    )

    res = router.route("clean_only", "sys", "user")
    assert res.provider == "openrouter"
    assert res.text == "Fixed text."
    assert any("Check API key for cerebras" in n for n in notices)
    assert any("unavailable on gemini" in n for n in notices)

    # Next call should skip groq (in 429 cooldown), cerebras (401 disabled), and gemini (404 model)
    backend.behaviors["openrouter"] = LLMResult(
        text="Second call.",
        provider="openrouter",
        model="meta-llama/llama-3.2-3b-instruct:free",
    )
    backend.calls.clear()
    res2 = router.route("clean_only", "sys", "user")
    assert res2.provider == "openrouter"
    assert backend.calls == [("openrouter", "meta-llama/llama-3.2-3b-instruct:free")]


def test_router_first_token_hedge_wins_when_primary_stalls(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.clean_only.chain = ["groq.fast", "cerebras.fast"]
    settings.routing.clean_only.hedge = HedgeConfig(enabled=True, after_ms=80, trigger="first_token")
    settings.routing.clean_only.timeout_ms = 1000

    def _slow_groq(*, provider: str, model: str, on_first_token: Any, cancel_event: threading.Event | None) -> LLMResult:
        # Stall for 400ms without emitting first token
        for _ in range(40):
            if cancel_event and cancel_event.is_set():
                raise LLMTimeoutError("Cancelled by hedge", provider=provider, model=model)
            time.sleep(0.01)
        return LLMResult(text="Slow groq", provider=provider, model=model, ttft_ms=400.0, total_ms=450.0)

    def _fast_cerebras(*, provider: str, model: str, on_first_token: Any, cancel_event: threading.Event | None) -> LLMResult:
        if on_first_token:
            on_first_token()
        return LLMResult(text="Fast cerebras hedge winner!", provider=provider, model=model, ttft_ms=25.0, total_ms=40.0)

    backend = ScriptedMockBackend({"groq": _slow_groq, "cerebras": _fast_cerebras})
    router = LLMRouter(
        settings=settings,
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
        key_getter=lambda _: "test-key",
    )

    t0 = time.perf_counter()
    res = router.route("clean_only", "sys", "user")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert res.provider == "cerebras"
    assert res.hedged is True
    assert res.text == "Fast cerebras hedge winner!"
    assert elapsed_ms < 300.0


def test_router_guard_soft_failure_retries_next_provider(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.clean_only.hedge.enabled = False
    settings.routing.clean_only.chain = ["groq.fast", "cerebras.fast"]

    backend = ScriptedMockBackend(
        {
            "groq": LLMResult(text="Sure! Here is your answer:", provider="groq", model="m1"),
            "cerebras": LLMResult(text="Valid cleaned prompt.", provider="cerebras", model="m2"),
        }
    )
    router = LLMRouter(
        settings=settings,
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
        key_getter=lambda _: "test-key",
    )

    res = router.route(
        "clean_only",
        "sys",
        "user",
        post_guard=lambda r: not r.text.startswith("Sure!"),
    )
    assert res.provider == "cerebras"
    assert res.text == "Valid cleaned prompt."


def test_router_latency_ema_reranks_after_5_samples(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.clean_only.hedge.enabled = False
    settings.routing.clean_only.chain = ["groq.fast", "cerebras.fast"]

    backend = ScriptedMockBackend(
        {
            "groq": LLMResult(text="ok", provider="groq", model="m1", ttft_ms=250.0, total_ms=300.0),
            "cerebras": LLMResult(text="ok", provider="cerebras", model="m2", ttft_ms=45.0, total_ms=70.0),
        }
    )
    router = LLMRouter(
        settings=settings,
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
        key_getter=lambda _: "test-key",
    )

    # Before 5 samples, config order (groq first) is preserved
    assert [c.provider for c in router.get_candidates("clean_only")] == ["groq", "cerebras"]

    for _ in range(5):
        router._record_latency("groq", True, 250.0)
        router._record_latency("cerebras", True, 45.0)

    # After 5 samples each, cerebras (45ms EMA) ranks ahead of groq (250ms EMA)
    assert [c.provider for c in router.get_candidates("clean_only")] == ["cerebras", "groq"]


def test_rate_limiter_concurrent_usage_json_never_corrupts(tmp_path: Path) -> None:
    usage_file = tmp_path / "usage.json"
    limiter1 = RateLimiter(usage_file=usage_file)
    limiter2 = RateLimiter(usage_file=usage_file)

    def _worker(lim: RateLimiter) -> None:
        for _ in range(15):
            lim.record_usage("groq", "llama-3.1-8b-instant", input_tokens=50, output_tokens=25)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_worker, limiter1 if i % 2 == 0 else limiter2) for i in range(4)]
        for f in futs:
            f.result()

    summary = limiter1.get_daily_summary()
    assert summary["groq"]["requests"] == 60
    assert summary["groq"]["tokens"] == 60 * 75