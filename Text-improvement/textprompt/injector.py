"""
In-place selection replacement, delayed clipboard restoration, and 1-step Undo (Task 1.3).
Checks that the foreground `HWND` has not changed before pasting; if it changed, leaves
the refined output on the clipboard and notifies the user instead of pasting into the wrong window.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from textprompt.config import AppSettings
from textprompt.keys import release_modifiers, send_shortcut
from textprompt.text_capture import (
    CF_UNICODETEXT,
    ClipboardBackup,
    _open_clipboard_with_retry,
    backup_clipboard,
    get_clipboard_sequence_number,
    get_foreground_window_info,
    restore_clipboard,
)

logger = logging.getLogger(__name__)

IS_WINDOWS: Final[bool] = sys.platform == "win32"

if IS_WINDOWS:
    import win32clipboard


@dataclass
class UndoRecord:
    """Stores the original text and target window of the last replacement for `Ctrl+Alt+Z`."""

    original_text: str
    replaced_text: str
    hwnd: int
    process_name: str


class UndoManager:
    """Thread-safe holder for the last replacement so the user can undo with `Ctrl+Alt+Z`."""

    def __init__(self) -> None:
        self._last: UndoRecord | None = None
        self._lock = threading.Lock()

    def record(self, original_text: str, replaced_text: str, hwnd: int, process_name: str) -> None:
        with self._lock:
            self._last = UndoRecord(
                original_text=original_text,
                replaced_text=replaced_text,
                hwnd=hwnd,
                process_name=process_name,
            )

    def pop_last(self) -> UndoRecord | None:
        with self._lock:
            rec = self._last
            self._last = None
            return rec

    def has_undo(self) -> bool:
        with self._lock:
            return self._last is not None


def write_clipboard_unicode_text(text: str) -> bool:
    """Write `CF_UNICODETEXT` to the clipboard, normalizing `\\n` to `\\r\\n` on Windows."""
    if not IS_WINDOWS:
        return True
    win_text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    if not _open_clipboard_with_retry():
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(CF_UNICODETEXT, win_text)
        return True
    except Exception as exc:
        logger.error("Failed to write CF_UNICODETEXT to clipboard: %s", exc)
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def replace_selection(
    text: str,
    backup: ClipboardBackup,
    target_hwnd: int,
    process_name: str,
    settings: AppSettings,
    on_notice: Callable[[str], None] | None = None,
) -> tuple[bool, float]:
    """
    Write `text` to the clipboard, verify `target_hwnd` is still focused, send Paste
    (`Ctrl+V` or `Ctrl+Shift+V`), and schedule clipboard restoration after `restore_delay_ms`.
    Returns `(pasted_ok, paste_ms)`.
    """
    t0 = time.perf_counter()

    if not write_clipboard_unicode_text(text):
        restore_clipboard(backup)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return False, elapsed_ms

    # Verify foreground HWND has not changed while rules/LLM were running (Task 1.3 & 6.1)
    cur_hwnd, _, _ = get_foreground_window_info()
    if target_hwnd and cur_hwnd and cur_hwnd != target_hwnd:
        logger.info(
            "Foreground window changed (%s -> %s); leaving result on clipboard without pasting.",
            target_hwnd,
            cur_hwnd,
        )
        if on_notice:
            on_notice("Window changed — result copied to clipboard")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return False, elapsed_ms

    # Release any held modifiers and send paste keystroke
    release_modifiers(wait_for_release=settings.capture.wait_for_release)
    paste_shortcut = settings.paste.get_paste_shortcut(process_name)
    send_shortcut(paste_shortcut)

    backup.seq_after_paste = get_clipboard_sequence_number()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    if settings.paste.restore_clipboard:
        delay_s = settings.paste.restore_delay_ms / 1000.0
        timer = threading.Timer(delay_s, restore_clipboard, args=(backup,))
        timer.daemon = True
        timer.name = "clipboard-restore"
        timer.start()

    return True, elapsed_ms


def perform_undo(
    undo_mgr: UndoManager,
    settings: AppSettings,
    on_notice: Callable[[str], None] | None = None,
) -> bool:
    """Restore the previous text from `UndoManager` by pasting it into the active window."""
    record = undo_mgr.pop_last()
    if record is None:
        if on_notice:
            on_notice("Nothing to undo")
        return False

    cur_hwnd, proc_name, _ = get_foreground_window_info()
    backup = backup_clipboard(mode=settings.capture.backup_formats)
    ok, _ = replace_selection(
        text=record.original_text,
        backup=backup,
        target_hwnd=cur_hwnd,
        process_name=proc_name,
        settings=settings,
        on_notice=on_notice,
    )
    if ok and on_notice:
        on_notice("Undid last replacement")
    return ok