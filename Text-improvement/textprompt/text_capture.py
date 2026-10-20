"""
Selection capture and multi-format clipboard backup (Task 1.2, Task 6.1).
Uses `GetClipboardSequenceNumber()` polling every 5 ms after `SendInput(Ctrl+C)`
so copy detection takes 10-40 ms instead of a fixed sleep.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Final

import psutil

from textprompt.config import AppSettings
from textprompt.keys import release_modifiers, send_shortcut

logger = logging.getLogger(__name__)

IS_WINDOWS: Final[bool] = sys.platform == "win32"
CF_UNICODETEXT: Final[int] = 13

# Clipboard formats that cannot or should not be raw-copied via GetClipboardData/SetClipboardData bytes
_SKIP_RAW_FORMATS: Final[frozenset[int]] = frozenset(
    {
        2,   # CF_BITMAP (GDI handle)
        3,   # CF_METAFILEPICT (handle)
        9,   # CF_PALETTE (GDI handle)
        14,  # CF_ENHMETAFILE (handle)
    }
)

if IS_WINDOWS:
    import win32clipboard
    import win32gui
    import win32process


@dataclass
class ClipboardBackup:
    """Stores backed-up clipboard formats so user data (including images/HTML/RTF) is never lost."""

    formats: dict[int, Any] = field(default_factory=dict)
    text_only: bool = False
    seq_after_paste: int | None = None


@dataclass
class CaptureResult:
    """Returned by `capture_selection()`."""

    text: str
    backup: ClipboardBackup
    hwnd: int
    process_name: str
    local_only: bool = False
    is_elevated: bool = False
    truncated: bool = False
    empty: bool = False
    capture_ms: float = 0.0


def get_clipboard_sequence_number() -> int:
    """Return Win32 `GetClipboardSequenceNumber()` (increments on every clipboard change)."""
    if not IS_WINDOWS:
        return 0
    return int(ctypes.windll.user32.GetClipboardSequenceNumber())


def _is_process_elevated(pid: int) -> bool:
    """Return True if target PID is running elevated and we cannot access/inject into it (Task 6.1)."""
    if not IS_WINDOWS or pid <= 0:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32

    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h_proc:
        return True  # Access denied usually indicates an elevated/system process

    try:
        h_token = ctypes.c_void_p()
        if not advapi32.OpenProcessToken(h_proc, TOKEN_QUERY, ctypes.byref(h_token)):
            return False
        try:
            elevation = ctypes.c_ulong(0)
            ret_len = ctypes.c_ulong(0)
            res = advapi32.GetTokenInformation(
                h_token,
                TokenElevation,
                ctypes.byref(elevation),
                ctypes.sizeof(elevation),
                ctypes.byref(ret_len),
            )
            if res:
                return bool(elevation.value)
            return False
        finally:
            kernel32.CloseHandle(h_token)
    finally:
        kernel32.CloseHandle(h_proc)


def is_current_process_elevated() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_foreground_window_info() -> tuple[int, str, bool]:
    """
    Return `(hwnd, process_name, target_requires_admin)` for the active foreground window.
    """
    if not IS_WINDOWS:
        return 0, "unknown.exe", False
    try:
        hwnd = int(win32gui.GetForegroundWindow())
        if not hwnd:
            return 0, "unknown.exe", False
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = psutil.Process(pid).name() if pid > 0 else "unknown.exe"
        target_elevated = _is_process_elevated(pid) and not is_current_process_elevated()
        return hwnd, proc_name, target_elevated
    except Exception as exc:
        logger.debug("Could not inspect foreground window: %s", exc)
        return 0, "unknown.exe", False


def _open_clipboard_with_retry(retries: int = 10, delay_ms: int = 5) -> bool:
    """Retry `OpenClipboard` up to `retries` times with `delay_ms` sleep (Task 1.2)."""
    if not IS_WINDOWS:
        return False
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(delay_ms / 1000.0)
    return False


def backup_clipboard(mode: str = "all") -> ClipboardBackup:
    """Back up current clipboard contents before sending Ctrl+C."""
    backup = ClipboardBackup(text_only=(mode == "text_only"))
    if not IS_WINDOWS:
        return backup

    if not _open_clipboard_with_retry():
        return backup

    try:
        if mode == "text_only":
            if win32clipboard.IsClipboardFormatAvailable(CF_UNICODETEXT):
                try:
                    backup.formats[CF_UNICODETEXT] = win32clipboard.GetClipboardData(CF_UNICODETEXT)
                except Exception:
                    pass
            return backup

        fmt = 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            if fmt in _SKIP_RAW_FORMATS:
                continue
            try:
                data = win32clipboard.GetClipboardData(fmt)
                if isinstance(data, (str, bytes)):
                    backup.formats[fmt] = data
            except Exception:
                continue
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    return backup


def restore_clipboard(backup: ClipboardBackup) -> bool:
    """
    Restore backed-up clipboard formats. If `backup.seq_after_paste` is set and the current
    sequence number differs, the user copied something new since we pasted, so we skip restoring.
    """
    if not IS_WINDOWS:
        return True

    if backup.seq_after_paste is not None:
        current_seq = get_clipboard_sequence_number()
        if current_seq != backup.seq_after_paste:
            logger.debug("Skipping clipboard restore because user copied new data.")
            return False

    if not _open_clipboard_with_retry():
        return False

    try:
        win32clipboard.EmptyClipboard()
        for fmt, data in backup.formats.items():
            try:
                if fmt == CF_UNICODETEXT and isinstance(data, str):
                    win32clipboard.SetClipboardData(CF_UNICODETEXT, data)
                elif isinstance(data, (bytes, str)):
                    win32clipboard.SetClipboardData(fmt, data)
            except Exception as exc:
                logger.debug("Could not restore clipboard format %s: %s", fmt, exc)
        return True
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def read_clipboard_unicode_text() -> str:
    """Read `CF_UNICODETEXT` from the Windows clipboard."""
    if not IS_WINDOWS:
        return ""
    if not _open_clipboard_with_retry():
        return ""
    try:
        if win32clipboard.IsClipboardFormatAvailable(CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(CF_UNICODETEXT)
            return data if isinstance(data, str) else ""
        return ""
    except Exception:
        return ""
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def capture_selection(settings: AppSettings) -> CaptureResult:
    """
    Back up clipboard, release held modifiers, simulate Copy (`Ctrl+C` or `Ctrl+Shift+C`),
    poll `GetClipboardSequenceNumber()` every 5 ms up to `copy_timeout_ms`, and read text.
    """
    t0 = time.perf_counter()
    hwnd, proc_name, requires_admin = get_foreground_window_info()
    local_only = settings.is_app_excluded(proc_name)
    backup = backup_clipboard(mode=settings.capture.backup_formats)

    if requires_admin:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return CaptureResult(
            text="",
            backup=backup,
            hwnd=hwnd,
            process_name=proc_name,
            local_only=local_only,
            is_elevated=True,
            empty=True,
            capture_ms=elapsed,
        )

    seq0 = get_clipboard_sequence_number()
    release_modifiers(
        wait_for_release=settings.capture.wait_for_release,
        timeout_ms=settings.capture.copy_timeout_ms,
    )

    if settings.capture.auto_select_all:
        send_shortcut("ctrl+a")
        time.sleep(0.015)

    copy_shortcut = settings.capture.get_copy_shortcut(proc_name)
    send_shortcut(copy_shortcut)

    # Poll GetClipboardSequenceNumber every 5 ms up to copy_timeout_ms (Task 1.2)
    timeout_s = settings.capture.copy_timeout_ms / 1000.0
    deadline = time.perf_counter() + timeout_s
    copied = False
    while time.perf_counter() < deadline:
        if get_clipboard_sequence_number() != seq0:
            copied = True
            break
        time.sleep(0.005)

    if not copied:
        restore_clipboard(backup)
        elapsed = (time.perf_counter() - t0) * 1000.0
        return CaptureResult(
            text="",
            backup=backup,
            hwnd=hwnd,
            process_name=proc_name,
            local_only=local_only,
            empty=True,
            capture_ms=elapsed,
        )

    raw_text = read_clipboard_unicode_text()
    if not raw_text or not raw_text.strip():
        restore_clipboard(backup)
        elapsed = (time.perf_counter() - t0) * 1000.0
        return CaptureResult(
            text="",
            backup=backup,
            hwnd=hwnd,
            process_name=proc_name,
            local_only=local_only,
            empty=True,
            capture_ms=elapsed,
        )

    # Normalize CRLF -> LF internally for clean rule/LLM processing
    norm_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    truncated = False
    if len(norm_text) > settings.capture.max_chars:
        norm_text = norm_text[: settings.capture.max_chars]
        truncated = True

    elapsed = (time.perf_counter() - t0) * 1000.0
    return CaptureResult(
        text=norm_text,
        backup=backup,
        hwnd=hwnd,
        process_name=proc_name,
        local_only=local_only,
        truncated=truncated,
        empty=False,
        capture_ms=elapsed,
    )