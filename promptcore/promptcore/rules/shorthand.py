"""
Whole-word, case-preserving shorthand expansion for typed inputs (Task 2.2).
Only runs when `input_type == "typed"`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

# Default built-in shorthand dictionary (supplemented by shorthand.txt when provided)
DEFAULT_SHORTHAND: Final[dict[str, str]] = {
    "pls": "please",
    "plz": "please",
    "thx": "thanks",
    "ty": "thank you",
    "tysm": "thank you so much",
    "u": "you",
    "ur": "your",
    "r": "are",
    "bc": "because",
    "cuz": "because",
    "b/c": "because",
    "w/": "with",
    "w/o": "without",
    "b4": "before",
    "rn": "right now",
    "idk": "I don't know",
    "imo": "in my opinion",
    "imho": "in my humble opinion",
    "tbh": "to be honest",
    "fyi": "for your information",
    "asap": "as soon as possible",
    "btw": "by the way",
    "afaik": "as far as I know",
    "iirc": "if I recall correctly",
    "tldr": "TL;DR",
    "tl;dr": "TL;DR",
    "msg": "message",
    "msgs": "messages",
    "repo": "repository",
    "repos": "repositories",
    "auth": "authentication",
    "db": "database",
    "env": "environment",
    "envs": "environments",
    "param": "parameter",
    "params": "parameters",
    "fn": "function",
    "funcs": "functions",
    "dep": "dependency",
    "deps": "dependencies",
    "pkg": "package",
    "pkgs": "packages",
    "dir": "directory",
    "dirs": "directories",
    "approx": "approximately",
    "esp": "especially",
    "wanna": "want to",
    "gonna": "going to",
    "gotta": "got to",
    "kinda": "kind of",
    "sorta": "sort of",
    "dont": "don't",
    "cant": "can't",
    "wont": "won't",
    "shouldnt": "shouldn't",
    "couldnt": "couldn't",
    "wouldnt": "wouldn't",
    "doesnt": "doesn't",
    "didnt": "didn't",
    "isnt": "isn't",
    "arent": "aren't",
    "wasnt": "wasn't",
    "werent": "weren't",
    "hasnt": "hasn't",
    "havent": "haven't",
    "hadnt": "hadn't",
    "im": "I'm",
    "ive": "I've",
    "youre": "you're",
    "youve": "you've",
    "theyre": "they're",
    "weve": "we've",
    "lets": "let's",
    "thats": "that's",
    "whats": "what's",
    "heres": "here's",
    "theres": "there's",
}

# Phrasal verb compound fixes at the start of an imperative or after "please"/"to"/"let's"
_SETUP_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?<=^)|(?<=\bplease\s)|(?<=\bto\s)|(?<=\blet's\s))\b(setup)\b(?=\s+(?:a|an|the|[A-Za-z0-9_⟦]))",
    re.IGNORECASE,
)

# Trailing courtesy "thanks" or "thank you" after a clause of >= 3 words without preceding comma
_TRAILING_THANKS_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\S+(?:\s+\S+){2,}?)\s+(thanks|thank you)([.!?]*)$",
    re.IGNORECASE,
)


def load_shorthand_file(path: Path | str | None = None) -> dict[str, str]:
    mapping = dict(DEFAULT_SHORTHAND)
    if path is None:
        return mapping
    file_path = Path(path)
    if not file_path.exists():
        return mapping
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        k, v = line.split("=>", 1)
        k_clean = k.strip().lower()
        v_clean = v.strip()
        if k_clean and v_clean:
            mapping[k_clean] = v_clean
    return mapping


def _apply_case(source: str, replacement: str) -> str:
    """Preserve initial capitalization or ALL-CAPS if source had it."""
    if not source or not replacement:
        return replacement
    if replacement.startswith(("I'", "I ", "TL;DR")):
        return replacement
    if source.isupper() and len(source) > 1:
        return replacement.upper()
    if source[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def expand_shorthand(
    text: str,
    mapping: dict[str, str] | None = None,
) -> str:
    """
    Expand whole-word shorthand tokens in `text`, skipping any `⟦n⟧` placeholders.
    Also normalizes imperative `setup <noun>` -> `set up <noun>` and adds a comma
    before a trailing `thanks` / `thank you` at the end of a request sentence.
    """
    if not text:
        return text

    shorthand_map = mapping or DEFAULT_SHORTHAND

    # 1. Handle slash-based abbreviations first (`w/o`, `w/`, `b/c`, `tl;dr`)
    out = text
    for special in ("w/o", "w/", "b/c", "tl;dr"):
        if special in shorthand_map:
            pattern = re.compile(
                rf"(?:(?<=^)|(?<=\s))({re.escape(special)})(?=\s|$|[.,;!?])",
                re.IGNORECASE,
            )
            out = pattern.sub(
                lambda m, rep=shorthand_map[special]: _apply_case(m.group(1), rep),
                out,
            )

    # 2. Whole-word token expansion (ignoring tokens adjacent to `⟦`, `⟧`, `/`, `.`, `-`, `_`)
    word_pattern = re.compile(r"(?<![A-Za-z0-9_/.@-])\b([A-Za-z0-9']+)\b(?![A-Za-z0-9_/.@-])")

    def _replace_word(match: re.Match[str]) -> str:
        word = match.group(1)
        low = word.lower()
        if low in shorthand_map:
            return _apply_case(word, shorthand_map[low])
        return word

    out = word_pattern.sub(_replace_word, out)

    # 3. Imperative "setup <target>" -> "set up <target>"
    out = _SETUP_VERB_RE.sub(
        lambda m: "Set up" if m.group(1)[0].isupper() else "set up",
        out,
    )

    # 4. Add comma before trailing "thanks" / "thank you" on multi-word requests
    m_thanks = _TRAILING_THANKS_RE.match(out.strip())
    if m_thanks:
        prefix, thanks_word, punct = m_thanks.groups()
        if not prefix.endswith((",", ".", "!", "?", ";", ":")):
            out = f"{prefix}, {thanks_word.lower()}{punct}"

    return out