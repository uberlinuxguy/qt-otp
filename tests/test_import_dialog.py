"""The import dialog's validation: a wrong file is caught before anything moves."""

from __future__ import annotations

from pathlib import Path

import pytest

from otpvault.vault import OtpEntry, Vault

PASSWORD = "import-me-please"
SECRET = "GEZDGNBVGY3TQOJQ"


@pytest.fixture(autouse=True)
def cheap_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    from otpvault import crypto

    monkeypatch.setattr(
        crypto, "new_kdf_params", lambda: crypto.KdfParams(salt=b"0123456789abcdef", n=2**12)
    )


@pytest.fixture()
def real_vault(tmp_path: Path) -> Path:
    path = tmp_path / "elsewhere" / "existing.otpv"
    path.parent.mkdir(parents=True)
    vault = Vault(path)
    vault.create(PASSWORD)
    vault.add(OtpEntry(issuer="Imported", account="me", secret=SECRET))
    vault.lock()
    return path


@pytest.fixture()
def dialog(qapp, tmp_path: Path):
    from otpvault.ui.dialogs import ImportVaultDialog

    widget = ImportVaultDialog(tmp_path / "app" / "vault.otpv")
    yield widget
    widget.close()


def import_button(dialog):
    from PySide6.QtWidgets import QDialogButtonBox

    return dialog._buttons.button(QDialogButtonBox.StandardButton.Open)  # noqa: SLF001


def test_import_starts_disabled(dialog) -> None:
    assert not import_button(dialog).isEnabled()
    assert dialog.source_path is None


def test_a_real_vault_is_accepted_and_described(dialog, real_vault: Path) -> None:
    dialog._path_field.setText(str(real_vault))  # noqa: SLF001

    assert import_button(dialog).isEnabled()
    assert dialog.source_path == real_vault
    details = dialog._details.text()  # noqa: SLF001
    assert "qt-otp vault" in details
    assert "AES-256-GCM" in details
    assert "scrypt" in details


def test_a_file_that_is_not_a_vault_is_refused(dialog, tmp_path: Path) -> None:
    junk = tmp_path / "photo.jpg"
    junk.write_bytes(b"\xff\xd8\xff\xe0 nope")
    dialog._path_field.setText(str(junk))  # noqa: SLF001

    assert not import_button(dialog).isEnabled()
    assert dialog.source_path is None
    assert "Not a qt-otp vault" in dialog._details.text()  # noqa: SLF001


def test_a_missing_file_is_refused(dialog, tmp_path: Path) -> None:
    dialog._path_field.setText(str(tmp_path / "gone.otpv"))  # noqa: SLF001
    assert not import_button(dialog).isEnabled()
    assert "Cannot read" in dialog._details.text()  # noqa: SLF001


def test_quotes_around_a_pasted_path_are_tolerated(dialog, real_vault: Path) -> None:
    """Copy As Path in Explorer hands you a quoted string."""
    dialog._path_field.setText(f'"{real_vault}"')  # noqa: SLF001
    assert import_button(dialog).isEnabled()
    assert dialog.source_path == real_vault


def test_clearing_the_field_disables_import_again(dialog, real_vault: Path) -> None:
    dialog._path_field.setText(str(real_vault))  # noqa: SLF001
    assert import_button(dialog).isEnabled()
    dialog._path_field.clear()  # noqa: SLF001
    assert not import_button(dialog).isEnabled()
    assert dialog.source_path is None


def test_copy_is_the_default_choice(dialog, real_vault: Path) -> None:
    dialog._path_field.setText(str(real_vault))  # noqa: SLF001
    assert dialog.copy_into_place is True
    dialog._open_in_place.setChecked(True)  # noqa: SLF001
    assert dialog.copy_into_place is False


def test_copying_onto_an_existing_file_is_blocked(qapp, tmp_path: Path, real_vault: Path) -> None:
    from otpvault.ui.dialogs import ImportVaultDialog

    occupied = tmp_path / "app" / "vault.otpv"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(real_vault.read_bytes())

    widget = ImportVaultDialog(occupied)
    try:
        widget._path_field.setText(str(real_vault))  # noqa: SLF001
        widget._on_accept()  # noqa: SLF001
        assert widget.result() != ImportVaultDialog.DialogCode.Accepted
        assert "already exists" in widget._error.text()  # noqa: SLF001

        # Opening it where it is remains fine.
        widget._open_in_place.setChecked(True)  # noqa: SLF001
        widget._on_accept()  # noqa: SLF001
        assert widget.result() == ImportVaultDialog.DialogCode.Accepted
    finally:
        widget.close()
