"""
Spoken syntax converter (Task 2.2):
- `app dot py` -> `app.py`
- `dash b` -> `-b`, `double dash force` -> `--force`
- `feature slash login` -> `feature/login`
- `user underscore id` -> `user_id`
- `camel case get user id` -> `getUserId`
- `pascal case user profile` -> `UserProfile`
- `snake case user id` -> `user_id`
- `kebab case my component` -> `my-component`
- Standalone CLI commands (`git checkout -b feature/login`) -> `` `git checkout -b feature/login` ``
"""

from __future__ import annotations

import re
from typing import Final

from promptcore.masking import KNOWN_EXTENSIONS

# 1. Explicit casing directives: "camel case X Y [Z]", "pascal case X Y", "snake case X Y", "kebab case X Y"
_CASE_DIRECTIVE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(camel|pascal|snake|kebab|screaming\s+snake)\s+case\s+([A-Za-z0-9]+(?:\s+[A-Za-z0-9]+){1,3})\b",
    re.IGNORECASE,
)

#Stopwords that shouldn't be absorbed into the tail of a casing directive
_CASE_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "from",
        "by",
        "and",
        "or",
        "but",
        "then",
        "please",
        "thanks",
        "is",
        "are",
        "was",
        "were",
    }
)

# 2. Spoken filenames: "app dot py" -> "app.py", "package dot json" -> "package.json"
_SPOKEN_FILE_RE: Final[re.Pattern[str]] = re.compile(
    rf"\b([A-Za-z0-9_-]+)\s+dot\s+({KNOWN_EXTENSIONS})\b",
    re.IGNORECASE,
)

# 3. Spoken flags: "double dash force" -> "--force", "double hyphen dry run" -> "--dry-run"
_DOUBLE_DASH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:double\s+dash|double\s+hyphen|dash\s+dash)\s+([A-Za-z0-9]+)(?:\s+(?:dash|hyphen)\s+([A-Za-z0-9]+))?\b",
    re.IGNORECASE,
)

# Single-letter or short CLI flag: "dash b" -> "-b", "dash rf" -> "-rf"
_SINGLE_DASH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:dash|hyphen)\s+([A-Za-z0-9]{1,3})\b",
    re.IGNORECASE,
)

# 4. Spoken slash paths: "feature slash login" -> "feature/login", "src slash main.py" -> "src/main.py"
_SPOKEN_SLASH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Za-z0-9_.-]+)(?:\s+slash\s+([A-Za-z0-9_.-]+))+",
    re.IGNORECASE,
)

# 5. Spoken underscore identifiers: "user underscore id" -> "user_id"
_SPOKEN_UNDERSCORE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Za-z0-9]+)(?:\s+underscore\s+([A-Za-z0-9]+))+",
    re.IGNORECASE,
)

# 6. Standalone CLI command detection (e.g., "git checkout -b feature/login")
_CLI_COMMAND_FULL_RE: Final[re.Pattern[str]] = re.compile(
    r"^((?:git|docker|kubectl|npm|pnpm|yarn|bun|uv|pip|pytest|ruff|cargo|go|python|node|npx|curl|ssh|chmod|chown|mkdir|rm|cp|mv)\s+"
    r"(?:checkout|commit|push|pull|merge|rebase|branch|status|log|diff|clone|init|add|reset|stash|tag|fetch|"
    r"run|build|compose|exec|ps|images|logs|apply|get|describe|delete|install|add|remove|test|sync|lock|venv|-[A-Za-z0-9-]+)"
    r"(?:\s+[^\s`]+)*)$"
)


def _format_cased_words(style: str, words: list[str]) -> str:
    low_words = [w.lower() for w in words]
    norm_style = re.sub(r"\s+", "_", style.lower())
    if norm_style == "camel":
        return low_words[0] + "".join(w.capitalize() for w in low_words[1:])
    if norm_style == "pascal":
        return "".join(w.capitalize() for w in low_words)
    if norm_style == "snake":
        return "_".join(low_words)
    if norm_style == "kebab":
        return "-".join(low_words)
    if norm_style == "screaming_snake":
        return "_".join(w.upper() for w in low_words)
    return " ".join(words)


def convert_spoken_syntax(text: str) -> str:
    """Convert spoken developer syntax into literal code/path/CLI tokens."""
    if not text:
        return text

    out = text

    # 1. Casing directives ("camel case get user id")
    def _case_replacer(match: re.Match[str]) -> str:
        style = match.group(1)
        raw_words = match.group(2).split()
        kept: list[str] = []
        trailing: list[str] = []
        for idx, w in enumerate(raw_words):
            if idx >= 2 and w.lower() in _CASE_STOPWORDS:
                trailing = raw_words[idx:]
                break
            kept.append(w)
        if len(kept) < 2:
            return match.group(0)
        formatted = _format_cased_words(style, kept)
        if trailing:
            return f"{formatted} {' '.join(trailing)}"
        return formatted

    out = _CASE_DIRECTIVE_RE.sub(_case_replacer, out)

    # 2. Spoken filenames ("app dot py" -> "app.py")
    out = _SPOKEN_FILE_RE.sub(lambda m: f"{m.group(1)}.{m.group(2).lower()}", out)

    # 3. Spoken CLI flags ("double dash force" -> "--force", "dash b" -> "-b")
    def _double_dash_repl(match: re.Match[str]) -> str:
        p1 = match.group(1).lower()
        p2 = match.group(2)
        if p2:
            return f"--{p1}-{p2.lower()}"
        return f"--{p1}"

    out = _DOUBLE_DASH_RE.sub(_double_dash_repl, out)
    out = _SINGLE_DASH_RE.sub(lambda m: f"-{m.group(1)}", out)

    # 4. Spoken slash paths ("feature slash login" -> "feature/login")
    def _slash_repl(match: re.Match[str]) -> str:
        full = match.group(0)
        parts = re.split(r"\s+slash\s+", full, flags=re.IGNORECASE)
        return "/".join(parts)

    out = _SPOKEN_SLASH_RE.sub(_slash_repl, out)

    # 5. Spoken underscores ("user underscore id" -> "user_id")
    def _underscore_repl(match: re.Match[str]) -> str:
        full = match.group(0)
        parts = re.split(r"\s+underscore\s+", full, flags=re.IGNORECASE)
        return "_".join(parts)

    out = _SPOKEN_UNDERSCORE_RE.sub(_underscore_repl, out)

    # 6. Wrap standalone CLI command in backticks (`git checkout -b feature/login`)
    stripped = out.strip()
    if not stripped.startswith("`") and _CLI_COMMAND_FULL_RE.match(stripped):
        out = f"`{stripped}`"

    return out