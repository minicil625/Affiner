"""
Unit tests for `textprompt/config.py`, `keys.py`, `hotkeys.py`, `text_capture.py`,
and `injector.py` (Tasks 1.1, 1.2, 1.3, 3.5, 5.1 AC).
Uses monkeypatching so tests run deterministically on any OS without modifying the user's real clipboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import textprompt.injector as injector_mod
import textprompt.text_capture as capture_mod
from textprompt.config import AppSettings, load_app_settings
from textprompt.hotkeys import shortcut_to_pynput_format, shortcut_to_win32_modifiers_and_vk
from textprompt.injector import UndoManager, perform_undo, replace_selection
from textprompt.keys import VK_CONTROL, VK_MENU, VK_SHIFT, VK_SPACE, parse_shortcut_vks
from textprompt.text_capture import ClipboardBackup, capture_selection


def test_app_settings_loads_and_resolves_per_app_terminal_rules() -> None:
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    settings = load_app_settings(cfg_path)

    assert settings.modes.default == "clean_only"
    assert settings.capture.get_copy_shortcut("WindowsTerminal.exe") == "ctrl+shift+c"
    assert settings.capture.get_copy_shortcut("chrome.exe") == "ctrl+c"
    assert settings.paste.get_paste_shortcut("WindowsTerminal.exe") == "ctrl+shift+v"
    assert settings.paste.get_paste_shortcut("Code.exe") == "ctrl+v"
    assert settings.is_app_excluded("KeePassXC.exe") is True
    assert settings.is_app_excluded("chrome.exe") is False


def test_shortcut_parsing_and_hotkey_conversions() -> None:
    mods, vk = parse_shortcut_vks("ctrl+alt+space")
    assert mods == [VK_CONTROL, VK_MENU]
    assert vk == VK_SPACE

    mods2, vk2 = parse_shortcut_vks("ctrl+shift+c")
    assert mods2 == [VK_CONTROL, VK_SHIFT]
    assert vk2 == ord("C")

    win_mods, win_vk = shortcut_to_win32_modifiers_and_vk("ctrl+alt+1")
    assert win_vk == ord("1")
    assert win_mods & 0x0002  # MOD_CONTROL
    assert win_mods & 0x0001  # MOD_ALT
    assert win_mods & 0x4000  # MOD_NOREPEAT

    assert shortcut_to_pynput_format("ctrl+alt+space") == "<ctrl>+<alt>+<space>"


def test_capture_selection_detects_sequence_change_and_enforces_max_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seq_counter = {"val": 100}
    sent_shortcuts: list[str] = []

    monkeypatch.setattr(capture_mod, "get_foreground_window_info", lambda: (12345, "WindowsTerminal.exe", False))
    monkeypatch.setattr(
        capture_mod,
        "backup_clipboard",
        lambda mode="all": ClipboardBackup(formats={13: "original_clipboard"}),
    )
    monkeypatch.setattr(capture_mod, "get_clipboard_sequence_number", lambda: seq_counter["val"])
    monkeypatch.setattr(capture_mod, "release_modifiers", lambda **_: None)

    def _fake_send_shortcut(sc: str) -> None:
        sent_shortcuts.append(sc)
        seq_counter["val"] += 1  # Simulate copy landing immediately

    monkeypatch.setattr(capture_mod, "send_shortcut", _fake_send_shortcut)
    monkeypatch.setattr(capture_mod, "read_clipboard_unicode_text", lambda: "hello world from terminal\r\n")

    settings = AppSettings()
    settings.capture.max_chars = 11
    res = capture_selection(settings)

    assert res.empty is False
    assert sent_shortcuts == ["ctrl+shift+c"]
    assert res.text == "hello world"
    assert res.truncated is True
    assert res.hwnd == 12345
    assert res.process_name == "WindowsTerminal.exe"


def test_capture_selection_restores_clipboard_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restored: list[ClipboardBackup] = []
    monkeypatch.setattr(capture_mod, "get_foreground_window_info", lambda: (12345, "notepad.exe", False))
    monkeypatch.setattr(
        capture_mod,
        "backup_clipboard",
        lambda mode="all": ClipboardBackup(formats={13: "keep_me"}),
    )
    # Sequence number never changes -> simulates empty selection timeout
    monkeypatch.setattr(capture_mod, "get_clipboard_sequence_number", lambda: 42)
    monkeypatch.setattr(capture_mod, "release_modifiers", lambda **_: None)
    monkeypatch.setattr(capture_mod, "send_shortcut", lambda _: None)
    monkeypatch.setattr(capture_mod, "restore_clipboard", lambda b: restored.append(b) or True)

    settings = AppSettings()
    settings.capture.copy_timeout_ms = 50
    res = capture_selection(settings)

    assert res.empty is True
    assert len(restored) == 1
    assert restored[0].formats[13] == "keep_me"


def test_replace_selection_aborts_paste_when_foreground_hwnd_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written_texts: list[str] = []
    sent_shortcuts: list[str] = []
    notices: list[str] = []

    monkeypatch.setattr(injector_mod, "write_clipboard_unicode_text", lambda t: written_texts.append(t) or True)
    # Foreground HWND is now 99999 instead of target 12345
    monkeypatch.setattr(injector_mod, "get_foreground_window_info", lambda: (99999, "slack.exe", False))
    monkeypatch.setattr(injector_mod, "send_shortcut", lambda s: sent_shortcuts.append(s))

    settings = AppSettings()
    settings.paste.restore_clipboard = False
    pasted, _ = replace_selection(
        text="Cleaned output.",
        backup=ClipboardBackup(),
        target_hwnd=12345,
        process_name="chrome.exe",
        settings=settings,
        on_notice=notices.append,
    )

    assert pasted is False
    assert written_texts == ["Cleaned output."]
    assert sent_shortcuts == []
    assert any("Window changed" in n for n in notices)


def test_undo_manager_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    pasted_texts: list[str] = []
    monkeypatch.setattr(injector_mod, "get_foreground_window_info", lambda: (100, "Code.exe", False))
    monkeypatch.setattr(injector_mod, "backup_clipboard", lambda mode="all": ClipboardBackup())
    monkeypatch.setattr(injector_mod, "write_clipboard_unicode_text", lambda t: pasted_texts.append(t) or True)
    monkeypatch.setattr(injector_mod, "release_modifiers", lambda **_: None)
    monkeypatch.setattr(injector_mod, "send_shortcut", lambda _: None)
    monkeypatch.setattr(injector_mod, "get_clipboard_sequence_number", lambda: 1)

    settings = AppSettings()
    settings.paste.restore_clipboard = False

    undo_mgr = UndoManager()
    undo_mgr.record("raw original text", "Cleaned text.", 100, "Code.exe")
    assert undo_mgr.has_undo() is True

    ok = perform_undo(undo_mgr, settings)
    assert ok is True
    assert pasted_texts == ["raw original text"]
    assert undo_mgr.has_undo() is False