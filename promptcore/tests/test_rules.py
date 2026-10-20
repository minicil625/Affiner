"""
Unit tests for `promptcore/rules/*`, `vocab.py`, and `signals.py` (Task 2.2 AC: >= 80 cases).
Covers shorthand, fillers, spoken syntax, self-corrections, capitalization,
vocabulary exact + fuzzy matching, and full pipeline execution.
"""

from __future__ import annotations

import pytest

from promptcore.rules.capitalization import apply_capitalization_and_punctuation
from promptcore.rules.corrections import has_correction_cue, resolve_corrections
from promptcore.rules.fillers import strip_fillers
from promptcore.rules.pipeline import RulePipeline
from promptcore.rules.shorthand import expand_shorthand
from promptcore.rules.spoken_syntax import convert_spoken_syntax
from promptcore.signals import FastPathSignals
from promptcore.vocab import Vocabulary

# ---------------------------------------------------------------------------
# 1. Exact Plan Acceptance Criteria Cases (Task 2.2 AC)
# ---------------------------------------------------------------------------


def test_plan_ac_headline_cases() -> None:
    pipeline = RulePipeline()

    # AC 1: "git checkout dash b feature slash login" -> "`git checkout -b feature/login`"
    res1 = pipeline.run("git checkout dash b feature slash login")
    assert res1.text == "`git checkout -b feature/login`"

    # AC 2: "use sqlite wait actually postgres" -> "Use PostgreSQL."
    res2 = pipeline.run("use sqlite wait actually postgres")
    assert res2.text == "Use PostgreSQL."
    assert res2.signals.has_correction_cue is False

    # AC 3: "setup fastapi w/ pydantic and nextjs" -> "Set up FastAPI with Pydantic and Next.js."
    res3 = pipeline.run("setup fastapi w/ pydantic and nextjs")
    assert res3.text == "Set up FastAPI with Pydantic and Next.js."


# ---------------------------------------------------------------------------
# 2. Shorthand Expansion Tests (20 cases)
# ---------------------------------------------------------------------------

SHORTHAND_CASES = [
    ("pls fix this bug", "please fix this bug"),
    ("plz check the logs", "please check the logs"),
    ("can u review my repo", "can you review my repository"),
    ("check ur env vars", "check your environment vars"),
    ("we r ready rn", "we are ready right now"),
    ("failed bc of auth", "failed because of authentication"),
    ("cuz the db is down", "because the database is down"),
    ("connect w/ tls and w/o proxy", "connect with tls and without proxy"),
    ("run this b4 deploy", "run this before deploy"),
    ("idk why the fn fails", "I don't know why the function fails"),
    ("imo this pkg has too many deps", "in my opinion this package has too many dependencies"),
    ("tbh the msg param is missing", "to be honest the message parameter is missing"),
    ("fyi check the root dir asap", "for your information check the root directory as soon as possible"),
    ("i dont think it cant work", "i don't think it can't work"),
    ("im sure weve tested it", "I'm sure we've tested it"),
    ("youre right thats cleaner", "you're right that's cleaner"),
    ("lets see whats inside", "let's see what's inside"),
    ("wanna refactor gonna take an hour", "want to refactor going to take an hour"),
    ("setup fastapi w/ pydantic", "set up fastapi with pydantic"),
    ("please fix the bug in app.py thx", "please fix the bug in app.py, thanks"),
]


@pytest.mark.parametrize(("raw", "expected"), SHORTHAND_CASES)
def test_shorthand_expansions(raw: str, expected: str) -> None:
    assert expand_shorthand(raw) == expected


def test_shorthand_skipped_for_transcript_input() -> None:
    pipeline = RulePipeline(input_type="transcript")
    res = pipeline.run("can u check the repo", input_type="transcript", sentence_punctuation="never")
    # In transcript mode, "u" and "repo" are not expanded by typed shorthand rules
    assert "repo" in res.text


# ---------------------------------------------------------------------------
# 3. Filler Stripping & Stutter Collapsing Tests (15 cases)
# ---------------------------------------------------------------------------

FILLER_CASES = [
    ("um please fix the bug", "please fix the bug"),
    ("uh, we need to, er, refactor this", "we need to, refactor this"),
    ("hmm can we add caching", "can we add caching"),
    ("basically, we need a faster query", "we need a faster query"),
    ("you know, this function is too long", "this function is too long"),
    ("we should, like, split this module", "we should split this module"),
    ("it looks like a bug in the parser", "it looks like a bug in the parser"),
    ("feels like a race condition", "feels like a race condition"),
    ("fix the the bug in in the router", "fix the bug in the router"),
    ("we we need to to check can can it run", "we need to check can it run"),
    ("um uh er hmm test", "test"),
    ("the query is, you know, slow", "the query is slow"),
    ("literally, just return the list", "just return the list"),
    ("check the, sort of, timeout logic", "check the timeout logic"),
    ("i i want a a clean prompt", "i want a clean prompt"),
]


@pytest.mark.parametrize(("raw", "expected"), FILLER_CASES)
def test_filler_stripping(raw: str, expected: str) -> None:
    assert strip_fillers(raw) == expected


# ---------------------------------------------------------------------------
# 4. Spoken Syntax Conversion Tests (18 cases)
# ---------------------------------------------------------------------------

SPOKEN_SYNTAX_CASES = [
    ("open app dot py", "open app.py"),
    ("edit config dot yaml", "edit config.yaml"),
    ("check package dot json", "check package.json"),
    ("read pyproject dot toml", "read pyproject.toml"),
    ("run with dash b flag", "run with -b flag"),
    ("pass double dash force", "pass --force"),
    ("use double dash dry dash run", "use --dry-run"),
    ("checkout feature slash login", "checkout feature/login"),
    ("look in src slash components slash button.tsx", "look in src/components/button.tsx"),
    ("rename user underscore id", "rename user_id"),
    ("set max underscore retry underscore count", "set max_retry_count"),
    ("create camel case get user id", "create getUserId"),
    ("define pascal case user profile card", "define UserProfileCard"),
    ("use snake case created at timestamp", "use created_at_timestamp"),
    ("name it kebab case my custom hook", "name it my-custom-hook"),
    ("git checkout dash b feature slash login", "`git checkout -b feature/login`"),
    ("docker run dash d double dash name web", "`docker run -d --name web`"),
    ("pytest dash q double dash maxfail", "`pytest -q --maxfail`"),
]


@pytest.mark.parametrize(("raw", "expected"), SPOKEN_SYNTAX_CASES)
def test_spoken_syntax_conversion(raw: str, expected: str) -> None:
    assert convert_spoken_syntax(raw) == expected


# ---------------------------------------------------------------------------
# 5. Self-Correction Detection & Resolution Tests (12 cases)
# ---------------------------------------------------------------------------

CORRECTION_RESOLUTION_CASES = [
    ("use sqlite wait actually postgres", "use PostgreSQL", False),
    ("deploy to heroku no wait vercel", "deploy to Vercel", False),
    ("write it in javascript actually typescript", "write it in TypeScript", False),
    ("use mysql scratch that postgres", "use PostgreSQL", False),
    ("connect to redis or rather mongodb", "connect to MongoDB", False),
    ("use not sqlite but postgres", "use PostgreSQL", False),
    # Complex non-vocabulary self-corrections keep `has_correction_cue = True` for the LLM
    ("send the email to John wait actually send it to Sarah tomorrow", "send the email to John wait actually send it to Sarah tomorrow", True),
    ("make the button blue no wait make the header dark grey", "make the button blue no wait make the header dark grey", True),
    ("delete the old table scratch that just rename it", "delete the old table scratch that just rename it", True),
    # Non-correction uses of "wait" / "actually" do NOT trigger correction cue
    ("wait for the server to start", "wait for the server to start", False),
    ("wait until the promise resolves", "wait until the promise resolves", False),
    ("check if the endpoint actually works", "check if the endpoint actually works", False),
]


@pytest.mark.parametrize(("raw", "expected_text", "expected_cue"), CORRECTION_RESOLUTION_CASES)
def test_corrections_resolution_and_cues(raw: str, expected_text: str, expected_cue: bool) -> None:
    vocab = Vocabulary.from_file()
    vocab.add_entry("heroku", "Heroku")
    vocab.add_entry("vercel", "Vercel")
    vocab.add_entry("mysql", "MySQL")
    resolved, cue = resolve_corrections(raw, vocab)
    assert resolved == expected_text
    assert cue is expected_cue


# ---------------------------------------------------------------------------
# 6. Capitalization, Tech Vocabulary Casing & Punctuation Tests (15 cases)
# ---------------------------------------------------------------------------

CAPITALIZATION_CASES = [
    ("use fastapi and pydantic", "auto", "Use FastAPI and Pydantic."),
    ("connect to postgres and redis", "auto", "Connect to PostgreSQL and Redis."),
    ("migrate from reactjs to nextjs", "auto", "Migrate from React to Next.js."),
    ("i think i'm going to use typescript", "auto", "I think I'm going to use TypeScript."),
    ("how do i connect to postgres", "auto", "How do I connect to PostgreSQL?"),
    ("what is the difference between graphql and grpc", "auto", "What is the difference between GraphQL and gRPC?"),
    ("refactor", "auto", "Refactor"),  # single word in 'auto' mode skips period
    ("refactor", "always", "Refactor."),
    ("use fastapi and pydantic", "never", "Use FastAPI and Pydantic"),
    ("`git status`", "auto", "`git status`"),
    ("install pywin32 and httpx", "auto", "Install pywin32 and httpx."),
    ("pywin32 is used for clipboard", "auto", "pywin32 is used for clipboard."),
    ("deploy on github actions with docker", "auto", "Deploy on GitHub Actions with Docker."),
    ("parse json yaml and toml files", "auto", "Parse JSON YAML and TOML files."),
    ("configure jwt and oauth", "auto", "Configure JWT and OAuth."),
]


@pytest.mark.parametrize(("raw", "punct_mode", "expected"), CAPITALIZATION_CASES)
def test_capitalization_and_punctuation(raw: str, punct_mode: str, expected: str) -> None:
    vocab = Vocabulary.from_file()
    vocab.add_entry("pywin32", "pywin32")
    vocab.add_entry("httpx", "httpx")
    vocab.add_entry("github actions", "GitHub Actions")
    assert apply_capitalization_and_punctuation(raw, vocab=vocab, sentence_punctuation=punct_mode) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. Vocabulary Exact & Fuzzy Hint Matching + FastPathSignals Tests (10 cases)
# ---------------------------------------------------------------------------


def test_vocab_exact_and_fuzzy_hints() -> None:
    vocab = Vocabulary.from_file()
    # Exact match + space-collapsed match ("fast api") + fuzzy typo match ("post gress", "pydantik")
    hints = vocab.hints("we use fast api with post gress and pydantik on next js")
    assert "FastAPI" in hints
    assert "PostgreSQL" in hints
    assert "Pydantic" in hints
    assert "Next.js" in hints

    tag = vocab.format_user_tag("deploy fastapi to github")
    assert tag == "<vocabulary>FastAPI, GitHub</vocabulary>"


def test_fast_path_signals_contract() -> None:
    # Clean signal -> takes fast path
    s_clean = FastPathSignals(word_count=8)
    assert s_clean.can_take_fast_path(enabled=True, strictness="balanced") is True

    # <= 3 words clean -> takes fast path
    s_short = FastPathSignals(word_count=2, has_grammar_risk=True)
    assert s_short.can_take_fast_path(enabled=True, strictness="balanced") is True

    # Grammar risk blocks "balanced" but passes "aggressive"
    s_grammar = FastPathSignals(word_count=10, has_grammar_risk=True)
    assert s_grammar.can_take_fast_path(enabled=True, strictness="balanced") is False
    assert s_grammar.can_take_fast_path(enabled=True, strictness="aggressive") is True

    # Unresolved correction cue or non-English or unresolved typos always route to LLM
    assert FastPathSignals(word_count=8, has_correction_cue=True).can_take_fast_path() is False
    assert FastPathSignals(word_count=8, non_english=True).can_take_fast_path() is False
    assert FastPathSignals(word_count=8, unresolved_typos=1).can_take_fast_path() is False
    assert FastPathSignals(word_count=8, low_confidence_words=1).can_take_fast_path() is False
    assert s_clean.can_take_fast_path(enabled=True, strictness="off") is False