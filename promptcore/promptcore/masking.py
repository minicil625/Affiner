"""
Protected-span masking (Task 2.1).
Replaces technical tokens (code fences, inline code, URLs, emails, paths,
filenames, CLI flags, versions, @mentions, #tags, and code identifiers)
with unicode bracket placeholders `⟦n⟧` so neither deterministic rules,
SymSpell, nor cloud LLMs can corrupt them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

OPEN_BRACKET: Final[str] = "⟦"
CLOSE_BRACKET: Final[str] = "⟧"

PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"⟦(\d+)⟧")

# Slash combinations that are natural-language shorthand, NOT file paths
SLASH_EXCLUSIONS: Final[frozenset[str]] = frozenset(
    {
        "w/",
        "w/o",
        "b/c",
        "c/o",
        "n/a",
        "a/b",
        "i/o",
        "ci/cd",
        "and/or",
        "true/false",
        "yes/no",
        "on/off",
        "he/she",
        "s/he",
        "they/them",
    }
)

KNOWN_EXTENSIONS: Final[str] = (
    r"py|pyi|pyc|js|mjs|cjs|ts|tsx|jsx|json|jsonl|yaml|yml|toml|md|mdx|rst|txt|"
    r"rs|go|java|kt|kts|swift|c|cpp|cc|cxx|h|hpp|cs|fs|rb|php|pl|lua|r|jl|dart|"
    r"html|htm|css|scss|sass|less|vue|svelte|astro|sql|sqlite|db|graphql|gql|proto|"
    r"sh|bash|zsh|fish|ps1|psm1|bat|cmd|exe|msi|dll|so|dylib|bin|app|deb|rpm|"
    r"env|lock|ini|cfg|conf|config|xml|plist|csv|tsv|parquet|feather|arrow|"
    r"pdf|doc|docx|xls|xlsx|ppt|pptx|png|jpg|jpeg|gif|webp|svg|ico|mp3|wav|mp4|"
    r"whl|tar|gz|tgz|bz2|xz|zip|7z|rar|dockerignore|gitignore|editorconfig"
)

# Priority-ordered detectors (Task 2.1)
_DETECTORS: Final[list[tuple[str, re.Pattern[str]]]] = [
    # 1. Fenced code blocks (```...``` or ~~~...~~~)
    ("code_fence", re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")),
    # 2. Inline backtick code (`...`)
    ("inline_code", re.compile(r"`[^`\r\n]+`")),
    # 3. URLs (http/https/ftp/ws/wss)
    (
        "url",
        re.compile(
            r"\b(?:https?|ftp|wss?)://[^\s<>\"\'\)\]]+(?<![.,;:!?])",
            re.IGNORECASE,
        ),
    ),
    # 4. Email addresses
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    # 5. Windows paths (e.g., C:\Users\foo\bar.py or ..\scripts\setup.ps1)
    (
        "win_path",
        re.compile(
            r"(?:[A-Za-z]:\\|(?:\.{1,2}\\))[A-Za-z0-9._\\-]+"
        ),
    ),
    # 6. Unix / relative file paths (e.g., /etc/hosts, ./src/main.py, src/components/Button.tsx)
    (
        "unix_path",
        re.compile(
            r"(?:(?<=^)|(?<=[\s(\[\"'=]))(?:\.{1,2}/|/[A-Za-z0-9._-]+/|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./-]+"
        ),
    ),
    # 7. Filenames with known extensions (e.g., app.py, config.yaml, package.json)
    (
        "filename",
        re.compile(
            rf"(?:(?<=^)|(?<=[\s(\[\"'=]))\.?[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:{KNOWN_EXTENSIONS})\b",
            re.IGNORECASE,
        ),
    ),
    # 8. CLI flags (e.g., --force, --max-tokens=100, -b, -rf)
    (
        "cli_flag",
        re.compile(
            r"(?:(?<=^)|(?<=[\s(\[]))(?:--[A-Za-z0-9][A-Za-z0-9_-]*(?:=[^\s,\)]+)?|-[A-Za-z0-9]{1,3})\b"
        ),
    ),
    # 9. Semantic versions & hex literals (e.g., v1.2.3, 3.11.4-rc1, 0xDEADBEEF)
    (
        "version",
        re.compile(
            r"\b(?:v\d+(?:\.\d+)+(?:-[A-Za-z0-9.-]+)?|\d+\.\d+(?:\.\d+)+(?:-[A-Za-z0-9.-]+)?|0x[0-9A-Fa-f]+)\b"
        ),
    ),
    # 10. @mentions and #tags (e.g., @username, #issue-123)
    (
        "mention_tag",
        re.compile(r"(?:(?<=^)|(?<=[\s(\[]))(?:@[A-Za-z0-9_.-]+|#[A-Za-z0-9_-]+)\b"),
    ),
    # 11. Function calls with parens (e.g., get_user(), FastAPI())
    (
        "func_call",
        re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*\([^\r\n()`]*\)"),
    ),
    # 12. Technical identifiers: snake_case, camelCase, PascalCase, SCREAMING_SNAKE_CASE
    (
        "identifier",
        re.compile(
            r"\b(?:"
            r"_[A-Za-z0-9_]+|"                                  # _private_var or __init__
            r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+|"                  # snake_case / SCREAMING_SNAKE
            r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}|"               # multi-segment kebab-case (my-feature-branch)
            r"[a-z]+[0-9]*[A-Z][A-Za-z0-9]*|"                   # camelCase (getUserId, useState)
            r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*"                  # PascalCase (GetUserById, PyInstaller)
            r")\b"
        ),
    ),
]


@dataclass
class MaskState:
    """Holds masked text and ordered original spans corresponding to `⟦0⟧`, `⟦1⟧`, ..."""

    text: str
    spans: list[str] = field(default_factory=list)

    def add_span(self, original: str) -> str:
        """Register a new protected span and return its placeholder `⟦n⟧`."""
        idx = len(self.spans)
        self.spans.append(original)
        return f"{OPEN_BRACKET}{idx}{CLOSE_BRACKET}"


def _contains_placeholder(segment: str) -> bool:
    return OPEN_BRACKET in segment or CLOSE_BRACKET in segment


def mask(
    text: str,
    existing_spans: list[str] | None = None,
    skip_identifiers: set[str] | None = None,
) -> MaskState:
    """
    Detect protected spans in priority order and replace them with `⟦n⟧`.
    Supports incremental masking when `existing_spans` is provided.
    """
    state = MaskState(text=text, spans=list(existing_spans) if existing_spans else [])
    if not text:
        return state

    skip_lower = {s.lower() for s in skip_identifiers} if skip_identifiers else set()

    for kind, pattern in _DETECTORS:
        def _replacer(match: re.Match[str], _kind: str = kind) -> str:
            token = match.group(0)
            if _contains_placeholder(token):
                return token
            low = token.lower()
            if _kind == "unix_path" and low in SLASH_EXCLUSIONS:
                return token
            if _kind == "identifier" and low in skip_lower:
                return token
            return state.add_span(token)

        state.text = pattern.sub(_replacer, state.text)

    return state


def unmask(text: str, spans: list[str]) -> str:
    """
    Restore all `⟦n⟧` placeholders back to their original text.
    Performs up to 3 passes in case a span added incrementally references an earlier placeholder.
    """
    if not spans or OPEN_BRACKET not in text:
        return text

    result = text
    for _ in range(3):
        if OPEN_BRACKET not in result:
            break

        def _restore(match: re.Match[str]) -> str:
            idx = int(match.group(1))
            if 0 <= idx < len(spans):
                return spans[idx]
            return match.group(0)

        new_result = PLACEHOLDER_RE.sub(_restore, result)
        if new_result == result:
            break
        result = new_result

    return result


def verify(text: str, spans: list[str]) -> bool:
    """
    Verify placeholder integrity (§4.11, Task 2.1):
    Every placeholder `⟦0⟧` .. `⟦len(spans)-1⟧` must appear in the text
    (or inside another outer span that appears in the text) exactly once,
    with no unknown placeholder indices.
    """
    if not spans:
        return len(PLACEHOLDER_RE.findall(text)) == 0

    # Expand nested placeholders in a copy so we check effective survival of all spans
    expanded = text
    seen_top = [int(m) for m in PLACEHOLDER_RE.findall(expanded)]
    for idx in seen_top:
        if idx < 0 or idx >= len(spans):
            return False

    # Count occurrences across expanded levels: each top-level span not nested inside
    # another span must survive exactly once.
    nested_indices: set[int] = set()
    for span in spans:
        for m in PLACEHOLDER_RE.findall(span):
            nested_indices.add(int(m))

    required_top = [i for i in range(len(spans)) if i not in nested_indices]
    if sorted(seen_top) != sorted(required_top):
        return False

    return True