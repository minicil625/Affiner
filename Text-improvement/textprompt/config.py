"""
AppSettings schema and live configuration/dictionary hot-reloader (Task 5.1, Task 4.5).
Extends `promptcore.config.CoreSettings` with TextPrompt-specific sections (`hotkeys`,
`capture`, `paste`, `typo`, `ui`) and watches `config.yaml`, `vocabulary.txt`,
`shorthand.txt`, and `%APPDATA%\\PromptCore\\providers.yaml` using `watchdog`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from promptcore.config import CoreSettings, deep_merge, get_appdata_dir, load_yaml_dict

logger = logging.getLogger(__name__)


class HotkeysConfig(BaseModel):
    run_active: str = "ctrl+alt+space"
    mode_1: str = "ctrl+alt+1"
    mode_2: str = "ctrl+alt+2"
    mode_3: str = "ctrl+alt+3"
    cycle_mode: str = "ctrl+alt+m"
    undo_last: str = "ctrl+alt+z"


class CaptureConfig(BaseModel):
    auto_select_all: bool = False
    copy_timeout_ms: int = Field(default=300, ge=50, le=3000)
    backup_formats: Literal["all", "text_only"] = "all"
    max_chars: int = Field(default=6000, ge=100, le=50000)
    wait_for_release: bool = False
    copy_rules: dict[str, str] = Field(
        default_factory=lambda: {
            "WindowsTerminal.exe": "ctrl+shift+c",
            "cmd.exe": "ctrl+shift+c",
            "powershell.exe": "ctrl+shift+c",
            "pwsh.exe": "ctrl+shift+c",
            "alacritty.exe": "ctrl+shift+c",
            "wezterm-gui.exe": "ctrl+shift+c",
        }
    )

    def get_copy_shortcut(self, process_name: str) -> str:
        low = process_name.lower()
        for app_exe, shortcut in self.copy_rules.items():
            if app_exe.lower() == low:
                return shortcut
        return "ctrl+c"


class PasteConfig(BaseModel):
    restore_clipboard: bool = True
    restore_delay_ms: int = Field(default=400, ge=50, le=5000)
    rules: dict[str, str] = Field(
        default_factory=lambda: {
            "WindowsTerminal.exe": "ctrl+shift+v",
            "cmd.exe": "ctrl+shift+v",
            "powershell.exe": "ctrl+shift+v",
            "pwsh.exe": "ctrl+shift+v",
            "alacritty.exe": "ctrl+shift+v",
            "wezterm-gui.exe": "ctrl+shift+v",
        }
    )

    def get_paste_shortcut(self, process_name: str) -> str:
        low = process_name.lower()
        for app_exe, shortcut in self.rules.items():
            if app_exe.lower() == low:
                return shortcut
        return "ctrl+v"


class TypoConfig(BaseModel):
    enabled: bool = True
    extra_dictionaries: list[str] = Field(default_factory=list)


class UIConfig(BaseModel):
    overlay: bool = True
    sounds: bool = True
    done_sound: bool = False
    busy_behavior: Literal["ignore", "queue"] = "ignore"


class AppSettings(CoreSettings):
    """Combined shared CoreSettings + TextPrompt desktop settings."""

    hotkeys: HotkeysConfig = Field(default_factory=HotkeysConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    paste: PasteConfig = Field(default_factory=PasteConfig)
    typo: TypoConfig = Field(default_factory=TypoConfig)
    ui: UIConfig = Field(default_factory=UIConfig)

    def is_app_excluded(self, process_name: str) -> bool:
        if not process_name:
            return False
        low = process_name.strip().lower()
        return any(app.strip().lower() == low for app in self.privacy.excluded_apps)


def get_textprompt_appdata_dir() -> Path:
    """Return `%APPDATA%\\TextPrompt` on Windows or `~/.config/TextPrompt` elsewhere."""
    appdata = os.environ.get("APPDATA")
    base = (Path(appdata) / "TextPrompt") if appdata else (Path.home() / ".config" / "TextPrompt")
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_app_settings(
    config_path: Path | str | None = None,
    providers_override_path: Path | str | None = None,
    on_error: Callable[[str], None] | None = None,
) -> AppSettings:
    """
    Load `AppSettings` by merging:
      1. Built-in defaults
      2. `%APPDATA%\\PromptCore\\providers.yaml` (discovered models/limits from check_providers.py)
      3. Project `config.yaml` (explicit user configuration takes precedence)
    Falls back to defaults if YAML syntax or schema validation fails.
    """
    cfg_file = Path(config_path) if config_path else (get_project_root() / "config.yaml")
    prov_file = (
        Path(providers_override_path)
        if providers_override_path
        else (get_appdata_dir() / "providers.yaml")
    )

    try:
        merged: dict[str, Any] = {}
        if prov_file.exists():
            merged = deep_merge(merged, load_yaml_dict(prov_file))
        if cfg_file.exists():
            merged = deep_merge(merged, load_yaml_dict(cfg_file))
        return AppSettings.model_validate(merged)
    except (yaml.YAMLError, ValidationError, OSError) as exc:
        msg = f"Invalid config.yaml ({exc}); falling back to default settings."
        logger.error(msg)
        if on_error:
            try:
                on_error("Config error; using defaults")
            except Exception:
                pass
        return AppSettings()


def save_config_field(
    config_path: Path | str | None,
    section: str,
    key: str,
    value: Any,
) -> bool:
    """Update a single key in `config.yaml` (e.g., active default mode or privacy toggle)."""
    cfg_file = Path(config_path) if config_path else (get_project_root() / "config.yaml")
    try:
        data = load_yaml_dict(cfg_file) if cfg_file.exists() else {}
        sec_dict = data.setdefault(section, {})
        if isinstance(sec_dict, dict):
            sec_dict[key] = value
        cfg_file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("Failed to save setting %s.%s to %s: %s", section, key, cfg_file, exc)
        return False


class _DebouncedReloadHandler(FileSystemEventHandler):
    """Watches config and dictionary files and triggers a debounced callback on modification."""

    def __init__(
        self,
        watched_filenames: set[str],
        callback: Callable[[Path], None],
        debounce_s: float = 0.25,
    ) -> None:
        super().__init__()
        self.watched_filenames = {name.lower() for name in watched_filenames}
        self.callback = callback
        self.debounce_s = debounce_s
        self._last_fired: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = Path(str(event.src_path))
        fname = src_path.name.lower()
        if fname not in self.watched_filenames:
            return
        now = time.monotonic()
        with self._lock:
            last = self._last_fired.get(fname, 0.0)
            if (now - last) < self.debounce_s:
                return
            self._last_fired[fname] = now
        try:
            self.callback(src_path)
        except Exception as exc:
            logger.warning("Hot-reload callback error for %s: %s", src_path, exc)

    def on_created(self, event: FileSystemEvent) -> None:
        self.on_modified(event)


class ConfigWatcher:
    """
    Background `watchdog` observer that hot-reloads `config.yaml`, `vocabulary.txt`,
    `shorthand.txt`, and `%APPDATA%\\PromptCore\\providers.yaml` without restarting (Task 5.1).
    """

    def __init__(
        self,
        project_dir: Path | None = None,
        appdata_dir: Path | None = None,
        on_file_changed: Callable[[Path], None] | None = None,
    ) -> None:
        self.project_dir = project_dir or get_project_root()
        self.appdata_dir = appdata_dir or get_appdata_dir()
        self.on_file_changed = on_file_changed
        self._observer: Observer | None = None

    def start(self) -> None:
        if self._observer is not None or self.on_file_changed is None:
            return
        watched_names = {"config.yaml", "vocabulary.txt", "shorthand.txt", "providers.yaml"}
        handler = _DebouncedReloadHandler(watched_names, self.on_file_changed)
        obs = Observer()
        if self.project_dir.exists():
            obs.schedule(handler, str(self.project_dir), recursive=False)
        if self.appdata_dir.exists() and self.appdata_dir != self.project_dir:
            obs.schedule(handler, str(self.appdata_dir), recursive=False)
        obs.daemon = True
        obs.start()
        self._observer = obs
        logger.debug("Started watchdog ConfigWatcher on %s", self.project_dir)

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=1.5)
            except Exception:
                pass
            self._observer = None