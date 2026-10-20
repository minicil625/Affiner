"""
Fast-path signal contract (§4.12) shared between TextPrompt AI and VoicePrompt AI.
Determines when Mode 1 (Clean) can skip the cloud LLM API entirely and return
the deterministic rule output in < 10 ms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StrictnessMode = Literal["balanced", "aggressive", "off"]


@dataclass
class FastPathSignals:
    """
    Aggregated signals produced by the deterministic rule pipeline and app-specific
    analyzers (SymSpell in TextPrompt, STT confidence in VoicePrompt).
    """

    word_count: int
    has_correction_cue: bool = False   # unresolved spoken/typed self-correction cue
    has_grammar_risk: bool = False     # heuristic flag for broken syntax/tense needing LLM
    non_english: bool = False          # TextPrompt language guard (>40% unknown English tokens)
    unresolved_typos: int = 0          # TextPrompt SymSpell unknown tokens with no high-conf fix
    low_confidence_words: int = 0      # VoicePrompt STT low-confidence word count

    def merge(self, other: FastPathSignals | None) -> FastPathSignals:
        """Merge app-specific signals (e.g. from SymSpell or STT) into pipeline signals."""
        if other is None:
            return self
        return FastPathSignals(
            word_count=max(self.word_count, other.word_count),
            has_correction_cue=self.has_correction_cue or other.has_correction_cue,
            has_grammar_risk=self.has_grammar_risk or other.has_grammar_risk,
            non_english=self.non_english or other.non_english,
            unresolved_typos=self.unresolved_typos + other.unresolved_typos,
            low_confidence_words=self.low_confidence_words + other.low_confidence_words,
        )

    def can_take_fast_path(
        self,
        enabled: bool = True,
        strictness: StrictnessMode = "balanced",
    ) -> bool:
        """
        Evaluate whether Mode 1 can skip the cloud LLM call (§4.12):
        - If fast_path is disabled or strictness == "off", always return False.
        - Inputs of <= 3 words always take the fast path (unless non_english or unresolved_typos > 0).
        - In "balanced" mode: every boolean flag must be False and every counter 0.
        - In "aggressive" mode: ignores `has_grammar_risk`.
        """
        if not enabled or strictness == "off":
            return False

        if self.word_count <= 3 and not self.non_english and self.unresolved_typos == 0 and self.low_confidence_words == 0 and not self.has_correction_cue:
            return True

        if (
            self.has_correction_cue
            or self.non_english
            or self.unresolved_typos > 0
            or self.low_confidence_words > 0
        ):
            return False

        if strictness == "balanced" and self.has_grammar_risk:
            return False

        return True