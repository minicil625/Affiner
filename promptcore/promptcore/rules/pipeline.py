"""
Ordered deterministic rule runner (Task 2.2).
Order:
  1. Initial mask of pre-existing code blocks, backticks, URLs, paths, flags, identifiers
  2. Shorthand expansion (`typed` input only)
  3. Filler removal & duplicate word collapsing
  4. Spoken syntax conversion (`app dot py` -> `app.py`, `dash b` -> `-b`, etc.) + incremental mask
  5. Optional mid-pipeline hook (used by `textprompt/typo.py` SymSpell on masked tokens)
  6. Self-correction resolution & cue detection
  7. Vocabulary canonical casing, capitalization & sentence punctuation
  8. Grammar risk estimation & `FastPathSignals` assembly
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from promptcore.masking import PLACEHOLDER_RE, mask, unmask, verify
from promptcore.rules.capitalization import (
    PunctuationMode,
    apply_capitalization_and_punctuation,
)
from promptcore.rules.corrections import resolve_corrections
from promptcore.rules.fillers import strip_fillers
from promptcore.rules.shorthand import expand_shorthand, load_shorthand_file
from promptcore.rules.spoken_syntax import convert_spoken_syntax
from promptcore.signals import FastPathSignals
from promptcore.vocab import Vocabulary

InputType = Literal["typed", "transcript"]
MidPipelineHook = Callable[[str, list[str]], tuple[str, FastPathSignals]]

# Heuristic grammar-risk patterns that benefit from an LLM rewrite in "balanced" fast-path mode
_GRAMMAR_RISK_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"he\s+don't|she\s+don't|it\s+don't|"
    r"they\s+was|we\s+was|you\s+was|"
    r"i\s+is|i\s+are|i\s+has|he\s+have|she\s+have|it\s+have|"
    r"didn't\s+(?:went|saw|came|took|made|did|ran|broke|worked)|"
    r"more\s+better|more\s+faster|most\s+fastest|"
    r"can\s+able\s+to|please\s+to\s+fix|how\s+for\s+to"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class PipelineResult:
    """Output of the deterministic rule pipeline."""

    text: str                    # Fully unmasked, rule-cleaned text
    masked_text: str             # Text with `⟦n⟧` placeholders intact (for LLM input if needed)
    spans: list[str]             # Protected spans corresponding to `⟦0⟧ .. ⟦n-1⟧`
    signals: FastPathSignals     # Signals for Mode 1 fast-path decision
    vocab_hints: list[str]       # Matched canonical vocabulary terms for `<vocabulary>` tag


class RulePipeline:
    """Deterministic text cleaning pipeline shared by TextPrompt AI and VoicePrompt AI."""

    def __init__(
        self,
        vocab: Vocabulary | None = None,
        shorthand_map: dict[str, str] | None = None,
        input_type: InputType = "typed",
        sentence_punctuation: PunctuationMode = "auto",
    ) -> None:
        self.vocab = vocab or Vocabulary.from_file()
        self.shorthand_map = shorthand_map or load_shorthand_file()
        self.input_type: InputType = input_type
        self.sentence_punctuation: PunctuationMode = sentence_punctuation

    @classmethod
    def from_paths(
        cls,
        vocab_path: Path | str | None = None,
        shorthand_path: Path | str | None = None,
        input_type: InputType = "typed",
        sentence_punctuation: PunctuationMode = "auto",
    ) -> RulePipeline:
        vocab = Vocabulary.from_file(vocab_path)
        shorthand_map = load_shorthand_file(shorthand_path)
        return cls(
            vocab=vocab,
            shorthand_map=shorthand_map,
            input_type=input_type,
            sentence_punctuation=sentence_punctuation,
        )

    def run(
        self,
        raw_text: str,
        *,
        input_type: InputType | None = None,
        sentence_punctuation: PunctuationMode | None = None,
        typo_hook: MidPipelineHook | None = None,
    ) -> PipelineResult:
        """
        Execute the ordered deterministic cleaning pipeline on `raw_text`.
        """
        effective_input_type = input_type or self.input_type
        effective_punct = sentence_punctuation or self.sentence_punctuation

        if not raw_text or not raw_text.strip():
            empty_signals = FastPathSignals(word_count=0)
            return PipelineResult(
                text=raw_text,
                masked_text=raw_text,
                spans=[],
                signals=empty_signals,
                vocab_hints=[],
            )

        # Vocabulary terms whose lowercase forms shouldn't be masked as opaque identifiers
        # before capitalization can apply their canonical casing (e.g. "nextjs", "fastapi")
        vocab_aliases = set(self.vocab.canonical_map.keys())

        # Step 1: Initial mask of pre-existing code blocks, inline code, URLs, paths, identifiers
        mask_state = mask(raw_text, skip_identifiers=vocab_aliases)
        working = mask_state.text
        spans = mask_state.spans

        # Step 2: Fillers removal (runs before shorthand so "um pls" becomes "pls" cleanly)
        working = strip_fillers(working)

        # Step 3: Spoken syntax conversion ("app dot py" -> "app.py", "dash b" -> "-b", etc.)
        working = convert_spoken_syntax(working)

        # Incrementally mask any newly synthesized filenames, flags, paths, or backticked commands
        mask_state = mask(working, existing_spans=spans, skip_identifiers=vocab_aliases)
        working = mask_state.text
        spans = mask_state.spans

        # Step 4: Optional typo/language-guard hook (SymSpell in TextPrompt)
        extra_signals: FastPathSignals | None = None
        if typo_hook is not None:
            working, extra_signals = typo_hook(working, spans)

        # Step 5: Shorthand expansion (`typed` only, and skipped if language guard flagged non-English)
        is_non_english = bool(extra_signals and extra_signals.non_english)
        if effective_input_type == "typed" and not is_non_english:
            working = expand_shorthand(working, self.shorthand_map)

        # Step 6: Self-correction detection and deterministic resolution
        working, has_remaining_cue = resolve_corrections(working, self.vocab)

        # Step 7: Capitalization, vocabulary canonical casing, and sentence punctuation
        # Check if the entire string is a single placeholder wrapping a backticked command
        single_placeholder_match = re.fullmatch(r"\s*⟦(\d+)⟧\s*", working)
        if single_placeholder_match:
            idx = int(single_placeholder_match.group(1))
            if 0 <= idx < len(spans) and spans[idx].startswith("`") and spans[idx].endswith("`"):
                pass
            else:
                working = apply_capitalization_and_punctuation(
                    working,
                    vocab=self.vocab,
                    sentence_punctuation=effective_punct,
                )
        else:
            working = apply_capitalization_and_punctuation(
                working,
                vocab=self.vocab,
                sentence_punctuation=effective_punct,
            )

        # Verify placeholder survival and unmask
        if not verify(working, spans):
            # Fallback safety: never drop protected spans
            unmasked_final = unmask(working, spans)
        else:
            unmasked_final = unmask(working, spans)

        # Compute word count (counting each placeholder as 1 word)
        words = re.findall(r"\S+", working)
        word_count = len(words)

        # Check heuristic grammar risk
        has_grammar_risk = bool(_GRAMMAR_RISK_RE.search(unmasked_final))

        base_signals = FastPathSignals(
            word_count=word_count,
            has_correction_cue=has_remaining_cue,
            has_grammar_risk=has_grammar_risk,
        )
        merged_signals = base_signals.merge(extra_signals)

        # Extract vocabulary hints from the unmasked text (excluding code fences)
        text_without_fences = PLACEHOLDER_RE.sub(" ", working) + " " + unmasked_final
        vocab_hints = self.vocab.hints(text_without_fences)

        return PipelineResult(
            text=unmasked_final,
            masked_text=working,
            spans=spans,
            signals=merged_signals,
            vocab_hints=vocab_hints,
        )