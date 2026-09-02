"""The unlock / create-vault screen."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from .styles import error_style, muted_color, muted_style

MIN_PASSWORD_LENGTH = 8


class ElidedLabel(QLabel):
    """One-line label that elides its own middle to whatever width it is given.

    Eliding in paintEvent rather than up front is the only reliable way to do
    it: the label's final width is not known until the layout has run.
    """

    #: What the label asks for, rather than the width of the whole path: the
    #: text must not dictate how wide the card wants to be, but asking for
    #: nothing would let the card collapse around it.
    PREFERRED_WIDTH = 150
    MINIMUM_WIDTH = 60

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self.PREFERRED_WIDTH, self.fontMetrics().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self.MINIMUM_WIDTH, self.fontMetrics().height())

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self.update()

    def full_text(self) -> str:
        return self._full_text

    def elided_text(self) -> str:
        return self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, self.width()
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setPen(muted_color(self))
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.elided_text(),
        )
        painter.end()


def password_strength(password: str) -> tuple[int, str]:
    """A rough 0-100 score plus a label. Advisory only, never blocking."""
    if not password:
        return 0, ""
    score = min(len(password), 20) * 3
    classes = sum(
        (
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        )
    )
    score += (classes - 1) * 10
    if len(password) >= 16:
        score += 10
    score = max(0, min(100, score))
    if score < 40:
        return score, "weak"
    if score < 70:
        return score, "fair"
    return score, "strong"


class UnlockPage(QWidget):
    """Asks for the vault password; also handles first-run vault creation."""

    unlockRequested = Signal(str)
    createRequested = Signal(str)
    changePathRequested = Signal()
    importRequested = Signal()

    def __init__(self, vault_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._creating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 40, 48, 40)
        outer.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        card = QFrame(self)
        card.setObjectName("unlockCard")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        # A fixed band keeps the card steady whichever mode it is in, and gives
        # the elided path row a predictable amount of room.
        card.setMinimumWidth(420)
        card.setMaximumWidth(460)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(10)

        self._title = QLabel("Unlock your vault")
        title_font = QFont(self._title.font())
        title_font.setPointSize(title_font.pointSize() + 5)
        title_font.setBold(True)
        self._title.setFont(title_font)
        layout.addWidget(self._title)

        self._subtitle = QLabel()
        self._subtitle.setWordWrap(True)
        self._subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._subtitle.setStyleSheet(muted_style(self))
        # Reserve room for the longest message (a wrapped vault path) so switching
        # between unlock and create modes never clips the text.
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._subtitle.setMinimumHeight(QFontMetrics(self._subtitle.font()).lineSpacing() * 3 + 4)
        layout.addWidget(self._subtitle)
        layout.addSpacing(8)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Master password")
        self._password.setClearButtonEnabled(True)
        self._password.returnPressed.connect(self._submit)
        self._password.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._password)

        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm.setPlaceholderText("Confirm master password")
        self._confirm.returnPressed.connect(self._submit)
        self._confirm.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._confirm)

        self._strength = QProgressBar()
        self._strength.setRange(0, 100)
        self._strength.setTextVisible(True)
        self._strength.setMaximumHeight(18)
        layout.addWidget(self._strength)

        self._show = QCheckBox("Show password")
        self._show.toggled.connect(self._on_show_toggled)
        layout.addWidget(self._show)

        self._button = QPushButton("Unlock")
        self._button.setDefault(True)
        self._button.clicked.connect(self._submit)
        layout.addWidget(self._button)

        # First run only: let the user say where the vault should live.
        self._path_row = QWidget()
        path_layout = QHBoxLayout(self._path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)
        self._path_label = ElidedLabel()
        path_layout.addWidget(self._path_label, 1)
        self._path_button = QPushButton("Change…")
        self._path_button.setToolTip("Choose where the encrypted vault file is saved")
        self._path_button.clicked.connect(self.changePathRequested)
        path_layout.addWidget(self._path_button)
        layout.addWidget(self._path_row)

        # Also first run only: adopt a vault that already exists somewhere.
        self._import_row = QWidget()
        import_layout = QHBoxLayout(self._import_row)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.setSpacing(8)
        already_have = QLabel("Already have a vault?")
        already_have.setStyleSheet(muted_style(self))
        import_layout.addWidget(already_have, 1)
        self._import_button = QPushButton("Import…")
        self._import_button.setToolTip("Use a vault file from a backup or another machine")
        self._import_button.clicked.connect(self.importRequested)
        import_layout.addWidget(self._import_button)
        layout.addWidget(self._import_row)

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setMinimumHeight(34)
        layout.addWidget(self._message)

        holder = QVBoxLayout()
        holder.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(holder)
        outer.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        self.set_vault_path(vault_path)

    # ------------------------------------------------------------------ api

    def set_vault_path(self, vault_path: Path) -> None:
        self._vault_path = Path(vault_path)
        self._creating = not self._vault_path.is_file()
        self._apply_mode()

    def reset(self, message: str = "") -> None:
        """Clear inputs and show an optional status line."""
        self._password.clear()
        self._confirm.clear()
        self._show.setChecked(False)
        self.set_vault_path(self._vault_path)
        self.set_message(message, error=False)
        self._button.setEnabled(True)

    def focus_password(self) -> None:
        self._password.setFocus(Qt.FocusReason.OtherFocusReason)
        self._password.selectAll()

    def set_message(self, text: str, error: bool = True) -> None:
        self._message.setStyleSheet(error_style(self) if error else muted_style(self))
        self._message.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._button.setEnabled(not busy)
        self._button.setText("Working…" if busy else ("Create vault" if self._creating else "Unlock"))

    # ------------------------------------------------------------- internals

    def _apply_mode(self) -> None:
        creating = self._creating
        self._title.setText("Create your vault" if creating else "Unlock your vault")
        if creating:
            self._subtitle.setText(
                "Choose a master password. It encrypts every code in the vault, "
                "and there is no recovery if you forget it."
            )
        else:
            self._subtitle.setText(f"Vault: {self._vault_path}")
        self._confirm.setVisible(creating)
        self._strength.setVisible(creating)
        # The location is only settable before the vault exists; afterwards it
        # moves from Tools → Settings, which knows how to relocate the file.
        self._path_row.setVisible(creating)
        self._import_row.setVisible(creating)
        self._update_path_label()
        self._button.setText("Create vault" if creating else "Unlock")
        self._on_text_changed()

    def _update_path_label(self) -> None:
        self._path_label.set_full_text(f"Saving to {self._vault_path}")

    def _on_show_toggled(self, shown: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        self._password.setEchoMode(mode)
        self._confirm.setEchoMode(mode)

    def _on_text_changed(self) -> None:
        if not self._creating:
            self._strength.setValue(0)
            return
        score, label = password_strength(self._password.text())
        self._strength.setValue(score)
        self._strength.setFormat(f"Password strength: {label}" if label else "Password strength")

    def _submit(self) -> None:
        password = self._password.text()
        if not password:
            self.set_message("Enter your master password.")
            return
        if self._creating:
            if len(password) < MIN_PASSWORD_LENGTH:
                self.set_message(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
                return
            if password != self._confirm.text():
                self.set_message("The two passwords do not match.")
                return
            self.createRequested.emit(password)
        else:
            self.unlockRequested.emit(password)

    def clear_password(self) -> None:
        self._password.clear()
        self._confirm.clear()
