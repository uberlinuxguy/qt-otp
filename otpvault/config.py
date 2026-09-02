"""User preferences (non-secret) persisted with QSettings."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from PySide6.QtCore import QSettings

from . import APP_NAME, ORG_NAME
from .vault import default_vault_path

IDLE_CHOICES = (0, 60, 120, 300, 600, 900, 1800)


SETTINGS_FILE_ENV = "QT_OTP_SETTINGS_FILE"


def settings_store() -> QSettings:
    """The backing store for preferences.

    One seam for every read and write. Setting QT_OTP_SETTINGS_FILE points it
    at an ini file instead of the per-user registry/config location, which
    keeps a portable install (or a test) self-contained.
    """
    override = os.environ.get(SETTINGS_FILE_ENV)
    if override:
        return QSettings(override, QSettings.Format.IniFormat)
    return QSettings(ORG_NAME, APP_NAME)


@dataclass
class Settings:
    """Everything the user can tune. Nothing here is sensitive.

    `vault_path` empty means "wherever the platform default is", so a user who
    never chooses a location keeps following the default if it ever changes.
    """

    idle_lock_seconds: int = 300
    lock_on_session_lock: bool = True
    lock_on_suspend: bool = True
    lock_on_minimize: bool = False
    clipboard_clear_seconds: int = 20
    minimize_to_tray: bool = True
    hide_codes_until_hover: bool = False
    vault_path: str = ""

    @classmethod
    def load(cls) -> "Settings":
        store = settings_store()
        values: dict[str, object] = {}
        for spec in fields(cls):
            default = getattr(cls, spec.name, None)
            raw = store.value(spec.name, default)
            if isinstance(default, bool):
                values[spec.name] = str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, str):
                values[spec.name] = "" if raw is None else str(raw)
            else:
                try:
                    values[spec.name] = int(raw)
                except (TypeError, ValueError):
                    values[spec.name] = default
        return cls(**values)  # type: ignore[arg-type]

    def save(self) -> None:
        store = settings_store()
        for spec in fields(self):
            store.setValue(spec.name, getattr(self, spec.name))
        store.sync()

    def resolved_vault_path(self, override: Path | str | None = None) -> Path:
        """Where the vault should live: --vault wins, then the saved setting,
        then the platform default."""
        if override:
            return Path(override).expanduser()
        if self.vault_path:
            return Path(self.vault_path).expanduser()
        return default_vault_path()
