"""Add/edit entry, change password, and settings dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import autopaste, totp
from ..config import IDLE_CHOICES, Settings
from ..vault import CryptoError, OtpEntry, Vault
from .styles import error_style, muted_style
from .unlock import MIN_PASSWORD_LENGTH, password_strength


class EntryDialog(QDialog):
    """Create or edit a single token."""

    def __init__(
        self,
        parent: QWidget | None = None,
        entry: OtpEntry | None = None,
        prefill: OtpEntry | None = None,
    ) -> None:
        """`entry` edits an existing token; `prefill` starts a new one from
        values found elsewhere — a scanned QR code, say — and still saves as an
        addition rather than an edit."""
        super().__init__(parent)
        self._entry = entry
        values = entry or prefill
        self.setWindowTitle("Edit code" if entry else "Add code")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._issuer = QLineEdit(values.issuer if values else "")
        self._issuer.setPlaceholderText("GitHub")
        form.addRow("Issuer", self._issuer)

        self._account = QLineEdit(values.account if values else "")
        self._account.setPlaceholderText("you@example.com")
        form.addRow("Account", self._account)

        secret_row = QHBoxLayout()
        self._secret = QLineEdit(values.secret if values else "")
        self._secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret.setPlaceholderText("Base32 shared secret")
        secret_row.addWidget(self._secret, 1)
        self._show_secret = QCheckBox("Show")
        self._show_secret.toggled.connect(
            lambda shown: self._secret.setEchoMode(
                QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
            )
        )
        secret_row.addWidget(self._show_secret)
        secret_widget = QWidget()
        secret_widget.setLayout(secret_row)
        form.addRow("Secret", secret_widget)

        self._algorithm = QComboBox()
        self._algorithm.addItems(sorted(totp.ALGORITHMS))
        self._algorithm.setCurrentText(values.algorithm if values else totp.DEFAULT_ALGORITHM)
        form.addRow("Algorithm", self._algorithm)

        self._digits = QComboBox()
        self._digits.addItems([str(d) for d in totp.ALLOWED_DIGITS])
        self._digits.setCurrentText(str(values.digits if values else totp.DEFAULT_DIGITS))
        form.addRow("Digits", self._digits)

        self._period = QSpinBox()
        self._period.setRange(5, 300)
        self._period.setSuffix(" s")
        self._period.setValue(values.period if values else totp.DEFAULT_PERIOD)
        form.addRow("Period", self._period)

        self._notes = QPlainTextEdit(values.notes if values else "")
        self._notes.setPlaceholderText("Optional notes (recovery codes go somewhere safer)")
        self._notes.setMaximumHeight(70)
        form.addRow("Notes", self._notes)

        layout.addLayout(form)

        import_row = QHBoxLayout()
        paste = QPushButton("Paste otpauth:// URI…")
        paste.setToolTip("Fill these fields from an otpauth:// URI (from a QR code)")
        paste.clicked.connect(self._import_uri)
        import_row.addWidget(paste)
        import_row.addStretch(1)
        layout.addLayout(import_row)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(error_style(self))
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _import_uri(self) -> None:
        clipboard = QGuiApplication.clipboard()
        prefill = clipboard.text().strip() if clipboard else ""
        if not prefill.lower().startswith("otpauth://"):
            prefill = ""
        text, ok = QInputDialog.getText(
            self, "Import otpauth URI", "otpauth:// URI:", QLineEdit.EchoMode.Normal, prefill
        )
        if not ok or not text.strip():
            return
        try:
            fields = totp.parse_otpauth_uri(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not import", str(exc))
            return
        self._issuer.setText(str(fields["issuer"]))
        self._account.setText(str(fields["account"]))
        self._secret.setText(str(fields["secret"]))
        self._algorithm.setCurrentText(str(fields["algorithm"]))
        self._digits.setCurrentText(str(fields["digits"]))
        self._period.setValue(int(fields["period"]))  # type: ignore[arg-type]

    def _on_accept(self) -> None:
        try:
            self._result = OtpEntry(
                issuer=self._issuer.text(),
                account=self._account.text(),
                secret=self._secret.text(),
                digits=int(self._digits.currentText()),
                period=self._period.value(),
                algorithm=self._algorithm.currentText(),
                notes=self._notes.toPlainText(),
                **({"id": self._entry.id, "created_at": self._entry.created_at} if self._entry else {}),
            )
        except (ValueError, totp.InvalidSecret) as exc:
            self._error.setText(str(exc))
            return
        self.accept()

    def entry(self) -> OtpEntry:
        return self._result


class ChangePasswordDialog(QDialog):
    """Ask for the current password and a new one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change master password")
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._current = QLineEdit()
        self._current.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Current password", self._current)
        self._new = QLineEdit()
        self._new.setEchoMode(QLineEdit.EchoMode.Password)
        self._new.textChanged.connect(self._on_new_changed)
        form.addRow("New password", self._new)
        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Confirm", self._confirm)
        layout.addLayout(form)

        self._strength = QProgressBar()
        self._strength.setRange(0, 100)
        self._strength.setMaximumHeight(14)
        layout.addWidget(self._strength)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(error_style(self))
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_new_changed(self, text: str) -> None:
        score, label = password_strength(text)
        self._strength.setValue(score)
        self._strength.setFormat(f"Password strength: {label}" if label else "")

    def _on_accept(self) -> None:
        if len(self._new.text()) < MIN_PASSWORD_LENGTH:
            self._error.setText(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if self._new.text() != self._confirm.text():
            self._error.setText("The new passwords do not match.")
            return
        self.accept()

    def set_error(self, text: str) -> None:
        self._error.setText(text)

    @property
    def current_password(self) -> str:
        return self._current.text()

    @property
    def new_password(self) -> str:
        return self._new.text()


class SettingsDialog(QDialog):
    """Vault location, auto-lock and clipboard preferences."""

    def __init__(
        self,
        settings: Settings,
        current_vault_path: Path,
        parent: QWidget | None = None,
        path_overridden: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(470)
        self._vault_path = Path(current_vault_path)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        self._path_field = QLineEdit(str(self._vault_path))
        self._path_field.setReadOnly(True)
        self._path_field.setCursorPosition(0)
        self._path_field.setToolTip(str(self._vault_path))
        path_row.addWidget(self._path_field, 1)
        browse = QPushButton("Change…")
        browse.clicked.connect(self._choose_path)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        if path_overridden:
            self._path_field.setEnabled(False)
            browse.setEnabled(False)
            path_widget.setToolTip("This run was started with --vault, which overrides the setting")
        form.addRow("Vault file", path_widget)

        self._idle = QComboBox()
        for seconds in IDLE_CHOICES:
            label = "Never" if seconds == 0 else (f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s")
            self._idle.addItem(label, seconds)
        index = self._idle.findData(settings.idle_lock_seconds)
        self._idle.setCurrentIndex(index if index >= 0 else self._idle.findData(300))
        form.addRow("Lock after inactivity", self._idle)

        self._clipboard = QSpinBox()
        self._clipboard.setRange(0, 300)
        self._clipboard.setSuffix(" s")
        self._clipboard.setSpecialValueText("Never")
        self._clipboard.setValue(settings.clipboard_clear_seconds)
        form.addRow("Clear clipboard after", self._clipboard)
        layout.addLayout(form)

        self._session = QCheckBox("Lock when the workstation locks")
        self._session.setChecked(settings.lock_on_session_lock)
        layout.addWidget(self._session)

        self._suspend = QCheckBox("Lock when the system sleeps")
        self._suspend.setChecked(settings.lock_on_suspend)
        layout.addWidget(self._suspend)

        self._minimize_lock = QCheckBox("Lock when the window is minimized")
        self._minimize_lock.setChecked(settings.lock_on_minimize)
        layout.addWidget(self._minimize_lock)

        self._tray = QCheckBox("Keep an icon in the system tray")
        self._tray.setChecked(settings.minimize_to_tray)
        layout.addWidget(self._tray)

        self._hide_codes = QCheckBox("Hide codes until the row is hovered")
        self._hide_codes.setChecked(settings.hide_codes_until_hover)
        layout.addWidget(self._hide_codes)

        self._auto_paste = QCheckBox(
            "Right-click a row to paste the code into the previous window and press Enter"
        )
        self._auto_paste.setChecked(settings.right_click_auto_paste and autopaste.is_supported())
        if autopaste.is_supported():
            self._auto_paste.setToolTip(
                "Copies the code, returns focus to the window you came from, sends Ctrl+V "
                "and then Enter to submit it.\n"
                "Enter submits whatever that window is, so make sure it is the one you "
                "meant — the status bar names it.\n"
                "The context menu stays available on Shift+F10."
            )
        else:
            self._auto_paste.setEnabled(False)
            self._auto_paste.setToolTip("Only available on Windows")
        layout.addWidget(self._auto_paste)

        note = QLabel(
            "Changing the vault file moves the existing vault to the new location. "
            "Workstation-lock detection uses native session notifications where "
            "available; the inactivity timer is the fallback everywhere else."
        )
        note.setWordWrap(True)
        note.setStyleSheet(muted_style(self))
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_path(self) -> None:
        chosen = choose_vault_path(self, self._vault_path)
        if chosen is None:
            return
        self._vault_path = chosen
        self._path_field.setText(str(chosen))
        self._path_field.setCursorPosition(0)
        self._path_field.setToolTip(str(chosen))

    @property
    def vault_path(self) -> Path:
        return self._vault_path

    def result_settings(self, base: Settings) -> Settings:
        return Settings(
            idle_lock_seconds=int(self._idle.currentData()),
            lock_on_session_lock=self._session.isChecked(),
            lock_on_suspend=self._suspend.isChecked(),
            lock_on_minimize=self._minimize_lock.isChecked(),
            clipboard_clear_seconds=self._clipboard.value(),
            minimize_to_tray=self._tray.isChecked(),
            hide_codes_until_hover=self._hide_codes.isChecked(),
            right_click_auto_paste=self._auto_paste.isChecked(),
            vault_path=str(self._vault_path),
        )


def choose_vault_path(parent: QWidget | None, current: Path) -> Path | None:
    """Ask where the vault file should live. Returns None if cancelled.

    Uses a save dialog with its own overwrite prompt suppressed — the caller
    decides what replacing an existing file means (it may be another vault).
    """
    dialog = QFileDialog(parent, "Vault file location", str(Path(current).parent))
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)
    dialog.setNameFilters(["Vault files (*.otpv)", "All files (*)"])
    dialog.setDefaultSuffix("otpv")
    dialog.selectFile(Path(current).name)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return None
    selected = dialog.selectedFiles()
    if not selected or not selected[0]:
        return None
    return Path(selected[0]).expanduser()


def ask_password(parent: QWidget | None, title: str, prompt: str) -> str | None:
    """Modal password prompt; returns None if cancelled."""
    text, ok = QInputDialog.getText(parent, title, prompt, QLineEdit.EchoMode.Password, "")
    if not ok:
        return None
    return text


__all__ = [
    "ChangePasswordDialog",
    "EntryDialog",
    "ImportVaultDialog",
    "SettingsDialog",
    "ask_password",
    "choose_vault_path",
]


class ImportVaultDialog(QDialog):
    """First-run import: adopt a vault file you already have.

    Two honest choices, because they are genuinely different intents: copy a
    backup into the app's own location, or open a vault where it already lives
    (a synced folder, say) and leave it there.
    """

    def __init__(self, target_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import an existing vault")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._target = Path(target_path)
        self._source: Path | None = None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Choose a vault file you already have — a backup, or a copy from "
            "another machine. You will still need its own master password."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self._path_field = QLineEdit()
        self._path_field.setPlaceholderText("Path to a .otpv vault file")
        self._path_field.textChanged.connect(self._on_path_changed)
        row.addWidget(self._path_field, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        row_widget = QWidget()
        row_widget.setLayout(row)
        layout.addWidget(row_widget)

        self._details = QLabel()
        self._details.setWordWrap(True)
        self._details.setStyleSheet(muted_style(self))
        self._details.setMinimumHeight(34)
        layout.addWidget(self._details)

        self._copy_here = QRadioButton(f"Copy it to {self._target}")
        self._copy_here.setChecked(True)
        self._copy_here.setToolTip("The file you chose is left untouched")
        layout.addWidget(self._copy_here)

        self._open_in_place = QRadioButton("Open it where it is, and remember that location")
        self._open_in_place.setToolTip("Right for a vault in a synced folder")
        layout.addWidget(self._open_in_place)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(error_style(self))
        self._error.setMinimumHeight(30)
        layout.addWidget(self._error)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Open).setText("Import")
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._set_import_enabled(False)

    # ---------------------------------------------------------------- results

    @property
    def source_path(self) -> Path | None:
        return self._source

    @property
    def copy_into_place(self) -> bool:
        return self._copy_here.isChecked()

    # -------------------------------------------------------------- internals

    def _set_import_enabled(self, enabled: bool) -> None:
        self._buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(enabled)

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a vault file",
            str(self._target.parent),
            "Vault files (*.otpv);;All files (*)",
        )
        if chosen:
            self._path_field.setText(chosen)

    def _on_path_changed(self, text: str) -> None:
        """Validate as they type, so a wrong file is caught before importing."""
        self._error.clear()
        self._details.clear()
        self._source = None
        candidate = text.strip().strip('"')
        if not candidate:
            self._set_import_enabled(False)
            return

        path = Path(candidate).expanduser()
        try:
            details = Vault.inspect_file(path)
        except OSError as exc:
            self._details.setText(f"Cannot read that file: {exc.strerror or exc}")
            self._set_import_enabled(False)
            return
        except CryptoError as exc:
            self._details.setText(f"Not a qt-otp vault: {exc}")
            self._set_import_enabled(False)
            return

        self._source = path
        self._details.setText(
            f"A qt-otp vault, version {details['version']} · {details['cipher']} · "
            f"{details['kdf']} · {details['file_bytes']} bytes"
        )
        self._set_import_enabled(True)

    def _on_accept(self) -> None:
        if self._source is None:
            self._error.setText("Choose a vault file first.")
            return
        if self.copy_into_place and self._target.exists():
            self._error.setText(
                f"{self._target} already exists. Use Tools → Settings to change the "
                "location, or open the vault where it is."
            )
            return
        if not self.copy_into_place and self._source.resolve() == self._target.resolve():
            # Nothing to do, but harmless: fall through as a normal open.
            pass
        self.accept()
