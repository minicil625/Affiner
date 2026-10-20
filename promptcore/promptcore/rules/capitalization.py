"""
Deterministic capitalization and sentence punctuation (Task 2.2):
- Applies canonical vocabulary casing (`fastapi` -> `FastAPI`, `postgres` -> `PostgreSQL`, `nextjs` -> `Next.js`)
- Uppercases standalone pronoun `i` -> `I` (and `i'm` -> `I'm`, `i've` -> `I've`, `i'll` -> `I'll`, `i'd` -> `I'd`)
- Applies sentence-initial capitalization (unless starting with a lowercase technical identifier like `npm` or `git` in a code-like command)
- Applies final sentence punctuation according to `sentence_punctuation: auto | always | never`
"""

from __future__ import annotations

import re
from typing import Final, Literal

from promptcore.vocab import Vocabulary

PunctuationMode = Literal["auto", "always", "never"]

_STANDALONE_I_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_/.@-])\bi(?=('m|'ve|'ll|'d)\b|\b(?![A-Za-z0-9_/.@-]))"
)

_QUESTION_STARTERS: Final[frozenset[str]] = frozenset(
    {
        "how",
        "what",
        "why",
        "when",
        "where",
        "who",
        "which",
        "can",
        "could",
        "would",
        "should",
        "is",
        "are",
        "do",
        "does",
        "did",
    }
)


def apply_vocabulary_casing(text: str, vocab: Vocabulary) -> str:
    """Replace vocabulary terms/aliases with their canonical casing outside placeholders."""
    if not text:
        return text

    # Match tokens (including dots/slashes/hashes like next.js, ci/cd, c#) that are not part of paths/URLs
    token_re = re.compile(
        r"(?<![A-Za-z0-9_/@\\-])([A-Za-z0-9]+(?:\.[A-Za-z0-9]+|/[A-Za-z0-9]+|#)?)(?![A-Za-z0-9_/@\\-])"
    )

    # First handle multi-word aliases like "rest api" -> "REST API" or "tailwind css" -> "Tailwind CSS"
    out = text
    for alias, canon in vocab.canonical_map.items():
        if " " in alias:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_/@\\-])\b{re.escape(alias)}\b(?![A-Za-z0-9_/@\\-])",
                re.IGNORECASE,
            )
            out = pattern.sub(canon, out)

    def _case_token(match: re.Match[str]) -> str:
        tok = match.group(1)
        if "⟦" in tok or "⟧" in tok:
            return tok
        low = tok.lower()
        if low in vocab.canonical_map:
            return vocab.canonical_map[low]
        return tok

    return token_re.sub(_case_token, out)


def apply_capitalization_and_punctuation(
    text: str,
    vocab: Vocabulary | None = None,
    sentence_punctuation: PunctuationMode = "auto",
) -> str:
    """
    Apply vocabulary casing, standalone `I`, sentence-start capitalization,
    and final punctuation (`auto` | `always` | `never`).
    """
    if not text or not text.strip():
        return text

    vocabulary = vocab or Vocabulary()
    out = text.strip()

    # If the entire input is a single backticked span or placeholder for a backticked command, leave untouched
    if out.startswith("`") and out.endswith("`") and out.count("`") == 2:
        return out

    # 1. Apply vocabulary canonical casing
    out = apply_vocabulary_casing(out, vocabulary)

    # 2. Standalone pronoun "i" -> "I"
    out = _STANDALONE_I_RE.sub("I", out)

    # 3. Capitalize the start of sentences (first letter of text and after `. `, `! `, `? `)
    def _cap_first_word(s: str) -> str:
        m = re.match(r"^(\s*)([A-Za-z][A-Za-z0-9._-]*)", s)
        if not m:
            return s
        leading, first_word = m.group(1), m.group(2)
        # Preserve canonical vocabulary casing if the first word is a lowercase-initial tech term (e.g. "pywin32", "httpx", "pgvector", "tRPC", "gRPC")
        canon = vocabulary.get_canonical(first_word)
        if canon and canon[0].islower():
            return leading + canon + s[m.end() :]
        if first_word[0].islower() and not any(c.isupper() for c in first_word[1:]):
            return leading + first_word[0].upper() + first_word[1:] + s[m.end() :]
        return s

    out = _cap_first_word(out)

    # Also capitalize after sentence-ending punctuation
    out = re.sub(
        r"([.!?]\s+)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
    )

    # 4. Final punctuation handling
    if sentence_punctuation == "never":
        return out

    # Check if already ends with terminal punctuation or closing fence/backtick
    if out[-1] in ".!?:;`" or out.endswith("```"):
        return out

    words = out.split()
    if sentence_punctuation == "auto":
        # Skip single words, pure placeholders, or multi-line bullet lists
        if len(words) <= 1:
            return out
        if "\n" in out and out.lstrip().startswith(("-", "*", "1.")):
            return out

    # Determine whether `.` or `?` is appropriate
    first_alpha = re.sub(r"[^A-Za-z]", "", words[0]).lower() if words else ""
    if first_alpha in _QUESTION_STARTERS and len(words) >= 3 and "please" not in out.lower():
        # Direct question like "how do I connect to PostgreSQL"
        if first_alpha in {"how", "what", "why", "when", "where", "who", "which", "is", "are", "does", "did"}:
            return out + "?"

    return out + "."