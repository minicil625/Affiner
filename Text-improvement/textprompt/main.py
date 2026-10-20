"""
`textprompt/main.py` — Main entry point and background orchestrator (Task 1.4, Task 3.5, Task 6.2).
Architecture:
  - Named Win32 single-instance mutex (`Global\\TextPromptAI_SingleInstance_Mutex`)
  - Rotating log in `%APPDATA%\\TextPrompt\\logs\\textprompt.log` with automatic API key redaction
  - Hotkey thread (`RegisterHotKey`) -> `queue.Queue` -> 1 worker thread (capture -> process -> replace)
  - Live `watchdog` hot-reloading of `config.yaml`, `vocabulary.txt`, `shorthand.txt`, and `providers.yaml`
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from promptcore.prompt_engine import ProcessResult, PromptEngine
from promptcore.prompts.builder import ModeName
from promptcore.rules.shorthand import load_shorthand_file
from promptcore.secrets import redact_key
from promptcore.vocab import Vocabulary
from textprompt.config import (
    AppSettings,
    ConfigWatcher,
    get_project_root,
    get_textprompt_appdata_dir,
    load_app_settings,
    save_config_field,
)
from textprompt.hotkeys import HotkeyEvent, HotkeyManager
from textprompt.injector import UndoManager, perform_undo, replace_selection
from textprompt.overlay import StatusOverlay
from textprompt.sounds import SoundPlayer
from textprompt.text_capture import capture_selection
from textprompt.tray_app import TrayApp
from textprompt.typo import TypoEngine

logger = logging.getLogger("textprompt")

IS_WINDOWS: Final[bool] = sys.platform == "win32"
MUTEX_NAME: Final[str] = "Global\\TextPromptAI_SingleInstance_Mutex"
ERROR_ALREADY_EXISTS: Final[int] = 183

MODE_CYCLE_ORDER: Final[tuple[ModeName, ...]] = (
    "clean_only",
    "clean_plus_improved",
    "structured_json",
)

MODE_LABELS: Final[dict[ModeName, str]] = {
    "clean_only": "Mode 1: Clean Only",
    "clean_plus_improved": "Mode 2: Clean + Improved",
    "structured_json": "Mode 3: Structured JSON",
}

MODE_PROGRESS_TEXT: Final[dict[ModeName, str]] = {
    "clean_only": "Cleaning…",
    "clean_plus_improved": "Cleaning & Improving…",
    "structured_json": "Structuring JSON…",
}


class KeyRedactingFormatter(logging.Formatter):
    """Ensures no API key is ever written to disk logs (Task 6.2)."""

    def format(self, record: logging.LogRecord) -> str:
        raw = super().format(record)
        return redact_key(raw)


def setup_logging() -> Path:
    log_dir = get_textprompt_appdata_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "textprompt.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    fmt = KeyRedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    return log_file


class SingleInstanceLock:
    """Acquires a named Win32 mutex so only one instance of TextPrompt AI runs at a time."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        if not IS_WINDOWS:
            return True
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return True
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = int(handle)
        return True

    def release(self) -> None:
        if IS_WINDOWS and self._handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


class TextPromptOrchestrator:
    """
    Coordinates hotkeys, selection capture, `PromptEngine`, clipboard injection,
    tray state, overlay pill, and live config hot-reload.
    """

    def __init__(self, project_dir: Path | None = None) -> None:
        self.project_dir = project_dir or get_project_root()
        self.config_path = self.project_dir / "config.yaml"
        self.vocab_path = self.project_dir / "vocabulary.txt"
        self.shorthand_path = self.project_dir / "shorthand.txt"
        self.log_path = setup_logging()

        self.settings: AppSettings = load_app_settings(self.config_path)
        self.active_mode: ModeName = self.settings.modes.default
        self._last_telemetry: dict[str, str] = {}

        self.overlay = StatusOverlay(enabled=self.settings.ui.overlay)
        self.sounds = SoundPlayer(
            enabled=self.settings.ui.sounds,
            done_sound=self.settings.ui.done_sound,
        )
        self.undo_mgr = UndoManager()

        vocab = Vocabulary.from_file(self.vocab_path)
        shorthand_keys = set(load_shorthand_file(self.shorthand_path).keys())
        pickle_cache = get_textprompt_appdata_dir() / "symspell.pkl"
        self.typo_engine = TypoEngine(
            vocab=vocab,
            shorthand_keys=shorthand_keys,
            enabled=self.settings.typo.enabled,
            lazy_load=True,
            pickle_cache_path=pickle_cache,
        )

        self.engine = PromptEngine(
            settings=self.settings,
            input_type="typed",
            vocab_path=self.vocab_path,
            shorthand_path=self.shorthand_path,
            typo_hook=self.typo_engine.process_masked_text if self.settings.typo.enabled else None,
            on_notice=self._notify_user,
        )

        self._work_queue: queue.Queue[HotkeyEvent] = queue.Queue()
        self._busy_lock = threading.Lock()
        self._is_busy = False
        self._stop_event = threading.Event()

        self.hotkeys = HotkeyManager(
            hotkeys_cfg=self.settings.hotkeys,
            on_trigger=self._on_hotkey_pressed,
            on_warning=self._notify_user,
        )
        self.tray = TrayApp(
            get_active_mode=lambda: self.active_mode,
            set_active_mode=self.set_active_mode,
            get_last_telemetry=lambda: dict(self._last_telemetry),
            get_daily_usage=self.engine.router.rate_limiter.get_daily_summary,
            get_privacy_flags=lambda: (
                self.settings.privacy.allow_cloud_text,
                self.settings.privacy.exclude_training_providers,
            ),
            toggle_allow_cloud=self._toggle_allow_cloud,
            toggle_exclude_training=self._toggle_exclude_training,
            is_paused=lambda: self.hotkeys.paused,
            toggle_pause=lambda: self.hotkeys.set_paused(not self.hotkeys.paused),
            on_undo=lambda: perform_undo(self.undo_mgr, self.settings, on_notice=self._notify_user),
            on_reload=self.reload_all,
            on_quit=self.stop,
            config_path=self.config_path,
            vocab_path=self.vocab_path,
            log_path=self.log_path,
        )
        self.watcher = ConfigWatcher(
            project_dir=self.project_dir,
            on_file_changed=self._on_watched_file_changed,
        )

    def _notify_user(self, message: str, color: str = "#f59e0b") -> None:
        logger.info("Notice: %s", message)
        self.overlay.show_toast(message, duration_ms=1500, color=color)

    def set_active_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            return
        self.active_mode = mode  # type: ignore[assignment]
        self.settings.modes.default = self.active_mode
        save_config_field(self.config_path, "modes", "default", self.active_mode)
        self.sounds.play_mode_switch(self.active_mode)
        self.overlay.show_toast(MODE_LABELS[self.active_mode], duration_ms=1100, color="#3b82f6")
        logger.info("Active mode switched to: %s", self.active_mode)

    def cycle_active_mode(self) -> ModeName:
        idx = MODE_CYCLE_ORDER.index(self.active_mode)
        next_mode = MODE_CYCLE_ORDER[(idx + 1) % len(MODE_CYCLE_ORDER)]
        self.set_active_mode(next_mode)
        return next_mode

    def _toggle_allow_cloud(self) -> None:
        new_val = not self.settings.privacy.allow_cloud_text
        self.settings.privacy.allow_cloud_text = new_val
        save_config_field(self.config_path, "privacy", "allow_cloud_text", new_val)
        self.tray.set_state("idle" if new_val else "offline")
        self._notify_user(
            "Cloud LLMs enabled" if new_val else "Local rules-only mode active",
            color="#10b981" if new_val else "#3b82f6",
        )

    def _toggle_exclude_training(self) -> None:
        new_val = not self.settings.privacy.exclude_training_providers
        self.settings.privacy.exclude_training_providers = new_val
        save_config_field(self.config_path, "privacy", "exclude_training_providers", new_val)
        self._notify_user(
            "Excluded training providers" if new_val else "All enabled providers allowed",
            color="#3b82f6",
        )

    def _on_watched_file_changed(self, changed_path: Path) -> None:
        fname = changed_path.name.lower()
        logger.info("Detected change in %s; hot-reloading...", fname)
        self.reload_all()

    def reload_all(self) -> None:
        """Hot-reload `config.yaml`, `vocabulary.txt`, `shorthand.txt`, and `providers.yaml`."""
        new_settings = load_app_settings(
            self.config_path,
            on_error=lambda msg: self._notify_user(msg, color="#ef4444"),
        )
        old_hotkeys = self.settings.hotkeys
        self.settings = new_settings
        self.engine.settings = new_settings
        self.engine.router.settings = new_settings

        vocab = Vocabulary.from_file(self.vocab_path)
        shorthand_map = load_shorthand_file(self.shorthand_path)
        self.engine.pipeline.vocab = vocab
        self.engine.pipeline.shorthand_map = shorthand_map
        self.typo_engine.enabled = new_settings.typo.enabled
        self.typo_engine.refresh_vocabulary(vocab)
        self.engine.typo_hook = (
            self.typo_engine.process_masked_text if new_settings.typo.enabled else None
        )

        self.overlay.enabled = new_settings.ui.overlay
        self.sounds.enabled = new_settings.ui.sounds
        self.sounds.done_sound = new_settings.ui.done_sound

        if new_settings.hotkeys != old_hotkeys:
            self.hotkeys.reload(new_settings.hotkeys)

        self.engine.cache.clear()
        logger.info("Hot-reload complete.")

    def _on_hotkey_pressed(self, event: HotkeyEvent) -> None:
        # Handle instant UI actions without queueing behind a capture worker
        if event.action == "cycle_mode":
            self.cycle_active_mode()
            return
        if event.action == "undo_last":
            perform_undo(self.undo_mgr, self.settings, on_notice=self._notify_user)
            return

        target_mode: ModeName = (event.mode or self.active_mode)  # type: ignore[assignment]
        # Pre-warm primary provider on hotkey press (§4.7, Task 1.1)
        self.engine.prewarm(target_mode)

        with self._busy_lock:
            if self._is_busy and self.settings.ui.busy_behavior == "ignore":
                self.overlay.show_toast("Busy refining…", duration_ms=700, color="#f59e0b")
                return

        self._work_queue.put(
            HotkeyEvent(action=event.action, mode=target_mode, timestamp=event.timestamp)
        )

    def handle_trigger_once(self, mode: ModeName) -> ProcessResult | None:
        """
        Execute a single `capture -> process -> replace` cycle on the worker thread.
        """
        t_trigger = time.perf_counter()
        cap = capture_selection(self.settings)

        if cap.is_elevated:
            self.tray.set_state("error", flash_back_ms=1200)
            self.sounds.play_error()
            self._notify_user("Run TextPrompt as admin to use in this window", color="#ef4444")
            return None

        if cap.empty:
            self._notify_user("No text selected", color="#f59e0b")
            return None

        if self.settings.debug_log_text:
            logger.info("Captured text (%d chars): %r", len(cap.text), cap.text)

        self.tray.set_state("refining")
        self.overlay.show_progress(MODE_PROGRESS_TEXT.get(mode, "Cleaning…"), delay_ms=150)

        try:
            proc_res = self.engine.process(
                cap.text,
                mode=mode,
                local_only=cap.local_only,
            )
        finally:
            self.overlay.hide()

        pasted_ok, paste_ms = replace_selection(
            text=proc_res.output,
            backup=cap.backup,
            target_hwnd=cap.hwnd,
            process_name=cap.process_name,
            settings=self.settings,
            on_notice=self._notify_user,
        )

        if pasted_ok:
            self.undo_mgr.record(
                original_text=cap.text,
                replaced_text=proc_res.output,
                hwnd=cap.hwnd,
                process_name=cap.process_name,
            )
            if proc_res.path == "fallback":
                self.tray.set_state("offline", flash_back_ms=1500)
            else:
                self.tray.set_state("done", flash_back_ms=900)
            self.sounds.play_done()

        if proc_res.notice:
            self._notify_user(proc_res.notice, color="#3b82f6")

        total_s = time.perf_counter() - t_trigger
        capture_s = cap.capture_ms / 1000.0
        rules_s = proc_res.timings.get("rules_s", 0.0)
        llm_s = proc_res.timings.get("llm_s", 0.0)
        paste_s = paste_ms / 1000.0

        self._last_telemetry[mode] = (
            f"{proc_res.provider}/{proc_res.model} ({proc_res.path}, {total_s * 1000:.0f} ms)"
        )

        # Exact Task 1.4 per-trigger timing log
        logger.info(
            "capture=%.3fs rules=%.3fs llm=%.3fs path=%s provider=%s paste=%.3fs total=%.3fs mode=%s app=%s",
            capture_s,
            rules_s,
            llm_s,
            proc_res.path,
            proc_res.provider,
            paste_s,
            total_s,
            mode,
            cap.process_name,
        )
        return proc_res

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                event = self._work_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            with self._busy_lock:
                self._is_busy = True
            try:
                target_mode: ModeName = (event.mode or self.active_mode)  # type: ignore[assignment]
                self.handle_trigger_once(target_mode)
            except Exception as exc:
                logger.exception("Unhandled error during trigger execution: %s", exc)
                self.tray.set_state("error", flash_back_ms=1200)
                self.sounds.play_error()
            finally:
                with self._busy_lock:
                    self._is_busy = False
                self._work_queue.task_done()

    def run(self) -> None:
        logger.info("Starting TextPrompt AI v1.1.0 (active_mode=%s)...", self.active_mode)
        self.overlay.start()
        self.tray.start()
        self.watcher.start()
        self.hotkeys.start()

        # Warm provider connections in background at startup (§4.7)
        threading.Thread(target=self.engine.warmup, daemon=True, name="startup-warmup").start()

        worker = threading.Thread(target=self._worker_loop, daemon=True, name="capture-worker")
        worker.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(0.25)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received; shutting down.")
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self.hotkeys.stop()
        self.watcher.stop()
        self.overlay.stop()
        self.tray.stop()
        self.engine.conn_manager.close_all()
        logger.info("TextPrompt AI stopped cleanly.")


def main() -> int:
    parser = argparse.ArgumentParser(description="TextPrompt AI (Cloud Free-Tier Edition)")
    parser.add_argument(
        "--once",
        choices=["clean_only", "clean_plus_improved", "structured_json"],
        help="Run a single capture-process-replace cycle and exit",
    )
    args = parser.parse_args()

    lock = SingleInstanceLock()
    if not lock.acquire():
        print("TextPrompt AI is already running in the system tray.", file=sys.stderr)
        return 1

    try:
        app = TextPromptOrchestrator()
        if args.once:
            app.handle_trigger_once(args.once)  # type: ignore[arg-type]
            return 0
        app.run()
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())