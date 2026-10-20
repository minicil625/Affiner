"""
Self-correction detection and deterministic resolution (Task 2.2).
- Resolves simple deterministic patterns like `"use sqlite wait actually postgres"` -> `"use PostgreSQL"`
  when X and Y are vocabulary terms or single clean tokens after a clear pivot cue.
- Flags `has_correction_cue = True` whenever an unresolved correction cue remains in the text
  so Mode 1 knows to route to the cloud LLM instead of taking the local fast path.
"""

from __future__ import annotations

import re
from typing import Final

from promptcore.vocab import Vocabulary

# Cues that signal a mid-sentence self-correction
CORRECTION_CUE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"no\s+wait|"
    r"wait\s+actually|"
    r"wait\s+no|"
    r"actually\s+wait|"
    r"scratch\s+that|"
    r"never\s+mind|"
    r"nevermind|"
    r"i\s+mean|"
    r"sorry\s+i\s+mean|"
    r"or\s+rather|"
    r"not\s+[A-Za-z0-9_⟦⟧.-]+\s+but\s+[A-Za-z0-9_⟦⟧.-]+"
    r")\b",
    re.IGNORECASE,
)

# Standalone "wait" or "actually" used as a pivot between phrases
PIVOT_WAIT_ACTUALLY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:,\s*|\s+)\b(?:wait|actually|sorry)\b(?:,\s*|\s+)",
    re.IGNORECASE,
)

# Deterministic "X [wait/actually/no] Y" pattern where X and Y are 1-2 word tokens
_DETERMINISTIC_XY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<x>[A-Za-z0-9_⟦⟧.#+-]+(?:\s+[A-Za-z0-9_⟦⟧.#+-]+)?)"
    r"\s*,?\s*"
    r"(?P<cue>no\s+wait\s+actually|wait\s+actually|actually\s+wait|no\s+wait|wait\s+no|scratch\s+that|i\s+mean|or\s+rather|wait|actually)"
    r"\s*,?\s*"
    r"(?P<y>[A-Za-z0-9_⟦⟧.#+-]+(?:\s+[A-Za-z0-9_⟦⟧.#+-]+)?)\b",
    re.IGNORECASE,
)

# Deterministic "not X but Y" pattern
_NOT_X_BUT_Y_RE: Final[re.Pattern[str]] = re.compile(
    r"\bnot\s+(?P<x>[A-Za-z0-9_⟦⟧.#+-]+)\s*,?\s*but\s+(?:rather\s+)?(?P<y>[A-Za-z0-9_⟦⟧.#+-]+)\b",
    re.IGNORECASE,
)

# Common verbs/prepositions that precede X in "use sqlite wait actually postgres"
_LEADING_VERBS: Final[frozenset[str]] = frozenset(
    {
        "use",
        "using",
        "with",
        "in",
        "on",
        "to",
        "from",
        "into",
        "install",
        "add",
        "deploy",
        "run",
        "choose",
        "pick",
        "prefer",
        "switch",
        "for",
        "via",
    }
)


def has_correction_cue(text: str) -> bool:
    """Return True if `text` contains a self-correction cue."""
    if not text:
        return False
    if CORRECTION_CUE_RE.search(text):
        return True
    # Check if "wait" or "actually" is used as a mid-sentence correction pivot
    # (as opposed to "wait for the response" or "actually works")
    for m in PIVOT_WAIT_ACTUALLY_RE.finditer(text):
        after = text[m.end() :].strip().lower()
        if after.startswith(("for ", "until ", "on ", "to ", "a ", "the ")):
            continue
        return True
    return False


def resolve_corrections(
    text: str,
    vocab: Vocabulary | None = None,
) -> tuple[str, bool]:
    """
    Attempt deterministic resolution of simple `"X, wait/actually, Y"` corrections
    when X and Y are vocabulary terms (or placeholders).
    Returns `(updated_text, has_remaining_correction_cue)`.
    """
    if not text:
        return text, False

    vocabulary = vocab or Vocabulary()
    out = text

    def _xy_replacer(match: re.Match[str]) -> str:
        raw_x = match.group("x").strip()
        raw_y = match.group("y").strip()

        # Strip a leading verb if the regex captured e.g. "use sqlite" as X
        x_parts = raw_x.split()
        leading_prefix = ""
        if len(x_parts) == 2 and x_parts[0].lower() in _LEADING_VERBS:
            leading_prefix = x_parts[0] + " "
            x_term = x_parts[1]
        else:
            x_term = raw_x

        # If Y captured a trailing word that isn't part of the vocab term, split it
        y_parts = raw_y.split()
        trailing_suffix = ""
        y_term = raw_y
        if len(y_parts) == 2 and not vocabulary.is_vocab_term(raw_y):
            if vocabulary.is_vocab_term(y_parts[0]):
                y_term = y_parts[0]
                trailing_suffix = " " + y_parts[1]

        x_is_vocab = vocabulary.is_vocab_term(x_term) or "⟦" in x_term
        y_is_vocab = vocabulary.is_vocab_term(y_term) or "⟦" in y_term

        if x_is_vocab and y_is_vocab:
            canon_y = vocabulary.get_canonical(y_term) or y_term
            return f"{leading_prefix}{canon_y}{trailing_suffix}"

        return match.group(0)

    out = _DETERMINISTIC_XY_RE.sub(_xy_replacer, out)

    def _not_x_but_y_replacer(match: re.Match[str]) -> str:
        x_term = match.group("x").strip()
        y_term = match.group("y").strip()
        if (vocabulary.is_vocab_term(x_term) or "⟦" in x_term) and (
            vocabulary.is_vocab_term(y_term) or "⟦" in y_term
        ):
            return vocabulary.get_canonical(y_term) or y_term
        return match.group(0)

    out = _NOT_X_BUT_Y_RE.sub(_not_x_but_y_replacer, out)

    remaining_cue = has_correction_cue(out)
    return out, remaining_cue