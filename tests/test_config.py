"""Settings persistence and vault-path resolution.

The autouse `isolate_settings` fixture in conftest points the store at a temp
ini file, so these tests never touch the user's real settings.
"""

from __future__ import annotations

from pathlib import Path

from otpvault.config import Settings
from otpvault.vault import default_vault_path


def test_defaults_when_nothing_is_saved() -> None:
    settings = Settings.load()
    assert settings.vault_path == ""
    assert settings.idle_lock_seconds == 300
    assert settings.lock_on_session_lock is True
    assert settings.lock_on_minimize is False


def test_round_trip_of_every_field(tmp_path: Path) -> None:
    saved = Settings(
        idle_lock_seconds=600,
        lock_on_session_lock=False,
        lock_on_suspend=False,
        lock_on_minimize=True,
        clipboard_clear_seconds=45,
        minimize_to_tray=False,
        hide_codes_until_hover=True,
        vault_path=str(tmp_path / "my vault.otpv"),
    )
    saved.save()
    assert Settings.load() == saved


def test_booleans_survive_being_stored_as_strings() -> None:
    from otpvault.config import settings_store

    store = settings_store()
    store.setValue("lock_on_session_lock", "false")
    store.setValue("lock_on_minimize", "true")
    store.sync()
    settings = Settings.load()
    assert settings.lock_on_session_lock is False
    assert settings.lock_on_minimize is True


def test_a_junk_number_falls_back_to_the_default() -> None:
    from otpvault.config import settings_store

    store = settings_store()
    store.setValue("idle_lock_seconds", "not a number")
    store.sync()
    assert Settings.load().idle_lock_seconds == 300


def test_resolved_path_uses_the_platform_default_when_unset() -> None:
    assert Settings().resolved_vault_path() == default_vault_path()


def test_resolved_path_prefers_the_saved_setting(tmp_path: Path) -> None:
    chosen = tmp_path / "vault.otpv"
    assert Settings(vault_path=str(chosen)).resolved_vault_path() == chosen


def test_resolved_path_lets_the_cli_override_win(tmp_path: Path) -> None:
    saved = tmp_path / "saved.otpv"
    override = tmp_path / "override.otpv"
    settings = Settings(vault_path=str(saved))
    assert settings.resolved_vault_path(override) == override
    assert settings.vault_path == str(saved)  # the override is not persisted


def test_resolved_path_expands_a_tilde() -> None:
    resolved = Settings(vault_path="~/vaults/codes.otpv").resolved_vault_path()
    assert "~" not in str(resolved)
    assert resolved == Path.home() / "vaults" / "codes.otpv"


def test_the_settings_file_override_is_honoured(tmp_path: Path, monkeypatch) -> None:
    """QT_OTP_SETTINGS_FILE keeps preferences beside a portable install."""
    from otpvault.config import SETTINGS_FILE_ENV, settings_store

    target = tmp_path / "portable" / "settings.ini"
    monkeypatch.setenv(SETTINGS_FILE_ENV, str(target))

    Settings(idle_lock_seconds=120, vault_path=str(tmp_path / "v.otpv")).save()

    assert Path(settings_store().fileName()) == target
    assert target.is_file(), "nothing was written to the override location"
    assert "idle_lock_seconds" in target.read_text(encoding="utf-8")
    assert Settings.load().idle_lock_seconds == 120


def test_without_the_override_the_per_user_store_is_used(monkeypatch) -> None:
    from otpvault.config import SETTINGS_FILE_ENV, settings_store

    monkeypatch.delenv(SETTINGS_FILE_ENV, raising=False)
    # fileName() only reports where it would read and write; nothing is stored.
    assert "qt-otp" in settings_store().fileName()
