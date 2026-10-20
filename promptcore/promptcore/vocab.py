"""
Vocabulary loading, canonical casing map, and per-input LLM hint matching (exact + fuzzy).
Implements §4.9 and Task 2.2.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from rapidfuzz import fuzz

WORD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9+#._/-]+")


class Vocabulary:
    """
    Manages technical terms and alias -> Canonical mappings from `vocabulary.txt`.
    Used by:
      1. `rules/capitalization.py` for deterministic casing
      2. `vocab.hints(text)` for exact + fuzzy `<vocabulary>` hints in the LLM user message
      3. `textprompt/typo.py` to boost tech terms in SymSpell
    """

    def __init__(self, terms_map: dict[str, str] | None = None) -> None:
        # Maps lowercase alias/term -> Canonical form (e.g. "postgres" -> "PostgreSQL")
        self.canonical_map: dict[str, str] = {}
        # Maps space-collapsed lowercase key -> Canonical form (e.g. "fastapi" -> "FastAPI")
        self.collapsed_map: dict[str, str] = {}
        # Set of all lowercase single-word tokens belonging to the vocabulary
        self.known_words: set[str] = set()

        if terms_map:
            for k, v in terms_map.items():
                self.add_entry(k, v)

    def add_entry(self, alias: str, canonical: str | None = None) -> None:
        clean_alias = alias.strip()
        if not clean_alias or clean_alias.startswith("#"):
            return
        clean_canonical = (canonical or clean_alias).strip()
        low_alias = clean_alias.lower()
        low_canon = clean_canonical.lower()

        self.canonical_map[low_alias] = clean_canonical
        self.canonical_map.setdefault(low_canon, clean_canonical)

        collapsed_alias = re.sub(r"[\s._-]+", "", low_alias)
        if len(collapsed_alias) >= 3:
            self.collapsed_map[collapsed_alias] = clean_canonical

        collapsed_canon = re.sub(r"[\s._-]+", "", low_canon)
        if len(collapsed_canon) >= 3:
            self.collapsed_map.setdefault(collapsed_canon, clean_canonical)

        for part in re.findall(r"[A-Za-z0-9]+", low_alias):
            self.known_words.add(part)
        for part in re.findall(r"[A-Za-z0-9]+", low_canon):
            self.known_words.add(part)

    @classmethod
    def from_file(cls, path: Path | str | None = None) -> Vocabulary:
        vocab = cls(_DEFAULT_BUILTIN_VOCAB)
        if path is None:
            return vocab
        file_path = Path(path)
        if not file_path.exists():
            return vocab

        for raw_line in file_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=>" in line:
                left, right = line.split("=>", 1)
                vocab.add_entry(left.strip(), right.strip())
            else:
                vocab.add_entry(line, line)
        return vocab

    def is_vocab_term(self, text: str) -> bool:
        low = text.strip().lower()
        return low in self.canonical_map or re.sub(r"[\s._-]+", "", low) in self.collapsed_map

    def get_canonical(self, text: str) -> str | None:
        low = text.strip().lower()
        if low in self.canonical_map:
            return self.canonical_map[low]
        collapsed = re.sub(r"[\s._-]+", "", low)
        return self.collapsed_map.get(collapsed)

    def hints(
        self,
        text: str,
        max_terms: int = 30,
        fuzzy_threshold: float = 85.0,
    ) -> list[str]:
        """
        Return up to `max_terms` canonical vocabulary terms matched in `text`
        via exact case-insensitive lookup OR fuzzy matching (rapidfuzz >= 85
        on space-collapsed 1-3-word windows, catching "fast api" -> "FastAPI"
        and "post gress" -> "PostgreSQL").
        """
        if not text or not text.strip():
            return []

        matched: list[str] = []
        seen: set[str] = set()

        def _record(canon: str) -> None:
            if canon not in seen and len(matched) < max_terms:
                seen.add(canon)
                matched.append(canon)

        words = WORD_TOKEN_RE.findall(text.lower())
        if not words:
            return []

        # 1. Exact 1-to-3 word window matches & exact collapsed matches
        n = len(words)
        for i in range(n):
            for width in (1, 2, 3):
                if i + width > n:
                    continue
                phrase = " ".join(words[i : i + width])
                if phrase in self.canonical_map:
                    _record(self.canonical_map[phrase])
                    continue
                collapsed = re.sub(r"[\s._-]+", "", phrase)
                if len(collapsed) >= 4 and collapsed in self.collapsed_map:
                    _record(self.collapsed_map[collapsed])

        # 2. Fuzzy matches on space-collapsed 1-3 word windows (rapidfuzz >= 85)
        if len(matched) < max_terms:
            candidate_windows: list[str] = []
            for i in range(n):
                for width in (1, 2, 3):
                    if i + width <= n:
                        collapsed_win = re.sub(r"[\s._-]+", "", "".join(words[i : i + width]))
                        if len(collapsed_win) >= 5:
                            candidate_windows.append(collapsed_win)

            for win in candidate_windows:
                if len(matched) >= max_terms:
                    break
                if win in self.collapsed_map:
                    _record(self.collapsed_map[win])
                    continue
                best_score = 0.0
                best_canon: str | None = None
                for key, canon in self.collapsed_map.items():
                    if len(key) < 5 or abs(len(key) - len(win)) > 2:
                        continue
                    if key[0] != win[0]:
                        continue
                    score = fuzz.ratio(win, key)
                    if score >= fuzzy_threshold and score > best_score:
                        best_score = score
                        best_canon = canon
                if best_canon:
                    _record(best_canon)

        return matched

    def format_user_tag(self, text: str, max_terms: int = 30) -> str:
        """Format matched terms as `<vocabulary>Term1, Term2</vocabulary>` or empty string."""
        found = self.hints(text, max_terms=max_terms)
        if not found:
            return ""
        return f"<vocabulary>{', '.join(found)}</vocabulary>"


_DEFAULT_BUILTIN_VOCAB: Final[dict[str, str]] = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "fastapi": "FastAPI",
    "pydantic": "Pydantic",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "vuejs": "Vue.js",
    "vue.js": "Vue.js",
    "reactjs": "React",
    "react": "React",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "python": "Python",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "sqlite": "SQLite",
    "mongodb": "MongoDB",
    "dynamodb": "DynamoDB",
    "redis": "Redis",
    "graphql": "GraphQL",
    "grpc": "gRPC",
    "trpc": "tRPC",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "K8s",
    "github": "GitHub",
    "gitlab": "GitLab",
    "vscode": "VS Code",
    "tailwind": "Tailwind CSS",
    "tailwindcss": "Tailwind CSS",
    "supabase": "Supabase",
    "prisma": "Prisma",
    "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
    "symspell": "SymSpell",
    "rapidfuzz": "RapidFuzz",
    "pyinstaller": "PyInstaller",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "groq": "Groq",
    "cerebras": "Cerebras",
    "openrouter": "OpenRouter",
    "mistral": "Mistral",
    "json": "JSON",
    "yaml": "YAML",
    "toml": "TOML",
    "jwt": "JWT",
    "oauth": "OAuth",
    "api": "API",
    "apis": "APIs",
    "cli": "CLI",
    "sdk": "SDK",
    "sql": "SQL",
    "html": "HTML",
    "css": "CSS",
    "url": "URL",
    "urls": "URLs",
    "llm": "LLM",
    "llms": "LLMs",
}