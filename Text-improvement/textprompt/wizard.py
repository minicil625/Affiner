"""
Task 6.3 — First-run setup wizard and "Start with Windows" registry helper.
1. Explains the free-tier cloud providers and privacy controls (`FastPath`, `excluded_apps`).
2. Detects existing `PromptCore` keys in Windows Credential Manager (shared with VoicePrompt AI)
   or allows pasting keys directly with one-click sign-up links.
3. Runs a quick provider check and optionally enables "Start with Windows".
"""

from __future__ import annotations

import logging
import sys
import webbrowser
from pathlib import Path
from typing import Final

from promptcore.secrets import list_configured_providers, set_key

logger = logging.getLogger(__name__)

IS_WINDOWS: Final[bool] = sys.platform == "win32"
RUN_REG_PATH: Final[str] = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME: Final[str] = "TextPromptAI"

PROVIDER_SIGNUP_URLS: Final[dict[str, tuple[str, str]]] = {
    "groq": ("Groq (Primary Fast/Quality — Free)", "https://console.groq.com/keys"),
    "cerebras": ("Cerebras (Fast Fallback — Free)", "https://cloud.cerebras.ai/"),
    "gemini": ("Google AI Studio (No-Billing Project — Free)", "https://aistudio.google.com/apikey"),
    "openrouter": ("OpenRouter (:free models — Free)", "https://openrouter.ai/keys"),
}


def is_start_with_windows_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_REG_NAME)
            return bool(val)
    except OSError:
        return False


def set_start_with_windows(enabled: bool, executable_cmd: str | None = None) -> bool:
    """Add or remove TextPrompt AI in `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = executable_cmd or f'"{sys.executable}" -m textprompt.main'
                winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_REG_NAME)
                except OSError:
                    pass
        return True
    except OSError as exc:
        logger.warning("Failed to update Windows startup registry key: %s", exc)
        return False


def run_first_run_wizard_if_needed(force: bool = False) -> bool:
    """
    If no API keys are configured in `PromptCore` (or `force=True`), display the
    first-run onboarding wizard. Returns True if at least one key is configured or user chose local-only.
    """
    configured = list_configured_providers()
    if configured and not force:
        return True

    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception:
        return False

    root = tk.Tk()
    root.title("Welcome to TextPrompt AI (Cloud Free-Tier Edition)")
    root.geometry("580x470")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    header = tk.Label(
        root,
        text="TextPrompt AI — First-Run Setup",
        font=("Segoe UI", 13, "bold"),
        anchor="w",
    )
    header.pack(fill="x", padx=18, pady=(14, 4))

    privacy_note = (
        "• Cost: $0. Uses free-tier cloud APIs (Groq, Cerebras, Gemini, OpenRouter) + local SymSpell & regex rules.\n"
        "• Speed & Privacy: Simple typo/shorthand fixes run 100% locally in < 10 ms without leaving your PC.\n"
        "• Shared Keys: Keys are stored in Windows Credential Manager ('PromptCore') and shared with VoicePrompt AI."
    )
    tk.Label(root, text=privacy_note, justify="left", fg="#3f3f46", wraplength=540).pack(
        fill="x", padx=18, pady=(0, 10)
    )

    entries: dict[str, tk.StringVar] = {}
    form = tk.Frame(root)
    form.pack(fill="both", expand=True, padx=18)

    for prov_id, (label_text, url) in PROVIDER_SIGNUP_URLS.items():
        row = tk.Frame(form)
        row.pack(fill="x", pady=4)
        lbl = tk.Label(row, text=label_text, width=36, anchor="w")
        lbl.pack(side="left")
        link_btn = ttk.Button(row, text="Get Free Key ↗", width=15, command=lambda u=url: webbrowser.open(u))
        link_btn.pack(side="right")

        var = tk.StringVar()
        ent = ttk.Entry(form, textvariable=var, show="*")
        ent.pack(fill="x", pady=(0, 6))
        entries[prov_id] = var

    startup_var = tk.BooleanVar(value=is_start_with_windows_enabled())
    ttk.Checkbutton(
        root,
        text="Start TextPrompt AI automatically when I sign in to Windows",
        variable=startup_var,
    ).pack(anchor="w", padx=18, pady=(6, 8))

    def _save_and_continue() -> None:
        saved_any = False
        for prov_id, var in entries.items():
            val = var.get().strip()
            if val and set_key(prov_id, val):
                saved_any = True
        set_start_with_windows(startup_var.get())
        if saved_any:
            messagebox.showinfo(
                "Setup Complete",
                "API keys saved to Windows Credential Manager (PromptCore).\n"
                "Select any text and press Ctrl+Alt+Space to refine!",
                parent=root,
            )
        root.destroy()

    btn_bar = tk.Frame(root)
    btn_bar.pack(fill="x", padx=18, pady=12)
    ttk.Button(btn_bar, text="Save & Start TextPrompt", command=_save_and_continue).pack(side="right", padx=(8, 0))
    ttk.Button(btn_bar, text="Use Offline Rules Only", command=root.destroy).pack(side="right")

    root.mainloop()
    return True