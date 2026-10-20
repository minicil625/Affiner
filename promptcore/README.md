# `promptcore` — Shared Deterministic Rule Engine & Cloud Free-Tier LLM Router

`promptcore` is the shared pure-Python core package powering both **TextPrompt AI** (`input_type="typed"`) and **VoicePrompt AI** (`input_type="transcript"`).

## Key Responsibilities

1. **Protected-Span Masking (`promptcore/masking.py`):**
   Replaces code fences, inline backticks, URLs, emails, Windows/Unix paths, filenames, CLI flags, semantic versions, `@mentions`, `#tags`, and `camelCase`/`snake_case`/`PascalCase` identifiers with unicode placeholders (`⟦0⟧`, `⟦1⟧`, …) before any rule or LLM sees the text.
2. **Deterministic Rule Pipeline (`promptcore/rules/`):**
   Executes in `< 5 ms`:
   - `fillers.py`: Strips vocalized pauses (`um`, `uh`, `er`, `hmm`), discourse fillers in filler positions, and word stutters (`the the`).
   - `spoken_syntax.py`: Converts `app dot py` $\to$ `app.py`, `dash b` $\to$ `-b`, `double dash force` $\to$ `--force`, `feature slash login` $\to$ `feature/login`, `camel case get user id` $\to$ `getUserId`, and wraps standalone CLI commands in backticks.
   - `shorthand.py` (`typed` only): Whole-word expansion (`pls` $\to$ `please`, `w/` $\to$ `with`, `b/c` $\to$ `because`, `thx` $\to$ `thanks`, etc.).
   - `corrections.py`: Resolves deterministic *"X, wait/actually, Y"* self-corrections and flags `has_correction_cue=True` when complex self-corrections require an LLM.
   - `capitalization.py`: Applies canonical tech vocabulary casing (~220 terms), sentence capitalization, and smart terminal punctuation.
3. **Fast-Path Contract (`promptcore/signals.py`):**
   Skips the cloud API call completely in Mode 1 when `FastPathSignals` indicates all edits were handled deterministically.
4. **Multi-Provider Free-Tier Router (`promptcore/llm/`):**
   - 6 OpenAI-compatible presets (`groq`, `cerebras`, `gemini`, `openrouter`, `github_models`, `mistral`).
   - Persistent `httpx.Client(http2=True)` connection pooling + idle re-warming (`GET /models`).
   - Shared cross-process quota tracking in `%APPDATA%\PromptCore\usage.json` protected by `filelock`.
   - Shared API keys in Windows Credential Manager under service `PromptCore`.
   - Streamed Mode 1 with 600 ms first-token hedging and 20-char streaming early-abort guard.

## Running Tests & Benchmarks

```powershell
uv run pytest -q
uv run python scripts/check_providers.py
uv run python scripts/run_eval.py
```