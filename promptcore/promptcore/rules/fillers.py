"""
Filler removal and duplicate word collapsing (Task 2.2).
Always strips vocalized pauses (`um`, `uh`, `er`, `hmm`).
Strips discourse fillers (`like`, `basically`, `you know`, `I mean`, `sort of`, `kind of`)
only when they appear in filler positions (so "looks like a bug" is preserved).
"""

from __future__ import annotations

import re
from typing import Final

# Unconditional hesitation fillers
_HESITATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?<=^)|(?<=[\s,]))\b(?:um+|uh+|er+|erm+|hmm+|uhm+)\b\s*,?\s*",
    re.IGNORECASE,
)

# Discourse fillers at sentence/clause start or delimited by commas
_CLAUSE_START_FILLER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|(?<=[.!?]\s))\s*(?:basically|you know|like|literally)\s*,?\s+",
    re.IGNORECASE,
)

# Comma-bracketed mid-sentence fillers: ", like, ", ", you know, ", ", basically, "
_COMMA_FILLER_RE: Final[re.Pattern[str]] = re.compile(
    r",\s*(?:like|you know|basically|literally|sort of|kind of)\s*,\s*",
    re.IGNORECASE,
)

# Mid-sentence "um/uh like" or filler after auxiliary/pronoun when not followed by a noun determiner
# Note: preserves "looks like", "feels like", "seems like", "is like a", "like this/that"
_SAFE_MID_FILLERS_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:you know|basically)\b\s*,?\s*",
    re.IGNORECASE,
)

# Immediate repeated single words: "the the", "in in", "we we" (case-insensitive)
# Excludes valid English reduplications like "that that", "had had" when intentional?
# In prompt cleaning, "the the", "a a", "to to", "is is", "in in", "we we", "I I" are always stutters.
_STUTTER_WORDS_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(the|a|an|to|in|on|at|for|with|from|by|of|is|are|was|were|be|we|i|you|they|it|can|should|will|would|could|please)\s+\1\b",
    re.IGNORECASE,
)


def strip_fillers(text: str) -> str:
    """Remove hesitation sounds, discourse fillers in filler positions, and word stutters."""
    if not text:
        return text

    out = text

    # 1. Strip unconditional vocal pauses (um, uh, er, hmm)
    out = _HESITATION_RE.sub(" ", out)

    # 2. Strip comma-delimited fillers (", like, " -> " ")
    out = _COMMA_FILLER_RE.sub(" ", out)

    # 3. Strip clause-start discourse fillers (" basically, we need..." -> "we need...")
    out = _CLAUSE_START_FILLER_RE.sub("", out)

    # 4. Strip mid-sentence "basically" / "you know"
    out = _SAFE_MID_FILLERS_RE.sub("", out)

    # 5. Collapse stuttered function words ("the the" -> "the")
    out = _STUTTER_WORDS_RE.sub(r"\1", out)

    # 6. Clean up stray spaces and orphaned leading commas
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r"^,\s*", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()