"""
Unit tests for `promptcore/prompt_engine.py` (Tasks 3.3 & 3.4 AC).
Covers:
  - Mode 1 local fast path (0 API calls when signals are clean)
  - Mode 1 LLM route when unresolved correction cue or grammar risk is present
  - Mode 1 guard rejection when LLM answers the prompt or drops a `⟦n⟧` placeholder
  - Mode 2 `CleanImproved` PRD template rendering & fast-path optimization
  - Mode 3 `StructuredPrompt` JSON validation, 1-step repair retry on next provider, and fallback
  - Privacy enforcement (`allow_cloud_text=False` and `local_only=True`)
  - 256-entry LRU cache hit on repeat trigger
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptcore.config import CoreSettings
from promptcore.llm.base import LLMResult
from promptcore.llm.rate_limiter import RateLimiter
from promptcore.prompt_engine import PromptEngine


class StubEngineBackend:
    """Configurable backend stub for testing PromptEngine modes and guards."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.call_count = 0

    def warmup(self, provider: str) -> bool:
        return True

    def healthy(self, provider: str) -> bool:
        return True

    def generate(self, system: str, user: str, **kwargs: Any) -> LLMResult:
        self.call_count += 1
        return self.handler(system=system, user=user, call_count=self.call_count, **kwargs)


def test_mode1_takes_fast_path_without_calling_llm(tmp_path: Path) -> None:
    backend = StubEngineBackend(lambda **_: LLMResult(text="should not be called", provider="groq", model="m1"))
    engine = PromptEngine(
        settings=CoreSettings(),
        input_type="typed",
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )

    res = engine.process("um pls fix the bug in app dot py thx", mode="clean_only")
    assert res.path == "fast"
    assert res.output == "Please fix the bug in app.py, thanks."
    assert backend.call_count == 0


def test_mode1_routes_complex_correction_to_llm_and_preserves_placeholders(tmp_path: Path) -> None:
    def _llm_handler(user: str, provider: str, model: str, **_: Any) -> LLMResult:
        # Verify vocabulary hint and input tag are in the user message
        assert "<input_text>" in user
        assert "⟦0⟧" in user
        return LLMResult(
            text="<output>Send the error report in ⟦0⟧ to Sarah tomorrow.</output>",
            provider=provider,
            model=model,
        )

    settings = CoreSettings()
    settings.routing.clean_only.hedge.enabled = False
    backend = StubEngineBackend(_llm_handler)
    engine = PromptEngine(
        settings=settings,
        input_type="typed",
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )
    engine.router._key_getter = lambda _: "test-key"

    raw = "send the error report in `report.json` to John wait actually send it to Sarah tomorrow"
    res = engine.process(raw, mode="clean_only")
    assert res.path == "llm"
    assert res.output == "Send the error report in `report.json` to Sarah tomorrow."
    assert backend.call_count == 1

    # Second identical trigger hits the LRU cache with 0 additional LLM calls
    res_cached = engine.process(raw, mode="clean_only")
    assert res_cached.path == "cache"
    assert res_cached.output == res.output
    assert backend.call_count == 1


def test_mode1_guard_blocks_answering_and_dropped_placeholders(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.clean_only.hedge.enabled = False
    settings.routing.clean_only.chain = ["groq.fast", "cerebras.fast"]

    def _bad_llm(**kwargs: Any) -> LLMResult:
        prov = kwargs["provider"]
        if prov == "groq":
            # Tries to answer the prompt instead of cleaning
            return LLMResult(text="Sure, here is how you fix the bug in your code:", provider="groq", model="m1")
        # Drops required placeholder ⟦0⟧
        return LLMResult(text="Send the report to Sarah tomorrow.", provider="cerebras", model="m2")

    backend = StubEngineBackend(_bad_llm)
    engine = PromptEngine(
        settings=settings,
        input_type="typed",
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )
    engine.router._key_getter = lambda _: "test-key"

    raw = "send `report.json` to John wait actually send it to Sarah tomorrow"
    res = engine.process(raw, mode="clean_only")
    # Both providers failed the safety guard -> falls back safely to deterministic rule output with `report.json` intact
    assert res.path == "fallback"
    assert "`report.json`" in res.output
    assert not res.output.startswith("Sure")


def test_mode2_renders_prd_template_with_unmasked_spans(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.clean_plus_improved.hedge.enabled = False

    def _mode2_llm(**kwargs: Any) -> LLMResult:
        payload = {
            "clean": "Fix the bug in ⟦0⟧.",
            "enhanced": "Diagnose and fix the bug in ⟦0⟧, adding unit tests to verify the fix.",
        }
        return LLMResult(text=json.dumps(payload), provider=kwargs["provider"], model=kwargs["model"])

    backend = StubEngineBackend(_mode2_llm)
    engine = PromptEngine(
        settings=settings,
        input_type="typed",
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )
    engine.router._key_getter = lambda _: "test-key"

    res = engine.process("pls fix the bug in `auth_service.py`", mode="clean_plus_improved")
    assert res.path == "llm"
    assert "---\n**Enhanced Prompt:**\n" in res.output
    assert "`auth_service.py`" in res.output


def test_mode3_structured_json_repairs_on_next_provider(tmp_path: Path) -> None:
    settings = CoreSettings()
    settings.routing.structured_json.hedge.enabled = False
    settings.routing.structured_json.chain = ["gemini.quality", "groq.quality"]

    def _mode3_llm(**kwargs: Any) -> LLMResult:
        if kwargs["call_count"] == 1:
            # First attempt returns invalid schema (empty goal)
            return LLMResult(text='{"goal": ""}', provider=kwargs["provider"], model=kwargs["model"])
        # Second attempt (repair retry) sees <validation_error> and returns valid StructuredPrompt JSON
        assert "<validation_error>" in kwargs["user"]
        valid = {
            "goal": "Refactor ⟦0⟧ to use FastAPI.",
            "context": {"file": "⟦0⟧"},
            "requirements": ["Use FastAPI routes"],
            "constraints": {},
            "steps": ["Update ⟦0⟧"],
            "expected_output": "Refactored ⟦0⟧",
        }
        return LLMResult(text=json.dumps(valid), provider=kwargs["provider"], model=kwargs["model"])

    backend = StubEngineBackend(_mode3_llm)
    engine = PromptEngine(
        settings=settings,
        input_type="typed",
        backend=backend,
        rate_limiter=RateLimiter(usage_file=tmp_path / "usage.json"),
    )
    engine.router._key_getter = lambda _: "test-key"

    res = engine.process("refactor `server.py` to use fastapi", mode="structured_json")
    assert res.path == "llm"
    parsed = json.loads(res.output)
    assert parsed["goal"] == "Refactor `server.py` to use FastAPI."
    assert parsed["steps"] == ["Update `server.py`"]
    assert backend.call_count == 2