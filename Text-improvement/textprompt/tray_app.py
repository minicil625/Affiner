"""
System tray application using `pystray` and `Pillow` (Task 5.2, Task 4.5).
Icon states:
  - grey: idle
  - amber: refining
  - green: flash on replace
  - red: error
  - blue outline: offline / rules-only
Includes menus for Mode switching, Last provider/latency, Today's shared free-tier usage,
Privacy toggles, Add word to vocabulary, Manage API keys, Undo last replace, Open/Reload config,
View log, Pause shortcuts, and Quit.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw

from promptcore.secrets import KNOWN_PROVIDERS, get as get_secret, set_key

logger = logging.getLogger(__name__)

TrayState = Literal["idle", "refining", "done", "error", "offline"]

_STATE_COLORS: dict[TrayState, tuple[str, str]] = {
    "idle": ("#71717a", "#a1a1aa"),      # grey fill, light grey border
    "refining": ("#f59e0b", "#fbbf24"),  # amber fill
    "done": ("#10b981", "#34d399"),      # green flash
    "error": ("#ef4444", "#f87171"),     # red error
    "offline": ("#3f3f46", "#3b82f6"),   # dark fill with bright blue outline (rules-only/offline)
}


def create_tray_icon_image(state: TrayState = "idle", size: int = 64) -> Image.Image:
    """Render a crisp 64x64 rounded badge icon with a 'T' monogram and status color."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill_hex, border_hex = _STATE_COLORS.get(state, _STATE_COLORS["idle"])

    pad = 4
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=14,
        fill=fill_hex,
        outline=border_hex,
        width=5 if state == "offline" else 3,
    )
    # Draw crisp white 'T' bar and stem
    bar_w = int(size * 0.46)
    bar_h = int(size * 0.11)
    stem_w = int(size * 0.13)
    stem_h = int(size * 0.48)
    cx = size // 2
    top_y = int(size * 0.26)
    draw.rectangle([cx - bar_w // 2, top_y, cx + bar_w // 2, top_y + bar_h], fill="#ffffff")
    draw.rectangle([cx - stem_w // 2, top_y, cx + stem_w // 2, top_y + stem_h], fill="#ffffff")
    return img


def open_file_in_os(path: Path) -> None:
    """Open a config or log file in the user's default editor."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        logger.warning("Could not open %s: %s", path, exc)


def prompt_add_vocabulary_word(vocab_file: Path, on_added: Callable[[str], None] | None = None) -> None:
    """Show a non-blocking Tkinter dialog to append a new term/alias to `vocabulary.txt` (Task 4.5)."""
    def _dialog_thread() -> None:
        try:
            import tkinter as tk
            from tkinter import simpledialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            entry = simpledialog.askstring(
                "Add Word to Vocabulary — TextPrompt AI",
                "Enter canonical term (e.g. 'FastAPI') or alias mapping (e.g. 'postgres => PostgreSQL'):",
                parent=root,
            )
            root.destroy()
            if entry and entry.strip():
                cleaned = entry.strip()
                with vocab_file.open("a", encoding="utf-8") as f:
                    f.write(f"\n{cleaned}\n")
                if on_added:
                    on_added(cleaned)
        except Exception as exc:
            logger.warning("Add vocabulary dialog failed: %s", exc)

    threading.Thread(target=_dialog_thread, daemon=True, name="vocab-dialog").start()


def prompt_manage_api_keys(on_saved: Callable[[str], None] | None = None) -> None:
    """Show a small dialog allowing the user to set/update a provider's API key in `PromptCore` keyring."""
    def _dialog_thread() -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk

            root = tk.Tk()
            root.title("Manage Free-Tier API Keys (PromptCore)")
            root.geometry("420x210")
            root.resizable(False, False)
            root.attributes("-topmost", True)

            pad = {"padx": 14, "pady": 6}
            tk.Label(root, text="Provider (shared with VoicePrompt AI):").pack(anchor="w", **pad)
            provider_var = tk.StringVar(value="groq")
            combo = ttk.Combobox(root, textvariable=provider_var, values=list(KNOWN_PROVIDERS), state="readonly")
            combo.pack(fill="x", **pad)

            tk.Label(root, text="API Key (stored in Windows Credential Manager):").pack(anchor="w", **pad)
            key_var = tk.StringVar(value=get_secret("groq") or "")
            key_entry = ttk.Entry(root, textvariable=key_var, show="*")
            key_entry.pack(fill="x", **pad)

            def _on_provider_select(_: Any = None) -> None:
                p = provider_var.get()
                key_var.set(get_secret(p) or "")

            combo.bind("<<ComboboxSelected>>", _on_provider_select)

            def _save() -> None:
                p = provider_var.get()
                val = key_var.get().strip()
                if val and set_key(p, val):
                    if on_saved:
                        on_saved(p)
                    messagebox.showinfo("Saved", f"API key for '{p}' saved to PromptCore keyring.", parent=root)
                    root.destroy()

            btn_frame = tk.Frame(root)
            btn_frame.pack(fill="x", padx=14, pady=12)
            ttk.Button(btn_frame, text="Save Key", command=_save).pack(side="right", padx=(6, 0))
            ttk.Button(btn_frame, text="Cancel", command=root.destroy).pack(side="right")
            root.mainloop()
        except Exception as exc:
            logger.warning("API key dialog failed: %s", exc)

    threading.Thread(target=_dialog_thread, daemon=True, name="apikey-dialog").start()


class TrayApp:
    """
    System tray icon and menu controller (Task 5.2).
    """

    def __init__(
        self,
        *,
        get_active_mode: Callable[[], str],
        set_active_mode: Callable[[str], None],
        get_last_telemetry: Callable[[], dict[str, str]],
        get_daily_usage: Callable[[], dict[str, dict[str, Any]]],
        get_privacy_flags: Callable[[], tuple[bool, bool]],
        toggle_allow_cloud: Callable[[], None],
        toggle_exclude_training: Callable[[], None],
        is_paused: Callable[[], bool],
        toggle_pause: Callable[[], None],
        on_undo: Callable[[], None],
        on_reload: Callable[[], None],
        on_quit: Callable[[], None],
        config_path: Path,
        vocab_path: Path,
        log_path: Path,
    ) -> None:
        self.get_active_mode = get_active_mode
        self.set_active_mode = set_active_mode
        self.get_last_telemetry = get_last_telemetry
        self.get_daily_usage = get_daily_usage
        self.get_privacy_flags = get_privacy_flags
        self.toggle_allow_cloud = toggle_allow_cloud
        self.toggle_exclude_training = toggle_exclude_training
        self.is_paused = is_paused
        self.toggle_pause = toggle_pause
        self.on_undo = on_undo
        self.on_reload = on_reload
        self.on_quit = on_quit
        self.config_path = config_path
        self.vocab_path = vocab_path
        self.log_path = log_path

        self._icon: Any = None
        self._state: TrayState = "idle"
        self._thread: threading.Thread | None = None

    def set_state(self, state: TrayState, flash_back_ms: int | None = None) -> None:
        self._state = state
        if self._icon is not None:
            try:
                self._icon.icon = create_tray_icon_image(state)
            except Exception:
                pass
        if flash_back_ms is not None:
            t = threading.Timer(flash_back_ms / 1000.0, lambda: self.set_state("idle"))
            t.daemon = True
            t.start()

    def _build_menu(self) -> Any:
        import pystray
        from pystray import Menu, MenuItem

        def _usage_items() -> list[MenuItem]:
            summary = self.get_daily_usage()
            if not summary:
                return [MenuItem("No cloud calls today (0 tokens)", None, enabled=False)]
            items: list[MenuItem] = []
            for prov, stats in sorted(summary.items()):
                reqs = stats.get("requests", 0)
                toks = stats.get("tokens", 0)
                items.append(MenuItem(f"{prov}: {reqs} reqs ({toks:,} tok)", None, enabled=False))
            return items

        def _telemetry_items() -> list[MenuItem]:
            tel = self.get_last_telemetry()
            if not tel:
                return [MenuItem("No runs yet in this session", None, enabled=False)]
            return [MenuItem(f"{mode}: {info}", None, enabled=False) for mode, info in tel.items()]

        return Menu(
            MenuItem(
                "Mode 1: Clean Only (Ctrl+Alt+1)",
                lambda _i, _m: self.set_active_mode("clean_only"),
                checked=lambda _: self.get_active_mode() == "clean_only",
                radio=True,
            ),
            MenuItem(
                "Mode 2: Clean + Improved (Ctrl+Alt+2)",
                lambda _i, _m: self.set_active_mode("clean_plus_improved"),
                checked=lambda _: self.get_active_mode() == "clean_plus_improved",
                radio=True,
            ),
            MenuItem(
                "Mode 3: Structured JSON (Ctrl+Alt+3)",
                lambda _i, _m: self.set_active_mode("structured_json"),
                checked=lambda _: self.get_active_mode() == "structured_json",
                radio=True,
            ),
            Menu.SEPARATOR,
            MenuItem("Last Provider & Latency", Menu(_telemetry_items)),
            MenuItem("Today's Shared Free Quota", Menu(_usage_items)),
            MenuItem(
                "Privacy",
                Menu(
                    MenuItem(
                        "Allow Cloud Text Processing",
                        lambda _i, _m: self.toggle_allow_cloud(),
                        checked=lambda _: self.get_privacy_flags()[0],
                    ),
                    MenuItem(
                        "Exclude Providers That Train on Free Data",
                        lambda _i, _m: self.toggle_exclude_training(),
                        checked=lambda _: self.get_privacy_flags()[1],
                    ),
                ),
            ),
            Menu.SEPARATOR,
            MenuItem("Undo Last Replace (Ctrl+Alt+Z)", lambda _i, _m: self.on_undo()),
            MenuItem(
                "Add Word to Vocabulary…",
                lambda _i, _m: prompt_add_vocabulary_word(self.vocab_path, lambda _: self.on_reload()),
            ),
            MenuItem("Manage API Keys…", lambda _i, _m: prompt_manage_api_keys(lambda _: self.on_reload())),
            Menu.SEPARATOR,
            MenuItem(
                "Pause Shortcuts",
                lambda _i, _m: self.toggle_pause(),
                checked=lambda _: self.is_paused(),
            ),
            MenuItem("Open config.yaml", lambda _i, _m: open_file_in_os(self.config_path)),
            MenuItem("Reload Configuration", lambda _i, _m: self.on_reload()),
            MenuItem("View Log", lambda _i, _m: open_file_in_os(self.log_path)),
            Menu.SEPARATOR,
            MenuItem("Quit TextPrompt AI", lambda _i, _m: self.on_quit()),
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        def _run_tray() -> None:
            try:
                import pystray

                self._icon = pystray.Icon(
                    "TextPromptAI",
                    create_tray_icon_image(self._state),
                    "TextPrompt AI (Cloud Free-Tier Edition)",
                    menu=self._build_menu(),
                )
                self._icon.run()
            except Exception as exc:
                logger.warning("System tray exited or unavailable: %s", exc)

        self._thread = threading.Thread(target=_run_tray, daemon=True, name="pystray-thread")
        self._thread.start()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None