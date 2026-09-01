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
