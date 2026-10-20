"""
Native Win32 `RegisterHotKey` message loop on a dedicated background thread,
with `pynput.keyboard.GlobalHotKeys` fallback (Task 1.1).
Consumes the shortcut at the OS level with 0% idle CPU.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from textprompt.config import HotkeysConfig
from textprompt.keys import NAMED_VK_MAP, VK_CONTROL, VK_LWIN, VK_MENU, VK_SHIFT, parse_shortcut_vks

logger = logging.getLogger(__name__)

IS_WINDOWS: Final[bool] = sys.platform == "win32"

MOD_ALT: Final[int] = 0x0001
MOD_CONTROL: Final[int] = 0x0002
MOD_SHIFT: Final[int] = 0x0004
MOD_WIN: Final[int] = 0x0008
MOD_NOREPEAT: Final[int] = 0x4000

WM_HOTKEY: Final[int] = 0x0312
WM_QUIT: Final[int] = 0x0012


@dataclass(frozen=True)
class HotkeyEvent:
    action: str          # "run_active" | "mode_1" | "mode_2" | "mode_3" | "cycle_mode" | "undo_last"
    mode: str | None     # "clean_only" | "clean_plus_improved" | "structured_json" | None
    timestamp: float


def shortcut_to_win32_modifiers_and_vk(shortcut: str) -> tuple[int, int]:
    """Convert `'ctrl+alt+space'` into `(MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE)`."""
    mod_vks, primary_vk = parse_shortcut_vks(shortcut)
    win_mods = MOD_NOREPEAT
    for m_vk in mod_vks:
        if m_vk == VK_CONTROL:
            win_mods |= MOD_CONTROL
        elif m_vk == VK_MENU:
            win_mods |= MOD_ALT
        elif m_vk == VK_SHIFT:
            win_mods |= MOD_SHIFT
        elif m_vk == VK_LWIN:
            win_mods |= MOD_WIN
    return win_mods, primary_vk


def shortcut_to_pynput_format(shortcut: str) -> str:
    """Convert `'ctrl+alt+space'` into `'<ctrl>+<alt>+<space>'` for pynput fallback."""
    parts = [p.strip().lower() for p in shortcut.split("+") if p.strip()]
    formatted: list[str] = []
    for p in parts:
        if len(p) == 1 and ("a" <= p <= "z" or "0" <= p <= "9"):
            formatted.append(p)
        elif p in NAMED_VK_MAP:
            canon = "ctrl" if p == "control" else ("cmd" if p in ("win", "super") else p)
            formatted.append(f"<{canon}>")
        else:
            formatted.append(p)
    return "+".join(formatted)


class HotkeyManager:
    """
    Manages global shortcuts using Win32 `RegisterHotKey` on a dedicated message-loop thread,
    falling back to `pynput` for any shortcut that fails Win32 registration.
    """

    def __init__(
        self,
        hotkeys_cfg: HotkeysConfig,
        on_trigger: Callable[[HotkeyEvent], None],
        on_warning: Callable[[str], None] | None = None,
    ) -> None:
        self.hotkeys_cfg = hotkeys_cfg
        self.on_trigger = on_trigger
        self.on_warning = on_warning

        self._paused = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._pynput_listener: Any = None
        self._lock = threading.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        logger.info("Global hotkeys %s.", "paused" if paused else "resumed")

    def _action_table(self) -> list[tuple[int, str, str | None, str]]:
        return [
            (1, "run_active", None, self.hotkeys_cfg.run_active),
            (2, "mode_1", "clean_only", self.hotkeys_cfg.mode_1),
            (3, "mode_2", "clean_plus_improved", self.hotkeys_cfg.mode_2),
            (4, "mode_3", "structured_json", self.hotkeys_cfg.mode_3),
            (5, "cycle_mode", None, self.hotkeys_cfg.cycle_mode),
            (6, "undo_last", None, self.hotkeys_cfg.undo_last),
        ]

    def _dispatch(self, action: str, mode: str | None) -> None:
        if self._paused:
            return
        event = HotkeyEvent(action=action, mode=mode, timestamp=time.perf_counter())
        try:
            self.on_trigger(event)
        except Exception as exc:
            logger.error("Error in hotkey trigger callback: %s", exc)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="win32-hotkey-loop",
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if IS_WINDOWS and self._thread_id is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    def reload(self, new_cfg: HotkeysConfig) -> None:
        """Re-register shortcuts when `config.yaml` is hot-reloaded."""
        self.stop()
        self.hotkeys_cfg = new_cfg
        self.start()

    def _run_loop(self) -> None:
        if not IS_WINDOWS:
            self._start_pynput_fallback(self._action_table())
            return

        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())

        registered_ids: dict[int, tuple[str, str | None]] = {}
        failed_entries: list[tuple[int, str, str | None, str]] = []

        for hk_id, action, mode, shortcut_str in self._action_table():
            try:
                win_mods, vk = shortcut_to_win32_modifiers_and_vk(shortcut_str)
                ok = bool(user32.RegisterHotKey(None, hk_id, win_mods, vk))
                if ok:
                    registered_ids[hk_id] = (action, mode)
                    logger.debug("Registered Win32 hotkey #%d (%s -> %s)", hk_id, shortcut_str, action)
                else:
                    failed_entries.append((hk_id, action, mode, shortcut_str))
            except Exception as exc:
                logger.warning("Invalid shortcut %r for %s: %s", shortcut_str, action, exc)

        if failed_entries:
            failed_names = ", ".join(f"{s} ({a})" for _, a, _, s in failed_entries)
            warn_msg = f"Shortcut conflict on {failed_names}; using fallback listener"
            logger.warning(warn_msg)
            if self.on_warning:
                try:
                    self.on_warning(warn_msg)
                except Exception:
                    pass
            self._start_pynput_fallback(failed_entries)

        msg = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0 or msg.message == WM_QUIT:
                    break
                if msg.message == WM_HOTKEY:
                    hk_id = int(msg.wParam)
                    target = registered_ids.get(hk_id)
                    if target is not None:
                        self._dispatch(target[0], target[1])
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hk_id in registered_ids:
                try:
                    user32.UnregisterHotKey(None, hk_id)
                except Exception:
                    pass

    def _start_pynput_fallback(self, entries: list[tuple[int, str, str | None, str]]) -> None:
        try:
            from pynput import keyboard

            mapping: dict[str, Callable[[], None]] = {}
            for _, action, mode, shortcut_str in entries:
                pynput_str = shortcut_to_pynput_format(shortcut_str)
                mapping[pynput_str] = lambda a=action, m=mode: self._dispatch(a, m)

            if mapping:
                self._pynput_listener = keyboard.GlobalHotKeys(mapping)
                self._pynput_listener.daemon = True
                self._pynput_listener.start()
        except Exception as exc:
            logger.error("Failed to start pynput fallback listener: %s", exc)