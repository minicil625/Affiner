"""
`PromptEngine` — Unified public API for TextPrompt AI and VoicePrompt AI (§4.2, §4.11, Task 3.3).
Coordinates:
  - Deterministic masking & rule pipeline (`RulePipeline`)
  - Mode 1 fast-path evaluation (`FastPathSignals`)
  - Streaming early-abort & post-generation safety guards (§4.11)
  - Mode 2 `{clean, enhanced}` rendering and fast-path optimization
  - Mode 3 `StructuredPrompt` validation and 1-step repair retry on the next provider
  - Long-input chunking (> 150 words) and 256-entry LRU caching
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import ValidationError

from promptcore.cache import PromptCache
from promptcore.chunking import CHUNK_TRIGGER_WORDS, process_chunks_parallel, split_into_chunks
from promptcore.config import CoreSettings
from promptcore.llm.base import LLMBackend, LLMError, LLMResult
from promptcore.llm.net import ConnectionManager
from promptcore.llm.openai_compat import OpenAICompatibleBackend
from promptcore.llm.rate_limiter import RateLimiter
from promptcore.llm.router import LLMRouter
from promptcore.masking import PLACEHOLDER_RE, unmask, verify
from promptcore.prompts.builder import InputType, ModeName, build_system_prompt, build_user_message
from promptcore.prompts.schemas import (
    CleanImproved,
    StructuredPrompt,
    extract_json_object,
    load_custom_schema,
    render_mode2_output,
    render_mode3_output,
)
from promptcore.rules.pipeline import MidPipelineHook, PipelineResult, RulePipeline
from promptcore.signals import FastPathSignals

logger = logging.getLogger(__name__)

# Answering-the-prompt prefix patterns (§4.11)
_ANSWERING_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:"
    r"sure[,!.\s]|"
    r"here\s+(?:is|are)\b|"
    r"certainly[,!.\s]|"
    r"of\s+course[,!.\s]|"
    r"i\s+can\s+help|"
    r"i'd\s+be\s+happy\s+to|"
    r"as\s+an\s+ai\b"
    r")",
    re.IGNORECASE,
)

_LIST_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"(?m)^\s*(?:[-*+]|\d+\.)\s+")


@dataclass
class ProcessResult:
    """Returned by `PromptEngine.process(...)` (§4.2)."""

    output: str
    mode: ModeName
    path: Literal["fast", "llm", "fallback", "cache"]
    provider: str = "-"
    model: str = "-"
    timings: dict[str, float] = field(default_factory=dict)
    notice: str | None = None


def is_answering_prefix(text: str) -> bool:
    """Return True if the first ~30 chars match a conversational assistant reply prefix (§4.11)."""
    return bool(_ANSWERING_PREFIX_RE.match(text))


def _content_tokens(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z0-9⟦⟧]{2,}", text)}


def check_clean_guard(
    candidate_masked: str,
    reference_masked: str,
    spans: list[str],
    check_line_structure: bool = True,
) -> bool:
    """
    Evaluate §4.11 safety guards on Mode 1 (or Mode 2 `clean`):
      1. Placeholder integrity (`verify(candidate_masked, spans)`)
      2. Does not start with conversational answering prefix ("Sure/Here/Certainly/Of course")
      3. Length <= 1.4x input length (for inputs >= 20 chars)
      4. Content token overlap >= 50% with reference rule-cleaned text
      5. Line count / list marker count preserved (Mode 1)
    """
    if not candidate_masked or not candidate_masked.strip():
        return False

    if not verify(candidate_masked, spans):
        return False

    if is_answering_prefix(candidate_masked):
        return False

    ref_len = max(len(reference_masked.strip()), 1)
    cand_len = len(candidate_masked.strip())
    if ref_len >= 25 and cand_len > int(ref_len * 1.45) + 25:
        return False

    ref_tokens = _content_tokens(reference_masked)
    cand_tokens = _content_tokens(candidate_masked)
    if len(ref_tokens) >= 4:
        overlap = len(ref_tokens & cand_tokens) / len(ref_tokens)
        if overlap < 0.50:
            return False

    if check_line_structure:
        ref_lines = [ln for ln in reference_masked.strip().splitlines() if ln.strip()]
        cand_lines = [ln for ln in candidate_masked.strip().splitlines() if ln.strip()]
        if len(ref_lines) != len(cand_lines):
            return False
        ref_markers = len(_LIST_MARKER_RE.findall(reference_masked))
        cand_markers = len(_LIST_MARKER_RE.findall(candidate_masked))
        if ref_markers != cand_markers:
            return False

    return True


class PromptEngine:
    """
    Main shared engine for TextPrompt AI (`input_type="typed"`) and
    VoicePrompt AI (`input_type="transcript"`).
    """

    def __init__(
        self,
        settings: CoreSettings | None = None,
        input_type: InputType = "typed",
        vocab_path: Path | str | None = None,
        shorthand_path: Path | str | None = None,
        backend: LLMBackend | None = None,
        rate_limiter: RateLimiter | None = None,
        typo_hook: MidPipelineHook | None = None,
        on_notice: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings or CoreSettings()
        self.input_type: InputType = input_type
        self.typo_hook = typo_hook
        self.on_notice = on_notice

        self.pipeline = RulePipeline.from_paths(
            vocab_path=vocab_path,
            shorthand_path=shorthand_path,
            input_type=input_type,
            sentence_punctuation=self.settings.modes.sentence_punctuation,
        )
        self.conn_manager = ConnectionManager()
        self.backend = backend or OpenAICompatibleBackend(conn_manager=self.conn_manager)
        self.router = LLMRouter(
            settings=self.settings,
            backend=self.backend,
            rate_limiter=rate_limiter,
            on_notice=on_notice,
        )
        self.cache = PromptCache(capacity=256)

    def warmup(self) -> dict[str, bool]:
        """Warm connections for all enabled providers (`GET /models`)."""
        results: dict[str, bool] = {}
        for name, cfg in self.settings.providers.items():
            if cfg.enabled:
                results[name] = self.backend.warmup(name)
        return results

    def prewarm(self, mode: ModeName | None = None) -> None:
        """
        Called on hotkey press / push-to-talk press (§4.7):
        prewarms the top candidate provider for `mode` if idle > 4 minutes.
        """
        target_mode = mode or self.settings.modes.default
        candidates = self.router.get_candidates(target_mode)
        if candidates:
            self.conn_manager.prewarm_if_idle(candidates[0].provider, background=True)

    def process(
        self,
        text: str,
        mode: ModeName | None = None,
        *,
        extra_signals: FastPathSignals | None = None,
        local_only: bool = False,
    ) -> ProcessResult:
        """
        Run the complete pipeline (`mask -> rules -> fast-path / LLM router -> guards -> unmask`).
        Never raises an unhandled exception; falls back gracefully on any provider/network error.
        """
        active_mode: ModeName = mode or self.settings.modes.default
        t_start = time.perf_counter()

        if not text or not text.strip():
            return ProcessResult(
                output=text,
                mode=active_mode,
                path="fast",
                timings={"rules_s": 0.0, "llm_s": 0.0, "total_s": 0.0},
            )

        # 1. Check LRU cache first (§4.13)
        cached = self.cache.get(mode=active_mode, input_type=self.input_type, text=text)
        if cached is not None:
            total_s = time.perf_counter() - t_start
            return ProcessResult(
                output=cached,
                mode=active_mode,
                path="cache",
                provider="cache",
                model="lru",
                timings={"rules_s": 0.0, "llm_s": 0.0, "total_s": round(total_s, 4)},
            )

        # 2. Run deterministic rule pipeline + optional SymSpell typo_hook
        t_rules0 = time.perf_counter()
        pipe_res: PipelineResult = self.pipeline.run(
            text,
            input_type=self.input_type,
            sentence_punctuation=self.settings.modes.sentence_punctuation,
            typo_hook=self.typo_hook,
        )
        if extra_signals is not None:
            pipe_res.signals = pipe_res.signals.merge(extra_signals)
        rules_s = time.perf_counter() - t_rules0

        # 3. Check privacy exclusions (`local_only` from excluded_apps or `allow_cloud_text=False`)
        cloud_allowed = self.settings.privacy.allow_cloud_text and not local_only
        if not cloud_allowed:
            out_text = pipe_res.text
            notice = "Rules only (privacy / local-only mode)" if active_mode != "clean_only" else None
            if active_mode == "structured_json":
                out_text = render_mode3_output(
                    StructuredPrompt(goal=pipe_res.text),
                    wrap_json_fence=self.settings.modes.wrap_json_fence,
                )
            return ProcessResult(
                output=out_text,
                mode=active_mode,
                path="fallback" if active_mode != "clean_only" else "fast",
                provider="local",
                model="rules",
                timings={"rules_s": round(rules_s, 4), "llm_s": 0.0, "total_s": round(time.perf_counter() - t_start, 4)},
                notice=notice,
            )

        # 4. Dispatch by mode
        if active_mode == "clean_only":
            res = self._process_mode1(text, pipe_res, rules_s, t_start)
        elif active_mode == "clean_plus_improved":
            res = self._process_mode2(text, pipe_res, rules_s, t_start)
        else:
            res = self._process_mode3(text, pipe_res, rules_s, t_start)

        if res.path in ("fast", "llm"):
            self.cache.put(
                mode=active_mode,
                input_type=self.input_type,
                text=text,
                output=res.output,
                provider=res.provider,
                model=res.model,
            )
        return res

    def _process_mode1(
        self,
        raw_text: str,
        pipe_res: PipelineResult,
        rules_s: float,
        t_start: float,
    ) -> ProcessResult:
        # Evaluate Mode 1 fast path (§4.12)
        if pipe_res.signals.can_take_fast_path(
            enabled=self.settings.modes.fast_path,
            strictness=self.settings.modes.fast_path_strictness,
        ):
            total_s = time.perf_counter() - t_start
            return ProcessResult(
                output=pipe_res.text,
                mode="clean_only",
                path="fast",
                provider="-",
                model="-",
                timings={"rules_s": round(rules_s, 4), "llm_s": 0.0, "total_s": round(total_s, 4)},
            )

        t_llm0 = time.perf_counter()
        system_prompt = build_system_prompt("clean_only", self.input_type)

        # Long-input chunking (> 150 words, §4.13)
        if pipe_res.signals.word_count > CHUNK_TRIGGER_WORDS:
            chunks = split_into_chunks(pipe_res.masked_text)
            last_provider = "-"
            last_model = "-"

            def _run_chunk(chunk_masked: str) -> str:
                nonlocal last_provider, last_model
                chunk_spans_indices = [int(m) for m in PLACEHOLDER_RE.findall(chunk_masked)]
                user_msg = build_user_message(chunk_masked, self.input_type, pipe_res.vocab_hints)
                est_in_tok = max(16, len(chunk_masked) // 4)
                max_tok = int(1.3 * est_in_tok + 32)
                try:
                    llm_res = self.router.route(
                        "clean_only",
                        system_prompt,
                        user_msg,
                        max_tokens=max_tok,
                        stop=["</output>"],
                        early_abort_check=is_answering_prefix,
                        post_guard=lambda r: (
                            not is_answering_prefix(r.text)
                            and sorted(int(m) for m in PLACEHOLDER_RE.findall(r.text))
                            == sorted(chunk_spans_indices)
                        ),
                    )
                    last_provider = llm_res.provider
                    last_model = llm_res.model
                    return llm_res.text
                except LLMError:
                    return chunk_masked

            combined_masked = process_chunks_parallel(chunks, _run_chunk)
            if verify(combined_masked, pipe_res.spans):
                final_text = unmask(combined_masked, pipe_res.spans)
                llm_s = time.perf_counter() - t_llm0
                return ProcessResult(
                    output=final_text,
                    mode="clean_only",
                    path="llm" if last_provider != "-" else "fallback",
                    provider=last_provider,
                    model=last_model,
                    timings={
                        "rules_s": round(rules_s, 4),
                        "llm_s": round(llm_s, 4),
                        "total_s": round(time.perf_counter() - t_start, 4),
                    },
                )

        # Standard single-call Mode 1
        user_msg = build_user_message(pipe_res.masked_text, self.input_type, pipe_res.vocab_hints)
        est_in_tokens = max(16, len(pipe_res.masked_text) // 4)
        max_tokens = int(1.3 * est_in_tokens + 32)

        try:
            llm_res: LLMResult = self.router.route(
                "clean_only",
                system_prompt,
                user_msg,
                max_tokens=max_tokens,
                stop=["</output>"],
                early_abort_check=is_answering_prefix,
                post_guard=lambda r: check_clean_guard(
                    r.text,
                    pipe_res.masked_text,
                    pipe_res.spans,
                    check_line_structure=True,
                ),
            )
            unmasked_out = unmask(llm_res.text, pipe_res.spans)
            llm_s = time.perf_counter() - t_llm0
            return ProcessResult(
                output=unmasked_out,
                mode="clean_only",
                path="llm",
                provider=llm_res.provider,
                model=llm_res.model,
                timings={
                    "rules_s": round(rules_s, 4),
                    "llm_s": round(llm_s, 4),
                    "total_s": round(time.perf_counter() - t_start, 4),
                },
            )
        except LLMError as exc:
            logger.info("Mode 1 falling back to deterministic rule output: %s", exc)
            llm_s = time.perf_counter() - t_llm0
            return ProcessResult(
                output=pipe_res.text,
                mode="clean_only",
                path="fallback",
                provider="local",
                model="rules",
                timings={
                    "rules_s": round(rules_s, 4),
                    "llm_s": round(llm_s, 4),
                    "total_s": round(time.perf_counter() - t_start, 4),
                },
                notice="Rules only (offline / quota)",
            )

    def _process_mode2(
        self,
        raw_text: str,
        pipe_res: PipelineResult,
        rules_s: float,
        t_start: float,
    ) -> ProcessResult:
        t_llm0 = time.perf_counter()
        fast_clean_ok = pipe_res.signals.can_take_fast_path(
            enabled=self.settings.modes.fast_path,
            strictness=self.settings.modes.fast_path_strictness,
        )
        system_prompt = build_system_prompt(
            "clean_plus_improved",
            self.input_type,
            clean_already_done=fast_clean_ok,
        )
        user_msg = build_user_message(pipe_res.masked_text, self.input_type, pipe_res.vocab_hints)
        schema = CleanImproved.model_json_schema()

        def _validate_mode2(r: LLMResult) -> bool:
            try:
                data = extract_json_object(r.text)
                parsed = CleanImproved.model_validate(data)
            except Exception:
                return False
            enh_words = len(parsed.enhanced.split())
            if enh_words == 0 or enh_words > 150:
                return False
            if is_answering_prefix(parsed.enhanced):
                return False
            # Verify no unknown placeholders were hallucinated
            for m in PLACEHOLDER_RE.findall(parsed.enhanced):
                if int(m) >= len(pipe_res.spans):
                    return False
            if parsed.clean and not verify(parsed.clean, pipe_res.spans):
                return False
            return True

        try:
            llm_res = self.router.route(
                "clean_plus_improved",
                system_prompt,
                user_msg,
                json_schema=schema,
                max_tokens=320,
                post_guard=_validate_mode2,
            )
            parsed = CleanImproved.model_validate(extract_json_object(llm_res.text))
            clean_part = (
                pipe_res.text
                if (fast_clean_ok or not parsed.clean.strip())
                else unmask(parsed.clean, pipe_res.spans)
            )
            enhanced_part = unmask(parsed.enhanced, pipe_res.spans)
            rendered = render_mode2_output(clean_part, enhanced_part)
            llm_s = time.perf_counter() - t_llm0
            return ProcessResult(
                output=rendered,
                mode="clean_plus_improved",
                path="llm",
                provider=llm_res.provider,
                model=llm_res.model,
                timings={
                    "rules_s": round(rules_s, 4),
                    "llm_s": round(llm_s, 4),
                    "total_s": round(time.perf_counter() - t_start, 4),
                },
            )
        except LLMError as exc:
            logger.info("Mode 2 falling back to Mode 1 result: %s", exc)
            llm_s = time.perf_counter() - t_llm0
            return ProcessResult(
                output=pipe_res.text,
                mode="clean_plus_improved",
                path="fallback",
                provider="local",
                model="rules",
                timings={
                    "rules_s": round(rules_s, 4),
                    "llm_s": round(llm_s, 4),
                    "total_s": round(time.perf_counter() - t_start, 4),
                },
                notice="Enhanced prompt unavailable; pasted cleaned text",
            )

    def _process_mode3(
        self,
        raw_text: str,
        pipe_res: PipelineResult,
        rules_s: float,
        t_start: float,
    ) -> ProcessResult:
        t_llm0 = time.perf_counter()
        system_prompt = build_system_prompt("structured_json", self.input_type)
        schema = load_custom_schema(self.settings.schemas.structured_json)
        user_msg = build_user_message(pipe_res.masked_text, self.input_type, pipe_res.vocab_hints)

        def _parse_and_validate_mode3(raw_json_str: str) -> StructuredPrompt:
            data = extract_json_object(raw_json_str)
            # Recursively unmask all string values inside the JSON dict before validation
            unmasked_json_str = unmask(json.dumps(data, ensure_ascii=False), pipe_res.spans)
            unmasked_data = json.loads(unmasked_json_str)
            model_obj = StructuredPrompt.model_validate(unmasked_data)
            if not model_obj.goal or not model_obj.goal.strip():
                raise ValueError("StructuredPrompt 'goal' field must not be empty")
            return model_obj

        import json

        last_validation_err: str | None = None
        for attempt in range(2):
            cur_user_msg = (
                user_msg
                if attempt == 0
                else build_user_message(
                    pipe_res.masked_text,
                    self.input_type,
                    pipe_res.vocab_hints,
                    repair_error=last_validation_err,
                )
            )
            try:
                llm_res = self.router.route(
                    "structured_json",
                    system_prompt,
                    cur_user_msg,
                    json_schema=schema,
                    max_tokens=384,
                )
                validated = _parse_and_validate_mode3(llm_res.text)
                rendered = render_mode3_output(
                    validated,
                    wrap_json_fence=self.settings.modes.wrap_json_fence,
                )
                llm_s = time.perf_counter() - t_llm0
                return ProcessResult(
                    output=rendered,
                    mode="structured_json",
                    path="llm",
                    provider=llm_res.provider,
                    model=llm_res.model,
                    timings={
                        "rules_s": round(rules_s, 4),
                        "llm_s": round(llm_s, 4),
                        "total_s": round(time.perf_counter() - t_start, 4),
                    },
                )
            except (LLMError, ValidationError, ValueError) as exc:
                last_validation_err = str(exc)
                continue

        # Final fallback (§4.4, §4.10): `{"goal": <clean text>}`
        fallback_obj = StructuredPrompt(goal=pipe_res.text)
        rendered_fallback = render_mode3_output(
            fallback_obj,
            wrap_json_fence=self.settings.modes.wrap_json_fence,
        )
        llm_s = time.perf_counter() - t_llm0
        return ProcessResult(
            output=rendered_fallback,
            mode="structured_json",
            path="fallback",
            provider="local",
            model="rules",
            timings={
                "rules_s": round(rules_s, 4),
                "llm_s": round(llm_s, 4),
                "total_s": round(time.perf_counter() - t_start, 4),
            },
            notice="Structured JSON fallback (offline / validation error)",
        )