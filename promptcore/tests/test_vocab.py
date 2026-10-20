"""
Dedicated unit tests for `promptcore/vocab.py` (§4.1, §4.9).
Verifies exact case-insensitive matching, alias mapping, space-collapsed matching,
and rapidfuzz >= 85 fuzzy matching on 1-3 word windows.
"""

from __future__ import annotations

from pathlib import Path

from promptcore.vocab import Vocabulary


def test_vocab_loads_custom_file_and_aliases(tmp_path: Path) -> None:
    vfile = tmp_path / "vocabulary.txt"
    vfile.write_text(
        "# Comment line\n"
        "postgres => PostgreSQL\n"
        "fastapi => FastAPI\n"
        "Tailscale\n",
        encoding="utf-8",
    )
    vocab = Vocabulary.from_file(vfile)

    assert vocab.is_vocab_term("postgres") is True
    assert vocab.get_canonical("postgres") == "PostgreSQL"
    assert vocab.get_canonical("FASTAPI") == "FastAPI"
    assert vocab.get_canonical("tailscale") == "Tailscale"


def test_vocab_hints_catches_collapsed_and_fuzzy_misspellings() -> None:
    vocab = Vocabulary.from_file()

    # "fast api" (space-separated), "post gress" (speech/typo), "type script" (space-separated)
    text = "connect fast api to post gress and write the frontend in type script"
    hints = vocab.hints(text)

    assert "FastAPI" in hints
    assert "PostgreSQL" in hints
    assert "TypeScript" in hints


def test_vocab_hints_respects_max_terms_and_formats_xml_tag() -> None:
    vocab = Vocabulary.from_file()
    text = "use python fastapi pydantic postgresql redis docker kubernetes github graphql jwt"
    hints_capped = vocab.hints(text, max_terms=4)
    assert len(hints_capped) == 4

    tag = vocab.format_user_tag("use fastapi and redis")
    assert tag == "<vocabulary>FastAPI, Redis</vocabulary>"
    assert vocab.format_user_tag("hello world") == ""