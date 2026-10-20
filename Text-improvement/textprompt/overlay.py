"""
Non-activating floating status pill (`tkinter` + Win32 `WS_EX_NOACTIVATE | WS_EX_TRANSPARENT`)
per Task 5.3.
- Never steals keyboard focus or clears the active selection in the target app.
- Shows near the active caret (`GetGUIThreadInfo`) or at bottom-center of the screen.
- For in-progress states ("Cleaning…"), only appears if processing takes > 150 ms.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

IS_WINDOWS: Final[bool] = sys.platform == "win32"

GWL_EXSTYLE: Final[int] = -20
WS_EX_LAYERED: Final[int] = 0x00080000
WS_EX_TRANSPARENT: Final[int] = 0x00000020
WS_EX_TOOLWINDOW: Final[int] = 0x00000080
WS_EX_TOPMOST: Final[int] = 0x00000008
WS_EX_NOACTIVATE: Final[int] = 0x08000000
SW_SHOWNOACTIVATE: Final[int] = 4
SW_HIDE: Final[int] = 0


if IS_WINDOWS:
    from ctypes import wintypes

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]


def get_caret_screen_position() -> tuple[int, int] | None:
    """Return `(x, y)` screen coordinates of the active caret if available via `GetGUIThreadInfo`."""
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not user32.GetGUIThreadInfo(0, ctypes.byref(info)):
            return None
        if not info.hwndCaret or (info.rcCaret.left == 0 and info.rcCaret.top == 0):
            return None
        pt = wintypes.POINT(info.rcCaret.left, info.rcCaret.bottom)
        if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(pt)):
            return None
        if pt.x <= 0 and pt.y <= 0:
            return None
        return int(pt.x), int(pt.y)
    except Exception:
        return None


@dataclass
class _OverlayCommand:
    kind: str  # "show_progress" | "show_toast" | "hide" | "quit"
    text: str = ""
    delay_ms: int = 0
    duration_ms: int = 1200
    accent_color: str = "#f59e0b"  # amber default


class StatusOverlay:
    """
    Thread-safe controller for the non-activating status pill overlay.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._cmd_queue: queue.Queue[_OverlayCommand] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._ui_loop, daemon=True, name="status-overlay")
        self._thread.start()

    def stop(self) -> None:
        self._cmd_queue.put(_OverlayCommand(kind="quit"))

    def show_progress(self, text: str, delay_ms: int = 150) -> None:
        """Show an in-progress pill only if `hide()` hasn't been called within `delay_ms` (Task 5.3)."""
        if not self.enabled:
            return
        self._cmd_queue.put(
            _OverlayCommand(
                kind="show_progress",
                text=text,
                delay_ms=delay_ms,
                accent_color="#f59e0b",
            )
        )

    def show_toast(self, text: str, duration_ms: int = 1300, color: str = "#10b981") -> None:
        """Show a brief non-activating notification pill (e.g. mode switch, fallback notice)."""
        if not self.enabled:
            return
        self._cmd_queue.put(
            _OverlayCommand(
                kind="show_toast",
                text=text,
                duration_ms=duration_ms,
                accent_color=color,
            )
        )

    def hide(self) -> None:
        if not self.enabled:
            return
        self._cmd_queue.put(_OverlayCommand(kind="hide"))

    def _ui_loop(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:
            logger.debug("Tkinter unavailable for overlay: %s", exc)
            return

        try:
            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.92)
            root.configure(bg="#18181b")

            frame = tk.Frame(root, bg="#18181b", padx=12, pady=6, highlightthickness=1, highlightbackground="#3f3f46")
            frame.pack(fill="both", expand=True)

            dot_label = tk.Label(frame, text="●", fg="#f59e0b", bg="#18181b", font=("Segoe UI", 9, "bold"))
            dot_label.pack(side="left", padx=(0, 6))

            msg_label = tk.Label(frame, text="", fg="#f4f4f5", bg="#18181b", font=("Segoe UI", 9))
            msg_label.pack(side="left")

            root.update_idletasks()
            hwnd = int(root.winfo_id())

            if IS_WINDOWS and hwnd:
                user32 = ctypes.windll.user32
                parent_hwnd = user32.GetParent(hwnd) or hwnd
                ex_style = user32.GetWindowLongW(parent_hwnd, GWL_EXSTYLE)
                new_style = (
                    ex_style
                    | WS_EX_NOACTIVATE
                    | WS_EX_TRANSPARENT
                    | WS_EX_TOOLWINDOW
                    | WS_EX_TOPMOST
                    | WS_EX_LAYERED
                )
                user32.SetWindowLongW(parent_hwnd, GWL_EXSTYLE, new_style)
            else:
                parent_hwnd = hwnd

            pending_after_id: str | None = None

            def _cancel_pending() -> None:
                nonlocal pending_after_id
                if pending_after_id is not None:
                    try:
                        root.after_cancel(pending_after_id)
                    except Exception:
                        pass
                    pending_after_id = None

            def _hide_window() -> None:
                _cancel_pending()
                if IS_WINDOWS and parent_hwnd:
                    ctypes.windll.user32.ShowWindow(parent_hwnd, SW_HIDE)
                else:
                    root.withdraw()

            def _display_now(text: str, accent: str, auto_hide_ms: int | None = None) -> None:
                nonlocal pending_after_id
                dot_label.configure(fg=accent)
                msg_label.configure(text=text)
                root.update_idletasks()
                w = root.winfo_reqwidth()
                h = root.winfo_reqheight()

                caret_pos = get_caret_screen_position()
                screen_w = root.winfo_screenwidth()
                screen_h = root.winfo_screenheight()
                if caret_pos is not None:
                    x = max(16, min(screen_w - w - 16, caret_pos[0]))
                    y = max(16, min(screen_h - h - 48, caret_pos[1] + 12))
                else:
                    x = (screen_w - w) // 2
                    y = screen_h - h - 72

                root.geometry(f"{w}x{h}+{x}+{y}")
                if IS_WINDOWS and parent_hwnd:
                    ctypes.windll.user32.ShowWindow(parent_hwnd, SW_SHOWNOACTIVATE)
                else:
                    root.deiconify()

                if auto_hide_ms is not None:
                    pending_after_id = root.after(auto_hide_ms, _hide_window)

            def _poll_queue() -> None:
                nonlocal pending_after_id
                try:
                    while True:
                        cmd = self._cmd_queue.get_nowait()
                        if cmd.kind == "quit":
                            root.destroy()
                            return
                        if cmd.kind == "hide":
                            _hide_window()
                        elif cmd.kind == "show_progress":
                            _cancel_pending()
                            if cmd.delay_ms > 0:
                                pending_after_id = root.after(
                                    cmd.delay_ms,
                                    lambda t=cmd.text, c=cmd.accent_color: _display_now(t, c, auto_hide_ms=6000),
                                )
                            else:
                                _display_now(cmd.text, cmd.accent_color, auto_hide_ms=6000)
                        elif cmd.kind == "show_toast":
                            _cancel_pending()
                            _display_now(cmd.text, cmd.accent_color, auto_hide_ms=cmd.duration_ms)
                except queue.Empty:
                    pass
                root.after(25, _poll_queue)

            root.after(25, _poll_queue)
            root.mainloop()
        except Exception as exc:
            logger.debug("Overlay UI loop exited: %s", exc)