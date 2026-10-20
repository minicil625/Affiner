"""
Unit tests for `promptcore/masking.py` (Task 2.1 AC: >= 30 cases, lossless round-trip,
placeholder verification, and incremental masking).
"""

from __future__ import annotations

import pytest

from promptcore.masking import OPEN_BRACKET, mask, unmask, verify

MASK_TEST_CASES = [
    # 1-3: Fenced code blocks
    ("Fix this block:\n```python\ndef foo(x):\n    return x + 1\n```\nplease", "```python\ndef foo(x):\n    return x + 1\n```"),
    ("Check ~~~js\nconsole.log('hi');\n~~~ now", "~~~js\nconsole.log('hi');\n~~~"),
    ("Multiple fences ```a``` and ```b``` here", "```a```"),
    # 4-7: Inline backticks
    ("Run `uv sync --all-extras` in terminal", "`uv sync --all-extras`"),
    ("Change `SELECT * FROM users` to use limit", "`SELECT * FROM users`"),
    ("Compare `foo_bar` and `baz_qux`", "`foo_bar`"),
    ("Use `Ctrl+Shift+C` in terminal", "`Ctrl+Shift+C`"),
    # 8-11: URLs
    ("Check https://api.groq.com/openai/v1/models for models", "https://api.groq.com/openai/v1/models"),
    ("Visit http://localhost:8000/docs?debug=true#auth.", "http://localhost:8000/docs?debug=true#auth"),
    ("Connect to wss://realtime.example.com/socket", "wss://realtime.example.com/socket"),
    ("Download from ftp://files.example.org/pub/data.zip", "ftp://files.example.org/pub/data.zip"),
    # 12-13: Emails
    ("Send logs to support@example.com or dev.ops+alerts@company.io", "support@example.com"),
    ("Contact admin_user@sub.domain.co.uk please", "admin_user@sub.domain.co.uk"),
    # 14-17: Windows & Unix paths
    (r"Open C:\Users\Ben\AppData\Roaming\PromptCore\usage.json", r"C:\Users\Ben\AppData\Roaming\PromptCore\usage.json"),
    (r"Run ..\scripts\setup.ps1 from PowerShell", r"..\scripts\setup.ps1"),
    ("Edit /etc/nginx/sites-available/default", "/etc/nginx/sites-available/default"),
    ("Import from ./src/components/Button.tsx or ../promptcore/config.py", "./src/components/Button.tsx"),
    # 18-21: Filenames with known extensions
    ("Check pyproject.toml and config.yaml", "pyproject.toml"),
    ("Update main.py and test_masking.py", "main.py"),
    ("Look at frequency_dictionary_en_82_765.txt", "frequency_dictionary_en_82_765.txt"),
    ("Bundle into app.exe or archive.tar.gz", "app.exe"),
    # 22-25: CLI flags
    ("Pass --max-tokens=320 and --force to the CLI", "--max-tokens=320"),
    ("Use -rf or -v when running the command", "-rf"),
    ("Add --all-extras to uv sync", "--all-extras"),
    ("Run pytest -q --tb=short", "--tb=short"),
    # 26-28: Versions & hex numbers
    ("Upgrade from v1.1.0 to v2.0.3-beta.1", "v1.1.0"),
    ("Python 3.11.8 and Node 20.11.1", "3.11.8"),
    ("Memory fault at 0xDEADBEEF or 0x0040A1", "0xDEADBEEF"),
    # 29-31: @mentions and #tags
    ("Ping @octocat and @team-backend on the PR", "@octocat"),
    ("Closes #142 and relates to #bug-fix", "#142"),
    ("See channel #general-dev for details", "#general-dev"),
    # 32-36: Identifiers & function calls
    ("Call get_clipboard_sequence_number() in capture_selection()", "get_clipboard_sequence_number()"),
    ("Update user_id and created_at in the table", "user_id"),
    ("Rename getUserById to fetchUserProfile", "getUserById"),
    ("Check FastPathSignals and OpenAICompatibleBackend classes", "FastPathSignals"),
    ("Set MAX_RETRY_COUNT and __init__ properly", "MAX_RETRY_COUNT"),
]


@pytest.mark.parametrize(("input_text", "expected_span"), MASK_TEST_CASES)
def test_mask_detects_span_and_roundtrips_losslessly(input_text: str, expected_span: str) -> None:
    state = mask(input_text)
    assert OPEN_BRACKET in state.text
    assert expected_span in state.spans
    assert expected_span not in state.text
    assert verify(state.text, state.spans) is True
    restored = unmask(state.text, state.spans)
    assert restored == input_text


def test_slash_shorthand_is_not_masked_as_path() -> None:
    text = "setup fastapi w/ pydantic w/o extra deps b/c we need speed"
    state = mask(text)
    assert "w/" in state.text
    assert "w/o" in state.text
    assert "b/c" in state.text
    assert state.spans == []


def test_verify_detects_missing_or_duplicated_placeholders() -> None:
    text = "Compare `foo` and `bar` in app.py"
    state = mask(text)
    assert len(state.spans) == 3
    assert verify(state.text, state.spans) is True

    # Missing placeholder ⟦1⟧
    dropped = state.text.replace("⟦1⟧", "bar")
    assert verify(dropped, state.spans) is False

    # Duplicated placeholder ⟦0⟧
    duplicated = state.text + " ⟦0⟧"
    assert verify(duplicated, state.spans) is False

    # Out-of-range placeholder ⟦99⟧
    unknown = state.text + " ⟦99⟧"
    assert verify(unknown, state.spans) is False


def test_incremental_masking_preserves_indices() -> None:
    state1 = mask("Check `first_span` in app dot py")
    assert state1.spans == ["`first_span`"]
    # Simulate spoken_syntax converting "app dot py" -> "app.py"
    updated = state1.text.replace("app dot py", "app.py")
    state2 = mask(updated, existing_spans=state1.spans)
    assert state2.spans == ["`first_span`", "app.py"]
    assert verify(state2.text, state2.spans) is True
    assert unmask(state2.text, state2.spans) == "Check `first_span` in app.py"