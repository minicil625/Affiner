"""
Unit tests for `textprompt/typo.py` and full integration with `promptcore.rules.pipeline` (Task 2.3 AC).
"""

from __future__ import annotations

from pathlib import Path

from promptcore.rules.pipeline import RulePipeline
from promptcore.vocab import Vocabulary
from textprompt.typo import TypoEngine


def _make_engine(tmp_path: Path | None = None) -> TypoEngine:
    vocab_file = Path(__file__).resolve().parent.parent / "vocabulary.txt"
    vocab = Vocabulary.from_file(vocab_file)
    pickle_path = (tmp_path / "symspell.pkl") if tmp_path else None
    return TypoEngine(vocab=vocab, lazy_load=False, pickle_cache_path=pickle_path)


def test_full_pipeline_with_symspell_headline_ac() -> None:
    """
    Task 2.2 + 2.3 headline AC:
    "um pls fix teh bug in app dot py thx" -> "Please fix the bug in app.py, thanks."
    """
    vocab_file = Path(__file__).resolve().parent.parent / "vocabulary.txt"
    shorthand_file = Path(__file__).resolve().parent.parent / "shorthand.txt"
    pipeline = RulePipeline.from_paths(vocab_file, shorthand_file, input_type="typed")
    typo_engine = _make_engine()

    res = pipeline.run(
        "um pls fix teh bug in app dot py thx",
        typo_hook=typo_engine.process_masked_text,
    )
    assert res.text == "Please fix the bug in app.py, thanks."
    assert res.signals.can_take_fast_path() is True


def test_vocabulary_and_protected_spans_are_never_corrupted() -> None:
    typo_engine = _make_engine()
    vocab_file = Path(__file__).resolve().parent.parent / "vocabulary.txt"
    pipeline = RulePipeline.from_paths(vocab_file, input_type="typed")

    raw = "use fastapi with pydantic, symspellpy, rapidfuzz, and `custom_var_xyz` in app.py"
    res = pipeline.run(raw, typo_hook=typo_engine.process_masked_text)
    assert "FastAPI" in res.text
    assert "Pydantic" in res.text
    assert "symspellpy" in res.text
    assert "RapidFuzz" in res.text
    assert "`custom_var_xyz`" in res.text
    assert "app.py" in res.text
    assert res.signals.unresolved_typos == 0


def test_language_guard_skips_non_english_input() -> None:
    """
    Task 2.3 AC: If > 40% of words aren't English (e.g. Indonesian), SymSpell and
    English shorthand rules are skipped and `non_english=True` is set.
    """
    typo_engine = _make_engine()
    pipeline = RulePipeline(input_type="typed")

    indonesian_input = "tolong perbaiki kesalahan pada fungsi autentikasi pengguna sekarang"
    res = pipeline.run(indonesian_input, typo_hook=typo_engine.process_masked_text)

    assert res.signals.non_english is True
    assert res.signals.can_take_fast_path() is False
    # Words should not be mangled into English words by SymSpell
    assert "tolong" in res.text.lower()
    assert "perbaiki" in res.text.lower()


def test_synthetic_typo_set_accuracy_over_95_percent(tmp_path: Path) -> None:
    """
    Task 2.3 AC: >= 95% of synthetic typos corrected, pickle cache works cleanly.
    """
    engine = _make_engine(tmp_path)
    assert (tmp_path / "symspell.pkl").exists()

    # Reload from pickle cache to verify pickle round-trip
    cached_engine = _make_engine(tmp_path)

    typo_pairs = [
        ("teh", "the"),
        ("taht", "that"),
        ("wiht", "with"),
        ("recieve", "receive"),
        ("seperate", "separate"),
        ("definately", "definitely"),
        ("occured", "occurred"),
        ("untill", "until"),
        ("enviroment", "environment"),
        ("responce", "response"),
        ("calender", "calendar"),
        ("necesary", "necessary"),
        ("wierd", "weird"),
        ("accomodate", "accommodate"),
        ("arguement", "argument"),
        ("maintainance", "maintenance"),
        ("independant", "independent"),
        ("existance", "existence"),
        ("implementaton", "implementation"),
        ("functon", "function"),
        ("varible", "variable"),
        ("databse", "database"),
        ("authantication", "authentication"),
        ("configuraton", "configuration"),
        ("conection", "connection"),
        ("applicaiton", "application"),
        ("perfromance", "performance"),
        ("paramter", "parameter"),
        ("repositry", "repository"),
        ("mesage", "message"),
    ]

    corrected_count = 0
    for wrong, right in typo_pairs:
        out, _ = cached_engine.process_masked_text(f"please check {wrong} value")
        if f"please check {right} value" == out:
            corrected_count += 1

    accuracy = corrected_count / len(typo_pairs)
    assert accuracy >= 0.95, f"Expected >= 95% accuracy, got {accuracy:.1%}"