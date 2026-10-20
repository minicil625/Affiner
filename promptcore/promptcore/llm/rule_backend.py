"""
`RuleBackend` wraps the deterministic `RulePipeline` in an `LLMResult` interface
for offline / local-only / final-fallback execution (Task 3.1).
"""

from __future__ import annotations

import time

from promptcore.llm.base import LLMResult
from promptcore.rules.pipeline import RulePipeline


class RuleBackend:
    """Executes deterministic rules locally and returns an `LLMResult`."""

    def __init__(self, pipeline: RulePipeline | None = None) -> None:
        self.pipeline = pipeline or RulePipeline()

    def run_fallback(self, text: str) -> LLMResult:
        t0 = time.perf_counter()
        res = self.pipeline.run(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return LLMResult(
            text=res.text,
            provider="local",
            model="rules",
            ttft_ms=elapsed_ms,
            total_ms=elapsed_ms,
            input_tokens=0,
            output_tokens=0,
        )