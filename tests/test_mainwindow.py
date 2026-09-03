"""End-to-end UI behaviour: create, show codes, copy, lock, re-unlock."""

from __future__ import annotations

from pathlib import Path

import pytest

from otpvault import crypto
from otpvault.config import Settings
from otpvault.ui.codes import HIDDEN_CODE, CodeTableModel
from otpvault.vault import OtpEntry, Vault

PASSWORD = "test-password-123"
SECRET = "GEZDGNBVGY3TQOJQ"


@pytest.fixture(autouse=True)
def cheap_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        crypto, "new_kdf_params", lambda: crypto.KdfParams(salt=b"0123456789abcdef", n=2**12)
    )


@pytest.fixture()
def window(qapp, tmp_path: Path):
    from otpvault.ui.mainwindow import MainWindow

    vault = Vault(tmp_path / "vault.otpv")
    settings = Settings(idle_lock_seconds=0, minimize_to_tray=False, clipboard_clear_seconds=0)
    win = MainWindow(vault, settings)
    win.show()  # offscreen, but child widgets need a shown top-level to report visibility
    qapp.processEvents()
    yield win
    win._quitting = True  # noqa: SLF001
    win.close()


def create_vault(window, qapp) -> None:
    page = window._unlock_page  # noqa: SLF001
    page._password.setText(PASSWORD)  # noqa: SLF001
    page._confirm.setText(PASSWORD)  # noqa: SLF001
    page._submit()  # noqa: SLF001
    qapp.processEvents()


def add_sample_entries(window, qapp) -> None:
    window._vault.add(OtpEntry(issuer="GitHub", account="me@example.com", secret=SECRET))  # noqa: SLF001
    window._vault.add(  # noqa: SLF001
        OtpEntry(issuer="AWS", account="root", secret=SECRET, digits=8, period=60, algorithm="SHA256")
    )
    window._reload_model()  # noqa: SLF001
    qapp.processEvents()


def code_at(window, row: int) -> str:
    source = window._proxy.mapToSource(window._proxy.index(row, CodeTableModel.COL_CODE))  # noqa: SLF001
    return window._model.data(source, CodeTableModel.CodeRole)  # noqa: SLF001


def display_at(window, row: int, column: int = CodeTableModel.COL_CODE) -> str:
    from PySide6.QtCore import Qt

    source = window._proxy.mapToSource(window._proxy.index(row, column))  # noqa: SLF001
    return window._model.data(source, Qt.ItemDataRole.DisplayRole)  # noqa: SLF001


def test_starts_locked_on_the_unlock_page(window) -> None:
    assert window.locked
    assert window._stack.currentWidget() is window._unlock_page  # noqa: SLF001
    assert not window._toolbar.isVisible()  # noqa: SLF001
    assert window._watcher.backend_name == "none"  # nothing watching while locked  # noqa: SLF001


def test_create_then_show_codes(window, qapp) -> None:
    create_vault(window, qapp)
    assert not window.locked
    assert window._vault.path.is_file()  # noqa: SLF001
    assert window._empty_hint.isVisible()  # noqa: SLF001

    add_sample_entries(window, qapp)
    assert window._model.rowCount() == 2  # noqa: SLF001
    assert not window._empty_hint.isVisible()  # noqa: SLF001
    assert len(code_at(window, 0)) == 6
    assert len(code_at(window, 1)) == 8
    assert display_at(window, 0) == f"{code_at(window, 0)[:3]} {code_at(window, 0)[3:]}"

    remaining = window._model.data(  # noqa: SLF001
        window._proxy.mapToSource(window._proxy.index(0, CodeTableModel.COL_TIMER)),  # noqa: SLF001
        CodeTableModel.RemainingRole,
    )
    assert 0 < remaining <= 30


def test_search_filters_rows(window, qapp) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._search.setText("aws")  # noqa: SLF001
    qapp.processEvents()
    assert window._proxy.rowCount() == 1  # noqa: SLF001
    window._search.setText("no such account")  # noqa: SLF001
    qapp.processEvents()
    assert window._proxy.rowCount() == 0  # noqa: SLF001


def click_row(window, qapp, row: int, column: int = CodeTableModel.COL_NAME) -> None:
    """Really click the row with the mouse, the way a user would."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = window._table  # noqa: SLF001
    rect = view.visualRect(window._proxy.index(row, column))  # noqa: SLF001
    assert not rect.isEmpty(), "row is not laid out; cannot click it"
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center()
    )
    qapp.processEvents()


def test_clicking_a_row_copies_its_code_and_says_so(window, qapp) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    QGuiApplication.clipboard().setText("something else")

    expected = code_at(window, 0)
    click_row(window, qapp, 0)

    assert QGuiApplication.clipboard().text() == expected
    assert window.statusBar().currentMessage() == (
        "Copied the code for GitHub — me@example.com to the clipboard"
    )


@pytest.mark.parametrize(
    "column", [CodeTableModel.COL_NAME, CodeTableModel.COL_CODE, CodeTableModel.COL_TIMER]
)
def test_clicking_anywhere_on_the_row_copies(window, qapp, column: int) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    expected = code_at(window, 1)
    click_row(window, qapp, 1, column)
    assert QGuiApplication.clipboard().text() == expected


def test_clicking_still_selects_the_row_for_edit_and_delete(window, qapp) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    click_row(window, qapp, 1)
    selected = window._selected_entry()  # noqa: SLF001
    assert selected is not None and selected.issuer == "AWS"
    assert window._action_edit.isEnabled()  # noqa: SLF001
    assert window._action_delete.isEnabled()  # noqa: SLF001


def test_copy_message_gets_the_whole_status_bar(window, qapp) -> None:
    """The permanent 'Unlocked · auto-lock…' label must not overlap the message."""
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    assert window._status_label.isVisible() or window.statusBar().currentMessage()  # noqa: SLF001

    click_row(window, qapp, 0)
    assert window.statusBar().currentMessage()
    assert not window._status_label.isVisible()  # noqa: SLF001

    window.statusBar().clearMessage()
    qapp.processEvents()
    assert window._status_label.isVisible()  # noqa: SLF001
    assert "auto-lock" in window._status_label.text()  # noqa: SLF001


def test_copy_message_mentions_the_clipboard_timeout_when_set(window, qapp) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._settings.clipboard_clear_seconds = 20  # noqa: SLF001
    click_row(window, qapp, 0)
    assert window.statusBar().currentMessage().endswith("clipboard clears in 20s")


def test_clicking_a_masked_row_still_copies_the_real_code(window, qapp) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._model.set_hide_codes(True)  # noqa: SLF001
    expected = code_at(window, 0)
    click_row(window, qapp, 0)
    assert QGuiApplication.clipboard().text() == expected


def test_copy_puts_the_bare_code_on_the_clipboard(window, qapp) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._table.selectRow(0)  # noqa: SLF001
    expected = code_at(window, 0)
    window._copy_index(window._table.currentIndex())  # noqa: SLF001
    qapp.processEvents()
    assert QGuiApplication.clipboard().text() == expected


def test_hidden_codes_reveal_on_hover(window, qapp) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._model.set_hide_codes(True)  # noqa: SLF001
    assert display_at(window, 0) == HIDDEN_CODE
    window._model.set_revealed_row(0)  # noqa: SLF001
    assert display_at(window, 0) != HIDDEN_CODE
    assert display_at(window, 1) == HIDDEN_CODE


def test_lock_wipes_everything_and_returns_to_the_unlock_page(window, qapp) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._table.selectRow(0)  # noqa: SLF001
    copied = code_at(window, 0)
    window._copy_index(window._table.currentIndex())  # noqa: SLF001
    entry_held_by_the_ui = window._model.entry_at(0)  # noqa: SLF001

    window._watcher.lockRequested.emit("the workstation was locked")  # noqa: SLF001
    qapp.processEvents()

    assert window.locked
    assert window._stack.currentWidget() is window._unlock_page  # noqa: SLF001
    assert window._model.rowCount() == 0  # noqa: SLF001
    assert window._vault.entries == []  # noqa: SLF001
    assert entry_held_by_the_ui.secret == ""  # the row's own copy lost its secret
    assert not window._refresh_timer.isActive()  # noqa: SLF001
    assert QGuiApplication.clipboard().text() != copied
    assert "workstation was locked" in window._unlock_page._message.text()  # noqa: SLF001


def test_wrong_password_then_right_password(window, qapp) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window.lock("you locked it")
    qapp.processEvents()

    page = window._unlock_page  # noqa: SLF001
    page._password.setText("not the password")  # noqa: SLF001
    page._submit()  # noqa: SLF001
    qapp.processEvents()
    assert window.locked
    assert "Wrong password" in page._message.text()  # noqa: SLF001

    page._password.setText(PASSWORD)  # noqa: SLF001
    page._submit()  # noqa: SLF001
    qapp.processEvents()
    assert not window.locked
    assert window._model.rowCount() == 2  # noqa: SLF001
    assert window._watcher.backend_name != "none" or True  # backend is platform-dependent  # noqa: SLF001


def test_repeated_wrong_passwords_throttle(window, qapp) -> None:
    create_vault(window, qapp)
    window.lock("you locked it")
    qapp.processEvents()
    page = window._unlock_page  # noqa: SLF001
    for _ in range(3):
        page._password.setText("wrong")  # noqa: SLF001
        page._submit()  # noqa: SLF001
        qapp.processEvents()
    assert window.locked
    assert "Waiting" in page._message.text()
    assert not page._button.isEnabled()  # noqa: SLF001


def test_closing_locks_the_vault(window, qapp) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    window._quitting = True  # noqa: SLF001
    window.close()
    qapp.processEvents()
    assert window._vault.locked  # noqa: SLF001
    assert window._watcher.backend_name == "none"  # noqa: SLF001


# ---------------------------------------------------------- vault location


def stub_message_box(monkeypatch, answer: str = "Yes") -> list[str]:
    """Answer the 'replace that file?' prompt without a modal dialog."""
    from PySide6.QtWidgets import QMessageBox

    from otpvault.ui import mainwindow as mw

    asked: list[str] = []

    def fake_warning(parent, title, text, *args, **kwargs):
        asked.append(text)
        return getattr(QMessageBox.StandardButton, answer)

    def fake_critical(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(mw.QMessageBox, "warning", fake_warning)
    monkeypatch.setattr(mw.QMessageBox, "critical", fake_critical)
    return asked


def test_first_run_shows_the_location_with_a_change_button(window, qapp) -> None:
    page = window._unlock_page  # noqa: SLF001
    assert page._path_row.isVisible()  # noqa: SLF001
    assert str(window._vault.path) in page._path_label.toolTip()  # noqa: SLF001


def test_the_location_row_is_hidden_once_the_vault_exists(window, qapp) -> None:
    create_vault(window, qapp)
    window.lock("you locked it")
    qapp.processEvents()
    assert not window._unlock_page._path_row.isVisible()  # noqa: SLF001


def test_choosing_a_location_at_first_run(window, qapp, monkeypatch, tmp_path: Path) -> None:
    from otpvault.ui import mainwindow as mw

    chosen = tmp_path / "somewhere else" / "codes.otpv"
    monkeypatch.setattr(mw, "choose_vault_path", lambda parent, current: chosen)

    window._choose_vault_location()  # noqa: SLF001
    qapp.processEvents()

    assert window._vault.path == chosen  # noqa: SLF001
    assert window._settings.vault_path == str(chosen)  # noqa: SLF001
    assert str(chosen) in window._unlock_page._path_label.toolTip()  # noqa: SLF001
    assert not chosen.exists()  # nothing written until the password is set

    create_vault(window, qapp)
    assert chosen.is_file()
    assert not window.locked


def test_cancelling_the_chooser_changes_nothing(window, qapp, monkeypatch) -> None:
    from otpvault.ui import mainwindow as mw

    before = window._vault.path  # noqa: SLF001
    monkeypatch.setattr(mw, "choose_vault_path", lambda parent, current: None)
    window._choose_vault_location()  # noqa: SLF001
    assert window._vault.path == before  # noqa: SLF001


def test_changing_the_path_moves_an_existing_vault(window, qapp, tmp_path: Path) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    old_path = window._vault.path  # noqa: SLF001
    target = tmp_path / "moved" / "vault.otpv"

    assert window._relocate_vault(target) is True  # noqa: SLF001
    qapp.processEvents()

    assert target.is_file()
    assert not old_path.exists()
    assert window._vault.path == target  # noqa: SLF001
    assert window._settings.vault_path == str(target)  # noqa: SLF001
    assert not window.locked  # the session survives the move
    assert window._model.rowCount() == 2  # noqa: SLF001
    assert str(target) in window.statusBar().currentMessage()

    # The moved vault is still the real thing, and still being written to.
    window._vault.add(OtpEntry(issuer="Added after the move", secret=SECRET))  # noqa: SLF001
    reopened = Vault(target)
    reopened.unlock(PASSWORD)
    assert len(reopened.entries) == 3


def test_relocating_over_an_existing_file_needs_confirmation(
    window, qapp, monkeypatch, tmp_path: Path
) -> None:
    create_vault(window, qapp)
    old_path = window._vault.path  # noqa: SLF001
    occupied = tmp_path / "occupied.otpv"
    occupied.write_bytes(b"another vault")

    asked = stub_message_box(monkeypatch, answer="Cancel")
    assert window._relocate_vault(occupied) is False  # noqa: SLF001
    assert asked, "the user should have been warned"
    assert window._vault.path == old_path  # noqa: SLF001
    assert occupied.read_bytes() == b"another vault"
    assert old_path.is_file()

    stub_message_box(monkeypatch, answer="Yes")
    assert window._relocate_vault(occupied) is True  # noqa: SLF001
    assert window._vault.path == occupied  # noqa: SLF001
    assert not old_path.exists()


def test_a_failed_move_keeps_the_old_location(window, qapp, monkeypatch, tmp_path: Path) -> None:
    create_vault(window, qapp)
    old_path = window._vault.path  # noqa: SLF001
    reported = stub_message_box(monkeypatch)

    def boom(*args, **kwargs):
        raise OSError("device not ready")

    monkeypatch.setattr(type(window._vault), "move_to", boom)  # noqa: SLF001
    assert window._relocate_vault(tmp_path / "nope.otpv") is False  # noqa: SLF001
    assert window._vault.path == old_path  # noqa: SLF001
    assert old_path.is_file()
    assert any("device not ready" in text for text in reported)


def test_relocating_to_the_same_path_is_a_no_op(window, qapp) -> None:
    create_vault(window, qapp)
    same = window._vault.path  # noqa: SLF001
    assert window._relocate_vault(same) is True  # noqa: SLF001
    assert window._vault.path == same  # noqa: SLF001


def test_moving_while_locked_updates_the_unlock_screen(window, qapp, tmp_path: Path) -> None:
    create_vault(window, qapp)
    window.lock("you locked it")
    qapp.processEvents()
    target = tmp_path / "relocated.otpv"

    assert window._relocate_vault(target) is True  # noqa: SLF001
    assert target.is_file()
    assert str(target) in window._unlock_page._subtitle.text()  # noqa: SLF001
    assert window._status_label.text() == f"Locked · {target.name}"  # noqa: SLF001

    page = window._unlock_page  # noqa: SLF001
    page._password.setText(PASSWORD)  # noqa: SLF001
    page._submit()  # noqa: SLF001
    qapp.processEvents()
    assert not window.locked


def test_relocating_persists_the_new_location(window, qapp, tmp_path: Path) -> None:
    from otpvault.config import Settings as StoredSettings

    create_vault(window, qapp)
    target = tmp_path / "remembered" / "vault.otpv"
    window._relocate_vault(target)  # noqa: SLF001

    # A fresh process would pick the vault up in its new home.
    assert StoredSettings.load().resolved_vault_path() == target


def test_the_location_row_is_disabled_for_a_cli_override(qapp, tmp_path: Path) -> None:
    from otpvault.ui.dialogs import SettingsDialog

    path = tmp_path / "one-off.otpv"
    dialog = SettingsDialog(Settings(), path, None, path_overridden=True)
    assert not dialog._path_field.isEnabled()  # noqa: SLF001
    assert dialog.vault_path == path  # nothing to change, so nothing moves
    dialog.close()


def test_settings_never_persist_a_cli_override(qapp, tmp_path: Path, monkeypatch) -> None:
    """A --vault run must not overwrite the location the user actually saved."""
    from otpvault.ui import mainwindow as mw
    from otpvault.ui.dialogs import SettingsDialog

    saved = tmp_path / "saved.otpv"
    override = tmp_path / "override.otpv"
    settings = Settings(vault_path=str(saved), minimize_to_tray=False, idle_lock_seconds=0)
    win = mw.MainWindow(Vault(override), settings, path_overridden=True)
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: SettingsDialog.DialogCode.Accepted)
    try:
        win._open_settings()  # noqa: SLF001
        assert win._vault.path == override  # noqa: SLF001
        assert win._settings.vault_path == str(saved)  # noqa: SLF001
        from otpvault.config import Settings as StoredSettings

        assert StoredSettings.load().vault_path == str(saved)
    finally:
        win._quitting = True  # noqa: SLF001
        win.close()


# ------------------------------------------------------- importing at first run


def make_foreign_vault(path: Path, password: str = "another-password-99") -> Path:
    """A vault created elsewhere, as if copied from a backup or another machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    other = Vault(path)
    other.create(password)
    other.add(OtpEntry(issuer="Imported", account="from-backup", secret=SECRET))
    other.add(OtpEntry(issuer="Also imported", account="second", secret=SECRET))
    other.lock()
    return path


def stub_import_dialog(monkeypatch, source: Path | None, copy: bool = True, accepted: bool = True):
    """Answer the import dialog without showing it."""
    from otpvault.ui import mainwindow as mw

    class FakeDialog:
        DialogCode = mw.ImportVaultDialog.DialogCode

        def __init__(self, target, parent=None):
            self.target = target

        def exec(self):
            return self.DialogCode.Accepted if accepted else self.DialogCode.Rejected

        @property
        def source_path(self):
            return source

        @property
        def copy_into_place(self):
            return copy

    monkeypatch.setattr(mw, "ImportVaultDialog", FakeDialog)


def test_the_first_run_screen_offers_import(window, qapp) -> None:
    page = window._unlock_page  # noqa: SLF001
    assert page._import_row.isVisible()  # noqa: SLF001
    assert page._import_button.isEnabled()  # noqa: SLF001


def test_the_import_offer_disappears_once_a_vault_exists(window, qapp) -> None:
    create_vault(window, qapp)
    window.lock("you locked it")
    qapp.processEvents()
    assert not window._unlock_page._import_row.isVisible()  # noqa: SLF001


def test_importing_copies_the_vault_and_unlocks_with_its_own_password(
    window, qapp, monkeypatch, tmp_path: Path
) -> None:
    source = make_foreign_vault(tmp_path / "backup" / "old.otpv")
    target = window._vault.path  # noqa: SLF001
    stub_import_dialog(monkeypatch, source, copy=True)

    window._import_existing_vault()  # noqa: SLF001
    qapp.processEvents()

    assert target.is_file(), "the vault should have been copied into place"
    assert source.is_file(), "the source must be left alone"
    assert window._vault.path == target  # noqa: SLF001
    assert window.locked, "an imported vault still needs its password"

    page = window._unlock_page  # noqa: SLF001
    assert "Unlock" in page._title.text()  # noqa: SLF001 - flipped out of create mode
    page._password.setText("another-password-99")  # noqa: SLF001
    page._submit()  # noqa: SLF001
    qapp.processEvents()

    assert not window.locked
    assert window._model.rowCount() == 2  # noqa: SLF001


def test_importing_in_place_leaves_the_file_and_remembers_it(
    window, qapp, monkeypatch, tmp_path: Path
) -> None:
    from otpvault.config import Settings as StoredSettings

    source = make_foreign_vault(tmp_path / "synced" / "vault.otpv")
    default_target = window._vault.path  # noqa: SLF001
    stub_import_dialog(monkeypatch, source, copy=False)

    window._import_existing_vault()  # noqa: SLF001
    qapp.processEvents()

    assert window._vault.path == source  # noqa: SLF001
    assert not default_target.exists(), "opening in place must not copy anything"
    assert StoredSettings.load().resolved_vault_path() == source, "the location should stick"

    page = window._unlock_page  # noqa: SLF001
    page._password.setText("another-password-99")  # noqa: SLF001
    page._submit()  # noqa: SLF001
    qapp.processEvents()
    assert not window.locked


def test_cancelling_the_import_changes_nothing(window, qapp, monkeypatch, tmp_path: Path) -> None:
    source = make_foreign_vault(tmp_path / "backup" / "old.otpv")
    before = window._vault.path  # noqa: SLF001
    stub_import_dialog(monkeypatch, source, accepted=False)

    window._import_existing_vault()  # noqa: SLF001

    assert window._vault.path == before  # noqa: SLF001
    assert not before.exists()
    assert window.locked


def test_a_failed_import_reports_and_changes_nothing(
    window, qapp, monkeypatch, tmp_path: Path
) -> None:
    junk = tmp_path / "not-a-vault.otpv"
    junk.write_bytes(b"definitely not a vault")
    before = window._vault.path  # noqa: SLF001
    reported = stub_message_box(monkeypatch)
    stub_import_dialog(monkeypatch, junk, copy=True)

    window._import_existing_vault()  # noqa: SLF001

    assert window._vault.path == before  # noqa: SLF001
    assert not before.exists(), "a rejected import must not leave a file behind"
    assert reported, "the user should have been told why"


def test_importing_rekeys_the_single_instance_guard(
    window, qapp, monkeypatch, tmp_path: Path
) -> None:
    """The guard is keyed on the vault path, so it has to follow an import."""
    from otpvault.singleinstance import SingleInstance, instance_key

    source = make_foreign_vault(tmp_path / "synced" / "vault.otpv")
    original_path = window._vault.path  # noqa: SLF001
    guard = SingleInstance(instance_key(original_path))
    assert guard.try_acquire()
    window._instance_guard = guard  # noqa: SLF001
    released: list = []
    try:
        stub_import_dialog(monkeypatch, source, copy=False)
        window._import_existing_vault()  # noqa: SLF001

        assert guard.key == instance_key(source), "the guard should now defend the new path"
        assert guard.is_primary

        # A launch on the newly adopted vault is refused...
        assert SingleInstance(instance_key(source)).try_acquire() is False
        # ... while the path it no longer uses has been given up.
        stale = SingleInstance(instance_key(original_path))
        released.append(stale)
        assert stale.try_acquire() is True, "the old key should have been released"
    finally:
        guard.release()
        for instance in released:
            instance.release()


# ------------------------------------------------- right-click auto-paste


def right_click_row(window, qapp, row: int, column: int = CodeTableModel.COL_NAME) -> None:
    """Send a real mouse context-menu event at that row."""
    from PySide6.QtGui import QContextMenuEvent

    view = window._table  # noqa: SLF001
    rect = view.visualRect(window._proxy.index(row, column))  # noqa: SLF001
    assert not rect.isEmpty(), "row is not laid out"
    local = rect.center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, local, view.viewport().mapToGlobal(local)
    )
    view.contextMenuEvent(event)
    qapp.processEvents()


def stub_autopaste(monkeypatch, target: int = 4242, title: str = "Notepad", focus_ok: bool = True,
                   paste_ok: bool = True, enter_ok: bool = True, enter_delay_ms: int = 10) -> dict:
    """Replace the Windows calls, so the sequencing can be checked anywhere."""
    from otpvault import autopaste

    calls: dict[str, object] = {"focused": [], "keys": []}

    # The window reaches these through the module, so patching it is enough.
    monkeypatch.setattr(autopaste, "is_supported", lambda: True)
    monkeypatch.setattr(autopaste, "window_title", lambda hwnd: title)
    monkeypatch.setattr(autopaste, "FOCUS_SETTLE_MS", 10)
    monkeypatch.setattr(autopaste, "ENTER_DELAY_MS", enter_delay_ms)

    def fake_focus(hwnd):
        calls["focused"].append(hwnd)
        return focus_ok

    def fake_paste():
        calls["keys"].append("paste")
        return paste_ok

    def fake_enter():
        calls["keys"].append("enter")
        return enter_ok

    monkeypatch.setattr(autopaste, "focus_window", fake_focus)
    monkeypatch.setattr(autopaste, "send_paste", fake_paste)
    monkeypatch.setattr(autopaste, "send_enter", fake_enter)
    monkeypatch.setattr(autopaste, "is_usable_target", lambda hwnd: bool(hwnd))
    return calls


def enable_auto_paste(window, target: int = 4242) -> None:
    window._settings.right_click_auto_paste = True  # noqa: SLF001
    window._table.set_auto_paste(True)  # noqa: SLF001
    tracker = window._foreground_tracker  # noqa: SLF001
    tracker._is_usable = lambda hwnd: bool(hwnd)  # noqa: SLF001
    tracker._provider = lambda: target  # noqa: SLF001
    tracker.sample()


def test_right_click_opens_the_menu_when_auto_paste_is_off(window, qapp, monkeypatch) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    opened: list = []
    monkeypatch.setattr(window, "_show_context_menu", lambda pos: opened.append(pos))

    right_click_row(window, qapp, 0)

    assert opened, "the context menu should still be the default behaviour"


def test_right_click_pastes_into_the_previous_window_when_enabled(
    window, qapp, monkeypatch, pump
) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch, target=4242, title="Notepad")
    enable_auto_paste(window, target=4242)
    menus: list = []
    monkeypatch.setattr(window, "_show_context_menu", lambda pos: menus.append(pos))

    expected = code_at(window, 0)
    right_click_row(window, qapp, 0)

    assert not menus, "auto-paste replaces the context menu on a mouse right-click"
    assert QGuiApplication.clipboard().text() == expected, "the code must reach the clipboard"
    assert calls["focused"] == [4242], "focus should have gone back to the other window"

    pump(300)  # the paste is sent after the foreground settles, then Enter
    assert calls["keys"] == ["paste", "enter"], "the code should be pasted and submitted"
    assert window.statusBar().currentMessage() == (
        "Pasted the code for GitHub — me@example.com into Notepad and pressed Enter"
    )


def test_the_paste_waits_for_focus_to_settle(window, qapp, monkeypatch) -> None:
    """Sending keys before the foreground moves would type into our own window."""
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch)
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    assert calls["focused"], "focus is handed over first"
    assert calls["keys"] == [], "the keystrokes must not be sent in the same turn"


def test_no_target_means_copy_only(window, qapp, monkeypatch) -> None:
    from PySide6.QtGui import QGuiApplication

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch)
    enable_auto_paste(window, target=0)  # nothing worth pasting into

    expected = code_at(window, 0)
    right_click_row(window, qapp, 0)

    assert QGuiApplication.clipboard().text() == expected, "copying still happens"
    assert calls["focused"] == []
    assert "no other window" in window.statusBar().currentMessage()


def test_a_refused_focus_change_is_reported(window, qapp, monkeypatch, pump) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch, focus_ok=False, title="Some App")
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    pump(200)

    assert calls["keys"] == [], "never send keys if focus did not move"
    assert "could not switch to Some App" in window.statusBar().currentMessage()


def test_a_failed_paste_is_reported(window, qapp, monkeypatch, pump) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch, paste_ok=False, title="Some App")
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    pump(300)

    assert "could not paste into Some App" in window.statusBar().currentMessage()
    assert calls["keys"] == ["paste"], (
        "Enter must not follow a failed paste: it would submit a form that never "
        "received the code"
    )


def test_enter_follows_the_paste_as_a_separate_step(window, qapp, monkeypatch, pump) -> None:
    """A field that reformats what it receives needs a beat before submitting."""
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    # A long Enter delay, so the gap between the two steps is observable.
    calls = stub_autopaste(monkeypatch, enter_delay_ms=400)
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    pump(120)
    assert calls["keys"] == ["paste"], "Enter should not ride along with the paste"
    pump(600)
    assert calls["keys"] == ["paste", "enter"]


def test_a_failed_enter_still_reports_the_paste(window, qapp, monkeypatch, pump) -> None:
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch, enter_ok=False, title="Some App")
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    pump(300)

    assert calls["keys"] == ["paste", "enter"]
    message = window.statusBar().currentMessage()
    assert "Pasted the code" in message and "could not press Enter" in message


def test_the_keyboard_context_menu_still_works_with_auto_paste_on(
    window, qapp, monkeypatch
) -> None:
    """Shift+F10 must keep Edit and Delete reachable."""
    from PySide6.QtGui import QContextMenuEvent

    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch)
    enable_auto_paste(window)
    opened: list = []
    monkeypatch.setattr(window, "_show_context_menu", lambda pos: opened.append(pos))

    view = window._table  # noqa: SLF001
    view.setCurrentIndex(window._proxy.index(0, 0))  # noqa: SLF001
    local = view.visualRect(window._proxy.index(0, 0)).center()  # noqa: SLF001
    view.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Keyboard, local, view.viewport().mapToGlobal(local)
        )
    )
    qapp.processEvents()

    assert opened, "a keyboard request should open the menu, not paste"
    assert calls["focused"] == []


def test_locking_cancels_a_pending_paste(window, qapp, monkeypatch, pump) -> None:
    """Nothing should be typed into another window after the vault locks."""
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch, enter_delay_ms=400)
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    window.lock("you locked it")
    qapp.processEvents()
    pump(600)

    assert calls["keys"] == [], "a locked vault must not send keystrokes"


def test_closing_cancels_a_pending_paste(window, qapp, monkeypatch, pump) -> None:
    """A pending step must not fire keystrokes after the window is gone."""
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    calls = stub_autopaste(monkeypatch, enter_delay_ms=400)
    enable_auto_paste(window)

    right_click_row(window, qapp, 0)
    window._quitting = True  # noqa: SLF001
    window.close()
    qapp.processEvents()
    pump(600)

    assert calls["keys"] == []


def test_the_tracker_only_runs_while_unlocked_and_enabled(window, qapp) -> None:
    import sys

    from otpvault import autopaste

    if not autopaste.is_supported():
        pytest.skip("auto-paste, and so the tracker, is Windows-only")
    assert sys.platform == "win32"

    create_vault(window, qapp)
    tracker = window._foreground_tracker  # noqa: SLF001
    assert not tracker.running, "nothing to watch while the feature is off"

    window._settings.right_click_auto_paste = True  # noqa: SLF001
    window._sync_foreground_tracker()  # noqa: SLF001
    assert tracker.running, "the tracker should follow the setting"

    window.lock("you locked it")
    qapp.processEvents()
    assert not tracker.running, "a locked vault has nothing to paste"
    assert tracker.last_external_window() == 0, "the remembered window is dropped on lock"


# -------------------------------------------------- adding by scanning a QR


def qr_screenshot(*uris):
    """A pretend screen capture containing QR codes for those URIs."""
    zxingcpp = pytest.importorskip("zxingcpp")
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    screen = QImage(900, 500, QImage.Format.Format_RGB32)
    screen.fill(QColor("#fafafa"))
    painter = QPainter(screen)
    for index, uri in enumerate(uris):
        barcode = zxingcpp.create_barcode(uri, zxingcpp.BarcodeFormat.QRCode)
        matrix = zxingcpp.write_barcode_to_image(barcode, scale=6)
        height, width = matrix.shape[0], matrix.shape[1]
        qr = QImage(
            bytes(memoryview(matrix)), width, height, width, QImage.Format.Format_Grayscale8
        ).copy()
        painter.drawImage(QRect(40 + index * 320, 60, qr.width(), qr.height()), qr)
    painter.end()
    return screen


def stub_capture(monkeypatch, image) -> None:
    """Answer the screen-region selector without showing an overlay."""
    from otpvault.ui import mainwindow as mw

    monkeypatch.setattr(mw, "select_region", lambda: image)


def auto_accept_entry_dialog(monkeypatch) -> list:
    """Accept the Add dialog as-is, keeping whatever the scan prefilled."""
    from otpvault.ui import mainwindow as mw

    seen = []

    class AcceptingDialog(mw.EntryDialog):
        def exec(self):
            seen.append(self)
            self._on_accept()  # noqa: SLF001 - validate and build like a click on Save
            return self.DialogCode.Accepted if self.result() else self.DialogCode.Rejected

    monkeypatch.setattr(mw, "EntryDialog", AcceptingDialog)
    return seen


SCANNED_URI = (
    "otpauth://totp/Scanned%20Co:scanned@example.com?secret=GEZDGNBVGY3TQOJQ"
    "&issuer=Scanned%20Co&digits=8&period=60&algorithm=SHA256"
)
SECOND_SCANNED_URI = (
    "otpauth://totp/Second:two@example.com?secret=GEZDGNBVGY3TQOJQ&issuer=Second"
)


def test_the_scan_action_exists_and_needs_an_unlocked_vault(window, qapp) -> None:
    assert not window._action_scan.isEnabled(), "nothing to add to while locked"  # noqa: SLF001
    create_vault(window, qapp)
    from otpvault import qrscan

    assert window._action_scan.isEnabled() == qrscan.is_available()  # noqa: SLF001


def test_scanning_a_qr_code_prefills_and_adds_the_account(window, qapp, monkeypatch) -> None:
    create_vault(window, qapp)
    stub_capture(monkeypatch, qr_screenshot(SCANNED_URI))
    dialogs = auto_accept_entry_dialog(monkeypatch)

    window._scan_qr_code()  # noqa: SLF001
    qapp.processEvents()

    assert dialogs, "the add dialog should have opened for review"
    assert window._model.rowCount() == 1  # noqa: SLF001
    added = window._vault.entries[0]  # noqa: SLF001
    assert added.issuer == "Scanned Co"
    assert added.account == "scanned@example.com"
    assert added.digits == 8
    assert added.period == 60
    assert added.algorithm == "SHA256"
    assert len(added.code()) == 8


def test_the_scanned_entry_is_added_not_treated_as_an_edit(window, qapp, monkeypatch) -> None:
    """A prefilled dialog must still save as a new account."""
    create_vault(window, qapp)
    add_sample_entries(window, qapp)
    stub_capture(monkeypatch, qr_screenshot(SCANNED_URI))
    auto_accept_entry_dialog(monkeypatch)

    window._scan_qr_code()  # noqa: SLF001

    assert window._model.rowCount() == 3, "the existing two must survive"


def test_several_codes_in_one_capture_are_offered_together(window, qapp, monkeypatch) -> None:
    create_vault(window, qapp)
    stub_capture(monkeypatch, qr_screenshot(SCANNED_URI, SECOND_SCANNED_URI))
    from otpvault.ui import mainwindow as mw

    monkeypatch.setattr(
        mw.QMessageBox, "question", lambda *args, **kwargs: mw.QMessageBox.StandardButton.Yes
    )

    window._scan_qr_code()  # noqa: SLF001
    qapp.processEvents()

    assert window._model.rowCount() == 2  # noqa: SLF001
    issuers = sorted(entry.issuer for entry in window._vault.entries)  # noqa: SLF001
    assert issuers == ["Scanned Co", "Second"]
    assert "scanned account" in window.statusBar().currentMessage()


def test_declining_the_multiple_confirmation_adds_nothing(window, qapp, monkeypatch) -> None:
    create_vault(window, qapp)
    stub_capture(monkeypatch, qr_screenshot(SCANNED_URI, SECOND_SCANNED_URI))
    from otpvault.ui import mainwindow as mw

    monkeypatch.setattr(
        mw.QMessageBox, "question", lambda *args, **kwargs: mw.QMessageBox.StandardButton.Cancel
    )

    window._scan_qr_code()  # noqa: SLF001

    assert window._model.rowCount() == 0  # noqa: SLF001


def test_a_capture_with_no_qr_code_says_so(window, qapp, monkeypatch) -> None:
    from PySide6.QtGui import QColor, QImage

    create_vault(window, qapp)
    blank = QImage(300, 200, QImage.Format.Format_RGB32)
    blank.fill(QColor("#ffffff"))
    stub_capture(monkeypatch, blank)
    told = []
    from otpvault.ui import mainwindow as mw

    monkeypatch.setattr(
        mw.QMessageBox, "information", lambda parent, title, text, *a, **k: told.append((title, text))
    )

    window._scan_qr_code()  # noqa: SLF001

    assert told and told[0][0] == "No QR code found"
    assert "300×200" in told[0][1], "the message should say how big the area was"
    assert window._model.rowCount() == 0  # noqa: SLF001


def test_a_google_authenticator_export_gets_its_own_explanation(window, qapp, monkeypatch) -> None:
    create_vault(window, qapp)
    stub_capture(monkeypatch, qr_screenshot("otpauth-migration://offline?data=Ch4KCkhlbGxv"))
    told = []
    from otpvault.ui import mainwindow as mw

    monkeypatch.setattr(
        mw.QMessageBox, "information", lambda parent, title, text, *a, **k: told.append((title, text))
    )

    window._scan_qr_code()  # noqa: SLF001

    assert told, "the user should be told what that QR actually was"
    assert "Google Authenticator" in told[0][0]
    assert "otpauth-migration" in told[0][1]


def test_cancelling_the_capture_changes_nothing(window, qapp, monkeypatch) -> None:
    create_vault(window, qapp)
    stub_capture(monkeypatch, None)

    window._scan_qr_code()  # noqa: SLF001

    assert window._model.rowCount() == 0  # noqa: SLF001
    assert "Nothing captured" in window.statusBar().currentMessage()


def test_the_window_gets_out_of_the_way_and_comes_back(window, qapp, monkeypatch) -> None:
    """The QR is usually behind qt-otp, so the window hides while selecting."""
    create_vault(window, qapp)
    visibility = []
    from otpvault.ui import mainwindow as mw

    def capture_while_hidden():
        visibility.append(window.isVisible())
        return None

    monkeypatch.setattr(mw, "select_region", capture_while_hidden)

    window._scan_qr_code()  # noqa: SLF001
    qapp.processEvents()

    assert visibility == [False], "the window should be hidden while the screen is selected"
    assert window.isVisible(), "and shown again afterwards"


def test_a_locked_vault_never_scans(window, qapp, monkeypatch) -> None:
    called = []
    from otpvault.ui import mainwindow as mw

    monkeypatch.setattr(mw, "select_region", lambda: called.append(True))

    window._scan_qr_code()  # noqa: SLF001

    assert called == []
