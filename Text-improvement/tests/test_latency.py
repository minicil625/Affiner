"""
Task 0.4 & Task 4.4 — Latency regression suite and Notepad `pywinauto` timing harness.
- Verifies Mode 1 fast-path p95 latency is well below 0.15 s (< 150 ms).
- Verifies evaluation suite KPIs from `run_eval.py` (150+ cases).
- Provides `run_notepad_baseline()` (`python -m tests.test_latency --notepad`) using `pywinauto`
  to benchmark 20 real capture -> stub/process -> replace runs in Windows Notepad (Task 0.4).
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
from pathlib import Path

import pytest

from promptcore.config import CoreSettings
from promptcore.prompt_engine import PromptEngine
from textprompt.config import AppSettings
from textprompt.typo import TypoEngine


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
    return s[idx]


def test_mode1_fast_path_p95_latency_under_150ms() -> None:
    """
    Task 4.4 AC: Fast-path processing p95 < 0.15 s (150 ms).
    """
    vocab_path = Path(__file__).resolve().parent.parent / "vocabulary.txt"
    shorthand_path = Path(__file__).resolve().parent.parent / "shorthand.txt"
    typo_engine = TypoEngine(lazy_load=False)
    engine = PromptEngine(
        settings=CoreSettings(),
        input_type="typed",
        vocab_path=vocab_path,
        shorthand_path=shorthand_path,
        typo_hook=typo_engine.process_masked_text,
    )

    samples = [
        "um pls fix teh bug in app dot py thx",
        "git checkout dash b feature slash login",
        "use sqlite wait actually postgres",
        "setup fastapi w/ pydantic and nextjs",
        "pls check why teh databse conection fails in `db.py` rn",
        "can u update the authantication middleware w/ jwt validation thx",
        "check https://api.groq.com/openai/v1/models and update `providers.py`",
        "Refactor `get_user_by_id` to use async connection pooling with `asyncpg`.",
        "run pytest dash q double dash maxfail",
        "upgrade from v1.1.0 to v2.0.0-rc1 in pyproject dot toml",
    ]

    latencies_s: list[float] = []
    for i in range(20):
        engine.cache.clear()
        sample = samples[i % len(samples)]
        t0 = time.perf_counter()
        res = engine.process(sample, mode="clean_only")
        elapsed = time.perf_counter() - t0
        assert res.path == "fast"
        latencies_s.append(elapsed)

    p50_s = statistics.median(latencies_s)
    p95_s = _p95(latencies_s)
    assert p95_s < 0.15, f"Expected fast-path p95 < 0.15s, got p50={p50_s:.4f}s p95={p95_s:.4f}s"


def test_eval_suite_meets_all_phase4_accuracy_kpis(tmp_path: Path) -> None:
    """
    Task 4.3 AC: Run the 150+ case evaluation suite and verify:
      - Mode 1 normalized accuracy >= 90%
      - Over-edit rate <= 5%
      - Answered rate == 0%
      - Placeholder integrity == 100%
      - Mode 3 validity == 100%
      - Fast-path hit rate >= 60%
    """
    from scripts.run_eval import run_evaluation_suite

    typo_engine = TypoEngine(lazy_load=False)
    summary = run_evaluation_suite(
        live=False,
        typo_hook=typo_engine.process_masked_text,
        usage_dir=tmp_path,
    )

    assert summary.total_cases >= 150
    assert (summary.fast_path_hits / summary.fast_path_eligible) >= 0.60
    assert summary.mode1_avg_similarity >= 0.90
    assert summary.over_edit_rate <= 0.05
    assert summary.answered_rate == 0.0
    assert summary.placeholder_integrity_rate == 1.0
    assert summary.mode3_valid_rate == 1.0


@pytest.mark.skipif(os.environ.get("RUN_NOTEPAD_BENCH") != "1", reason="Set RUN_NOTEPAD_BENCH=1 on Windows desktop")
def test_live_notepad_capture_replace_smoke() -> None:
    stats = run_notepad_baseline(runs=20)
    assert stats["p95_s"] < 0.20


def run_notepad_baseline(runs: int = 20) -> dict[str, float]:
    """
    Task 0.4 — Automated `pywinauto` Notepad harness measuring 20 real
    `capture_selection -> stub -> replace_selection` cycles on Windows.
    """
    from pywinauto.application import Application

    from textprompt.injector import replace_selection
    from textprompt.text_capture import capture_selection

    settings = AppSettings()
    settings.capture.auto_select_all = True
    settings.paste.restore_clipboard = False

    app = Application(backend="win32").start("notepad.exe")
    win = app.top_window()
    win.wait("visible", timeout=5.0)
    edit = win.Edit

    timings_s: list[float] = []
    try:
        for i in range(runs):
            sample = f"um pls fix teh bug in app dot py run {i} thx"
            edit.set_edit_text(sample)
            win.set_focus()
            time.sleep(0.03)

            t0 = time.perf_counter()
            cap = capture_selection(settings)
            stub_out = cap.text.upper() if cap.text else sample.upper()
            replace_selection(
                text=stub_out,
                backup=cap.backup,
                target_hwnd=cap.hwnd,
                process_name=cap.process_name,
                settings=settings,
            )
            timings_s.append(time.perf_counter() - t0)
            time.sleep(0.04)
    finally:
        try:
            app.kill()
        except Exception:
            pass

    p50_s = statistics.median(timings_s) if timings_s else 0.0
    p95_s = _p95(timings_s)
    print(f"[Notepad Baseline ({runs} runs)] p50={p50_s * 1000:.1f} ms | p95={p95_s * 1000:.1f} ms")
    return {"p50_s": p50_s, "p95_s": p95_s}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TextPrompt Latency & Notepad Baseline Harness")
    parser.add_argument("--notepad", action="store_true", help="Run 20 capture-replace cycles in Notepad")
    parser.add_argument("--runs", type=int, default=20, help="Number of Notepad iterations")
    args = parser.parse_args()
    if args.notepad:
        run_notepad_baseline(runs=args.runs)