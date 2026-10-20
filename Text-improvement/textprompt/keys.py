"""
Win32 `SendInput` keystroke simulation and modifier release helpers (Task 1.2).
Ensures held `Ctrl+Alt` modifiers from the trigger shortcut are cleanly released
before simulating `Ctrl+C`, `Ctrl+Shift+C`, `Ctrl+V`, or `Ctrl+Shift+V`.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Final

IS_WINDOWS: Final[bool] = sys.platform == "win32"

# Virtual-Key Codes
VK_SHIFT: Final[int] = 0x10
VK_CONTROL: Final[int] = 0x11
VK_MENU: Final[int] = 0x12      # Alt
VK_LWIN: Final[int] = 0x5B
VK_RWIN: Final[int] = 0x5C
VK_LSHIFT: Final[int] = 0xA0
VK_RSHIFT: Final[int] = 0xA1
VK_LCONTROL: Final[int] = 0xA2
VK_RCONTROL: Final[int] = 0xA3
VK_LMENU: Final[int] = 0xA4
VK_RMENU: Final[int] = 0xA5

VK_SPACE: Final[int] = 0x20
VK_RETURN: Final[int] = 0x0D
VK_TAB: Final[int] = 0x09
VK_BACK: Final[int] = 0x08
VK_ESCAPE: Final[int] = 0x1B
VK_INSERT: Final[int] = 0x2D
VK_DELETE: Final[int] = 0x2E

INPUT_KEYBOARD: Final[int] = 1
KEYEVENTF_KEYUP: Final[int] = 0x0002

MODIFIER_VKS: Final[tuple[int, ...]] = (
    VK_CONTROL,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_MENU,
    VK_LMENU,
    VK_RMENU,
    VK_SHIFT,
    VK_LSHIFT,
    VK_RSHIFT,
    VK_LWIN,
    VK_RWIN,
)

NAMED_VK_MAP: Final[dict[str, int]] = {
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "shift": VK_SHIFT,
    "win": VK_LWIN,
    "super": VK_LWIN,
    "space": VK_SPACE,
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "tab": VK_TAB,
    "backspace": VK_BACK,
    "esc": VK_ESCAPE,
    "escape": VK_ESCAPE,
    "ins": VK_INSERT,
    "insert": VK_INSERT,
    "del": VK_DELETE,
    "delete": VK_DELETE,
}

# Add F1..F12
for _f_idx in range(1, 13):
    NAMED_VK_MAP[f"f{_f_idx}"] = 0x6F + _f_idx


if IS_WINDOWS:
    ULONG_PTR = ctypes.WINFUNCTYPE(ctypes.c_ulong) if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_uint64

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_short),
            ("wParamH", ctypes.c_short),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_ulong),
            ("union", _INPUT_UNION),
        ]


def parse_shortcut_vks(shortcut: str) -> tuple[list[int], int]:
    """
    Parse a shortcut string like `'ctrl+shift+c'` or `'ctrl+alt+1'` into
    `(modifier_vks, primary_vk)`.
    """
    parts = [p.strip().lower() for p in shortcut.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty shortcut string: {shortcut!r}")

    mod_vks: list[int] = []
    primary_vk: int | None = None

    for part in parts:
        if part in ("ctrl", "control", "alt", "shift", "win", "super"):
            vk = NAMED_VK_MAP[part]
            if vk not in mod_vks:
                mod_vks.append(vk)
        elif part in NAMED_VK_MAP:
            primary_vk = NAMED_VK_MAP[part]
        elif len(part) == 1 and ("a" <= part <= "z" or "0" <= part <= "9"):
            primary_vk = ord(part.upper())
        else:
            raise ValueError(f"Unrecognized key token {part!r} in shortcut {shortcut!r}")

    if primary_vk is None:
        raise ValueError(f"Shortcut {shortcut!r} is missing a primary non-modifier key")

    return mod_vks, primary_vk


def _make_key_input(vk: int, key_up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if key_up else 0
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send_inputs(inputs: list[INPUT]) -> int:
    if not IS_WINDOWS or not inputs:
        return len(inputs)
    arr = (INPUT * len(inputs))(*inputs)
    return int(ctypes.windll.user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT)))


def is_key_pressed(vk: int) -> bool:
    if not IS_WINDOWS:
        return False
    state = ctypes.windll.user32.GetAsyncKeyState(vk)
    return bool(state & 0x8000)


def release_modifiers(wait_for_release: bool = False, timeout_ms: int = 300) -> None:
    """
    Inspect `GetAsyncKeyState` for Ctrl/Alt/Shift/Win and inject key-up events via `SendInput`
    so held physical modifiers do not interfere with simulated `Ctrl+C` or `Ctrl+V` (Task 1.2).
    """
    if not IS_WINDOWS:
        return

    if wait_for_release:
        deadline = time.perf_counter() + (timeout_ms / 1000.0)
        while time.perf_counter() < deadline:
            if not any(is_key_pressed(vk) for vk in MODIFIER_VKS):
                break
            time.sleep(0.005)

    up_events: list[INPUT] = []
    for vk in MODIFIER_VKS:
        if is_key_pressed(vk):
            up_events.append(_make_key_input(vk, key_up=True))

    # Always ensure generic Ctrl, Alt, Shift are released
    for base_vk in (VK_CONTROL, VK_MENU, VK_SHIFT):
        up_events.append(_make_key_input(base_vk, key_up=True))

    _send_inputs(up_events)


def send_shortcut(shortcut: str) -> None:
    """
    Send a key combination (e.g. `'ctrl+c'`, `'ctrl+shift+c'`, `'ctrl+v'`, `'ctrl+a'`)
    atomically via Win32 `SendInput`.
    """
    mod_vks, primary_vk = parse_shortcut_vks(shortcut)
    if not IS_WINDOWS:
        return

    events: list[INPUT] = []
    for m_vk in mod_vks:
        events.append(_make_key_input(m_vk, key_up=False))
    events.append(_make_key_input(primary_vk, key_up=False))
    events.append(_make_key_input(primary_vk, key_up=True))
    for m_vk in reversed(mod_vks):
        events.append(_make_key_input(m_vk, key_up=True))

    _send_inputs(events)