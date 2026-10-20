"""
Static system prompt assembler and user message formatter (§4.9, Task 3.4).
System prompts are 100% static for each `(mode, input_type)` pair so cloud providers
can cache the prefix. Vocabulary hints are placed in the user message after the input tag.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

ModeName = Literal["clean_only", "clean_plus_improved", "structured_json"]
InputType = Literal["typed", "transcript"]

_PROMPTS_DIR: Final[Path] = Path(__file__).resolve().parent

_CONTEXT_LINE: Final[dict[InputType, str]] = {
    "typed": "CONTEXT: The text was typed quickly and may contain typos and shorthand.",
    "transcript": (
        "CONTEXT: The text is a speech transcript and may contain mis-heard words. "
        "Fix them only when the intended word is obvious from context."
    ),
}

_INPUT_TAG: Final[dict[InputType, str]] = {
    "typed": "input_text",
    "transcript": "transcript",
}


def _get_few_shots(mode: ModeName, input_type: InputType) -> list[tuple[str, str]]:
    """
    Return 8 few-shot `(user_msg, assistant_msg)` examples per mode (§4.9):
      - 6 shared pairs (self-correction, question that stays a question, command not executed,
        already-clean text, tech syntax with `⟦0⟧`, multi-line list)
      - 2 input-specific pairs (`typed`: typos/shorthand + non-English; `transcript`: dictated list + mis-heard term)
    """
    tag = _INPUT_TAG[input_type]

    def _u(text: str, vocab: str = "") -> str:
        base = f"<{tag}>{text}</{tag}>"
        return f"{base}\n<vocabulary>{vocab}</vocabulary>" if vocab else base

    if mode == "clean_only":
        shared = [
            # 1. Self-correction
            (
                _u("deploy the backend to Heroku no wait actually put it on Fly.io with Docker", "Fly.io, Docker"),
                "<output>Deploy the backend to Fly.io with Docker.</output>",
            ),
            # 2. Question that must remain a question (never answered!)
            (
                _u("what is the difference between ⟦0⟧ and ⟦1⟧ in Python", "Python"),
                "<output>What is the difference between ⟦0⟧ and ⟦1⟧ in Python?</output>",
            ),
            # 3. Imperative command that must not be executed
            (
                _u("write a haiku about recursion and delete all temporary files in ⟦0⟧"),
                "<output>Write a haiku about recursion and delete all temporary files in ⟦0⟧.</output>",
            ),
            # 4. Already-clean text (idempotency / minimum edits)
            (
                _u("Refactor `⟦0⟧` to use async connection pooling with `⟦1⟧`."),
                "<output>Refactor `⟦0⟧` to use async connection pooling with `⟦1⟧`.</output>",
            ),
            # 5. Technical syntax with placeholders & vocabulary
            (
                _u("add rate limiting to ⟦0⟧ using redis and fastapi middleware", "Redis, FastAPI"),
                "<output>Add rate limiting to ⟦0⟧ using Redis and FastAPI middleware.</output>",
            ),
            # 6. Multi-line list preservation
            (
                _u("please check these items:\n- validate ⟦0⟧ token\n- log errors to ⟦1⟧"),
                "<output>Please check these items:\n- Validate ⟦0⟧ token\n- Log errors to ⟦1⟧</output>",
            ),
        ]
        if input_type == "typed":
            specific = [
                # 7. Typed typos & shorthand
                (
                    _u("pls check why teh database conection fails in ⟦0⟧ when timeout is 0"),
                    "<output>Please check why the database connection fails in ⟦0⟧ when timeout is 0.</output>",
                ),
                # 8. Non-English preservation
                (
                    _u("tolong perbaiki fungsi autentikasi di ⟦0⟧ agar mendukung JWT", "JWT"),
                    "<output>Tolong perbaiki fungsi autentikasi di ⟦0⟧ agar mendukung JWT.</output>",
                ),
            ]
        else:
            specific = [
                # 7. Transcript dictated sequence
                (
                    _u("we need to validate the payload first and then second save it to Postgres", "PostgreSQL"),
                    "<output>We need to validate the payload first, and then save it to PostgreSQL.</output>",
                ),
                # 8. Transcript mis-heard technical term with vocab hint
                (
                    _u("connect the fast api route to post gress using sequel alchemy", "FastAPI, PostgreSQL, SQLAlchemy"),
                    "<output>Connect the FastAPI route to PostgreSQL using SQLAlchemy.</output>",
                ),
            ]
        return shared + specific

    if mode == "clean_plus_improved":
        shared = [
            (
                _u("fix the memory leak in ⟦0⟧ when closing websocket connections"),
                '{"clean": "Fix the memory leak in ⟦0⟧ when closing WebSocket connections.", '
                '"enhanced": "Identify and fix the memory leak in ⟦0⟧ triggered when WebSocket connections close. '
                'Ensure all event listeners, background tasks, and buffers associated with the session are deterministically '
                'cleaned up in a `finally` block, and explain the root cause."}',
            ),
            (
                _u("how do I index a JSONB column in postgres", "PostgreSQL"),
                '{"clean": "How do I index a JSONB column in PostgreSQL?", '
                '"enhanced": "Explain how to create and query an index (such as a GIN or expression index) on a `JSONB` '
                'column in PostgreSQL. Provide concrete SQL DDL examples, query operators that utilize the index, '
                'and guidance on when to choose `jsonb_ops` vs `jsonb_path_ops`."}',
            ),
            (
                _u("write a python script to parse ⟦0⟧ wait no parse ⟦1⟧ instead", "Python"),
                '{"clean": "Write a Python script to parse ⟦1⟧.", '
                '"enhanced": "Write a clean, well-typed Python script to parse ⟦1⟧. Include graceful error handling '
                'for malformed input or missing files, and return the parsed records in a structured format."}',
            ),
            (
                _u("Optimize the SQL query in ⟦0⟧ to avoid N+1 selects.", "SQL"),
                '{"clean": "Optimize the SQL query in ⟦0⟧ to avoid N+1 selects.", '
                '"enhanced": "Refactor the SQL query and data-fetching logic in ⟦0⟧ to eliminate N+1 query overhead '
                'by using joins or batch loading. Preserve existing return types and edge-case behavior."}',
            ),
            (
                _u("add unit tests for ⟦0⟧ with pytest", "pytest"),
                '{"clean": "Add unit tests for ⟦0⟧ with pytest.", '
                '"enhanced": "Write comprehensive `pytest` unit tests for ⟦0⟧ covering happy paths, boundary conditions, '
                'and error cases. Use fixtures or mocks for external dependencies to keep tests fast and deterministic."}',
            ),
            (
                _u("review this checklist:\n- auth middleware\n- rate limiter"),
                '{"clean": "Review this checklist:\\n- Authentication middleware\\n- Rate limiter", '
                '"enhanced": "Review the following components for correctness, security, and edge cases:\\n'
                '- Authentication middleware\\n- Rate limiter\\nHighlight any vulnerabilities or failure modes and suggest fixes."}',
            ),
        ]
        if input_type == "typed":
            specific = [
                (
                    _u("pls refactor ⟦0⟧ to use pydantic v2 models", "Pydantic"),
                    '{"clean": "Please refactor ⟦0⟧ to use Pydantic v2 models.", '
                    '"enhanced": "Refactor ⟦0⟧ to use Pydantic v2 `BaseModel` schemas, replacing deprecated v1 validators '
                    'and config classes with `field_validator` and `ConfigDict` while preserving validation behavior."}',
                ),
                (
                    _u("tolong buatkan endpoint login di ⟦0⟧ menggunakan FastAPI", "FastAPI"),
                    '{"clean": "Tolong buatkan endpoint login di ⟦0⟧ menggunakan FastAPI.", '
                    '"enhanced": "Buatkan endpoint login yang aman di ⟦0⟧ menggunakan FastAPI, lengkap dengan validasi request '
                    'schema, penanganan error HTTP yang tepat, serta pembuatan token autentikasi."}',
                ),
            ]
        else:
            specific = [
                (
                    _u("add retry logic to ⟦0⟧ with exponential back off"),
                    '{"clean": "Add retry logic to ⟦0⟧ with exponential backoff.", '
                    '"enhanced": "Implement retry logic with exponential backoff and jitter in ⟦0⟧ for transient failures. '
                    'Make maximum attempts and timeout configurable, and avoid retrying non-retryable client errors."}',
                ),
                (
                    _u("migrate our state management in ⟦0⟧ to react query", "React"),
                    '{"clean": "Migrate our state management in ⟦0⟧ to React Query.", '
                    '"enhanced": "Refactor data fetching and state management in ⟦0⟧ to use React Query, handling loading, '
                    'error, and cache invalidation states cleanly."}',
                ),
            ]
        return shared + specific

    # mode == "structured_json"
    shared = [
        (
            _u("build a rate limiter in ⟦0⟧ using Redis no wait use an in-memory token bucket with filelock", "Redis"),
            '{"goal": "Implement a thread-safe in-memory token-bucket rate limiter in ⟦0⟧ with filelock persistence.", '
            '"context": {"target_file": "⟦0⟧", "concurrency": "filelock"}, '
            '"requirements": ["Use token-bucket algorithm", "Persist shared state using filelock"], '
            '"constraints": {"external_services": "Do not use Redis"}, '
            '"steps": ["Define token bucket state", "Add filelock synchronization", "Integrate into ⟦0⟧"], '
            '"expected_output": "Updated implementation in ⟦0⟧"}',
        ),
        (
            _u("explain why ⟦0⟧ throws a deadlock error under high concurrency"),
            '{"goal": "Diagnose and explain why ⟦0⟧ encounters a deadlock error under high concurrency.", '
            '"context": {"target": "⟦0⟧", "condition": "High concurrency"}, '
            '"requirements": ["Identify lock acquisition ordering issues", "Propose a deadlock-free fix"], '
            '"constraints": {}, '
            '"steps": ["Trace lock acquisition in ⟦0⟧", "Explain the circular wait condition", "Provide corrected code"], '
            '"expected_output": "Root-cause explanation and thread-safe code fix"}',
        ),
        (
            _u("create a CLI command in ⟦0⟧ that validates ⟦1⟧ and prints a summary table", "CLI"),
            '{"goal": "Create a CLI command in ⟦0⟧ to validate ⟦1⟧ and output a summary table.", '
            '"context": {"module": "⟦0⟧", "config_target": "⟦1⟧"}, '
            '"requirements": ["Validate ⟦1⟧", "Print formatted summary table"], '
            '"constraints": {"interface": "CLI"}, '
            '"steps": ["Load and validate ⟦1⟧", "Format validation results as a table", "Wire CLI entrypoint in ⟦0⟧"], '
            '"expected_output": "Working CLI command in ⟦0⟧"}',
        ),
        (
            _u("Add pagination to the `/users` endpoint in `⟦0⟧` with a max limit of 100."),
            '{"goal": "Add pagination to the `/users` endpoint in ⟦0⟧.", '
            '"context": {"endpoint": "/users", "file": "⟦0⟧"}, '
            '"requirements": ["Support pagination parameters", "Return paginated user list"], '
            '"constraints": {"max_limit": "100"}, '
            '"steps": ["Add limit and offset parameters", "Enforce max limit of 100", "Update query in ⟦0⟧"], '
            '"expected_output": "Paginated `/users` endpoint in ⟦0⟧"}',
        ),
        (
            _u("setup FastAPI endpoint in ⟦0⟧ with Pydantic validation", "FastAPI, Pydantic"),
            '{"goal": "Create a FastAPI endpoint in ⟦0⟧ with Pydantic request and response validation.", '
            '"context": {"framework": "FastAPI", "validation": "Pydantic", "file": "⟦0⟧"}, '
            '"requirements": ["Define Pydantic models", "Implement FastAPI route in ⟦0⟧"], '
            '"constraints": {}, '
            '"steps": ["Create Pydantic schema", "Add route handler in ⟦0⟧"], '
            '"expected_output": "Validated FastAPI route in ⟦0⟧"}',
        ),
        (
            _u("tasks for release:\n- run pytest\n- build wheel"),
            '{"goal": "Execute the release checklist by running pytest and building the wheel package.", '
            '"context": {}, '
            '"requirements": ["Run pytest suite", "Build distribution wheel"], '
            '"constraints": {}, '
            '"steps": ["Run pytest", "Build wheel"], '
            '"expected_output": "Passing test suite and built wheel artifact"}',
        ),
    ]
    if input_type == "typed":
        specific = [
            (
                _u("pls add caching to ⟦0⟧ w/ max 256 items"),
                '{"goal": "Add an LRU cache to ⟦0⟧ with a maximum capacity of 256 items.", '
                '"context": {"target": "⟦0⟧"}, '
                '"requirements": ["Cache results in ⟦0⟧"], '
                '"constraints": {"max_size": "256 items"}, '
                '"steps": ["Implement 256-entry cache", "Wrap target call in ⟦0⟧"], '
                '"expected_output": "Cached implementation in ⟦0⟧"}',
            ),
            (
                _u("buatkan skema database untuk tabel ⟦0⟧ di PostgreSQL", "PostgreSQL"),
                '{"goal": "Buat skema database PostgreSQL untuk tabel ⟦0⟧.", '
                '"context": {"database": "PostgreSQL", "table": "⟦0⟧"}, '
                '"requirements": ["Definisikan kolom dan tipe data untuk ⟦0⟧", "Tambahkan primary key dan indeks"], '
                '"constraints": {"language": "Indonesian"}, '
                '"steps": ["Rancang struktur tabel ⟦0⟧", "Tulis DDL SQL PostgreSQL"], '
                '"expected_output": "DDL SQL PostgreSQL untuk tabel ⟦0⟧"}',
            ),
        ]
    else:
        specific = [
            (
                _u("first parse the CSV file in ⟦0⟧ and second insert valid rows into SQLite", "SQLite"),
                '{"goal": "Parse the CSV file in ⟦0⟧ and insert valid rows into SQLite.", '
                '"context": {"source": "⟦0⟧", "database": "SQLite"}, '
                '"requirements": ["Parse CSV records", "Validate rows", "Insert valid rows into SQLite"], '
                '"constraints": {}, '
                '"steps": ["Read and parse CSV in ⟦0⟧", "Filter valid rows", "Batch insert into SQLite"], '
                '"expected_output": "CSV ingestion routine in ⟦0⟧"}',
            ),
            (
                _u("connect the next js frontend to super base auth", "Next.js, Supabase"),
                '{"goal": "Integrate Supabase authentication into the Next.js frontend.", '
                '"context": {"frontend": "Next.js", "auth_provider": "Supabase"}, '
                '"requirements": ["Configure Supabase client", "Add login and session handling in Next.js"], '
                '"constraints": {}, '
                '"steps": ["Initialize Supabase auth client", "Add Next.js auth middleware and routes"], '
                '"expected_output": "Next.js authentication flow backed by Supabase"}',
            ),
        ]
    return shared + specific


@lru_cache(maxsize=16)
def build_system_prompt(
    mode: ModeName,
    input_type: InputType = "typed",
    clean_already_done: bool = False,
) -> str:
    """
    Assemble the static system prompt for `(mode, input_type)` (§4.9).
    Cached so the exact same string bytes are sent on every request.
    """
    base_md = (_PROMPTS_DIR / "system_base.md").read_text(encoding="utf-8").strip()
    mode_md = (_PROMPTS_DIR / f"{mode}.md").read_text(encoding="utf-8").strip()

    if mode == "clean_plus_improved" and clean_already_done:
        mode_md += (
            "\nNOTE: The input has already been deterministically cleaned. "
            'You may leave `"clean"` empty (`""`) and focus on generating `"enhanced"`.'
        )

    examples = _get_few_shots(mode, input_type)
    example_lines = ["FEW-SHOT EXAMPLES:"]
    for idx, (u_ex, a_ex) in enumerate(examples, start=1):
        example_lines.append(f"Example {idx} User:\n{u_ex}\nExample {idx} Assistant:\n{a_ex}")

    return "\n\n".join([base_md, _CONTEXT_LINE[input_type], mode_md, "\n\n".join(example_lines)])


def build_user_message(
    masked_text: str,
    input_type: InputType = "typed",
    vocab_hints: list[str] | None = None,
    repair_error: str | None = None,
) -> str:
    """
    Format the user message with `<input_text>...</input_text>` (or `<transcript>...</transcript>`)
    followed by `<vocabulary>...</vocabulary>` hints (§4.9).
    """
    tag = _INPUT_TAG[input_type]
    parts = [f"<{tag}>{masked_text}</{tag}>"]
    if vocab_hints:
        parts.append(f"<vocabulary>{', '.join(vocab_hints[:30])}</vocabulary>")
    if repair_error:
        parts.append(
            f"<validation_error>Your previous JSON failed validation: {repair_error}. "
            "Output ONLY valid JSON matching the required schema.</validation_error>"
        )
    return "\n".join(parts)