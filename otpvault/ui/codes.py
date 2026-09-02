"""Table model, countdown delegate and view for the live code list."""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPoint,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QTableView

from ..totp import InvalidSecret, format_code
from ..vault import OtpEntry

HIDDEN_CODE = "••• •••"


class CodeTableModel(QAbstractTableModel):
    """Rows of entries with codes recomputed on demand."""

    COL_NAME, COL_CODE, COL_TIMER = range(3)
    HEADERS = ("Account", "Code", "Expires")

    EntryIdRole = Qt.ItemDataRole.UserRole + 1
    CodeRole = Qt.ItemDataRole.UserRole + 2
    RemainingRole = Qt.ItemDataRole.UserRole + 3
    FractionRole = Qt.ItemDataRole.UserRole + 4
    SearchRole = Qt.ItemDataRole.UserRole + 5
    UriRole = Qt.ItemDataRole.UserRole + 6

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[OtpEntry] = []
        self._codes: list[tuple[str, float, float]] = []
        self._hide_codes = False
        self._revealed_row = -1

    # ------------------------------------------------------------------ data

    def set_entries(self, entries: list[OtpEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self._revealed_row = -1
        self._recompute()
        self.endResetModel()

    def clear(self) -> None:
        self.set_entries([])

    def entry_at(self, row: int) -> OtpEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def row_of(self, entry_id: str) -> int:
        for row, entry in enumerate(self._entries):
            if entry.id == entry_id:
                return row
        return -1

    def set_hide_codes(self, hide: bool) -> None:
        if hide == self._hide_codes:
            return
        self._hide_codes = hide
        self._emit_code_columns_changed()

    def set_revealed_row(self, row: int) -> None:
        if row == self._revealed_row:
            return
        self._revealed_row = row
        if self._hide_codes:
            self._emit_code_columns_changed()

    def _emit_code_columns_changed(self) -> None:
        if not self._entries:
            return
        top = self.index(0, self.COL_CODE)
        bottom = self.index(len(self._entries) - 1, self.COL_TIMER)
        self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole, self.CodeRole])

    def _recompute(self) -> None:
        now = time.time()
        codes: list[tuple[str, float, float]] = []
        for entry in self._entries:
            try:
                code = entry.code(at=now)
                remaining = entry.remaining(at=now)
                fraction = max(0.0, min(1.0, remaining / entry.period))
            except (InvalidSecret, ValueError):
                code, remaining, fraction = "error", 0.0, 0.0
            codes.append((code, remaining, fraction))
        self._codes = codes

    def refresh(self) -> None:
        """Recompute codes/countdowns and repaint the value columns."""
        if not self._entries:
            return
        self._recompute()
        self._emit_code_columns_changed()

    # -------------------------------------------------------- Qt model API

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        entry = self._entries[row]
        code, remaining, fraction = self._codes[row]
        masked = self._hide_codes and row != self._revealed_row

        if role == Qt.ItemDataRole.DisplayRole:
            if column == self.COL_NAME:
                return entry.label
            if column == self.COL_CODE:
                return HIDDEN_CODE if masked else format_code(code)
            return ""  # the timer column is painted by the delegate

        if role == Qt.ItemDataRole.ToolTipRole:
            details = f"{entry.algorithm}, {entry.digits} digits, {entry.period}s period"
            if entry.notes:
                return f"{entry.notes}\nClick to copy · {details}"
            return f"Click to copy · {details}"

        if role == Qt.ItemDataRole.FontRole and column == self.COL_CODE:
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            font.setPointSize(font.pointSize() + 3)
            font.setBold(True)
            return font

        if role == Qt.ItemDataRole.TextAlignmentRole and column != self.COL_NAME:
            return int(Qt.AlignmentFlag.AlignCenter)

        if role == self.EntryIdRole:
            return entry.id
        if role == self.CodeRole:
            return code
        if role == self.RemainingRole:
            return remaining
        if role == self.FractionRole:
            return fraction
        if role == self.SearchRole:
            return f"{entry.issuer} {entry.account} {entry.notes}"
        if role == self.UriRole:
            return entry.to_uri()
        return None


class CodeFilterProxy(QSortFilterProxyModel):
    """Case-insensitive search over issuer/account/notes."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""

    def set_search_text(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        # invalidate() rather than the protected invalidate*Filter() variants,
        # which PySide6 flags as deprecated.
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        haystack = str(model.data(index, CodeTableModel.SearchRole) or "").lower()
        return all(part in haystack for part in self._needle.split())


class CountdownDelegate(QStyledItemDelegate):
    """Draws the remaining-seconds bar in the timer column."""

    NORMAL = QColor("#2f81f7")
    WARN = QColor("#d29922")
    URGENT = QColor("#d1242f")
    TRACK = QColor(128, 128, 128, 60)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(90, 34)

    def paint(self, painter: QPainter, option, index) -> None:
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        remaining = index.data(CodeTableModel.RemainingRole)
        fraction = index.data(CodeTableModel.FractionRole)
        if remaining is None or fraction is None:
            return

        color = self.NORMAL
        if remaining <= 5:
            color = self.URGENT
        elif remaining <= 10:
            color = self.WARN

        track = self.TRACK
        if option.state & QStyle.StateFlag.State_Selected:
            # Accent-on-highlight has no contrast; borrow the selection's own colors.
            color = option.palette.color(QPalette.ColorRole.HighlightedText)
            track = QColor(color)
            track.setAlpha(70)

        rect = QRectF(option.rect).adjusted(10, 0, -10, 0)
        bar_height = 6.0
        bar = QRectF(rect.left(), rect.center().y() + 3, rect.width(), bar_height)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, bar_height / 2, bar_height / 2)
        filled = QRectF(bar)
        filled.setWidth(max(0.0, bar.width() * float(fraction)))
        painter.setBrush(color)
        painter.drawRoundedRect(filled, bar_height / 2, bar_height / 2)

        label_rect = QRectF(rect.left(), rect.top() + 2, rect.width(), rect.height() / 2)
        font = QFont(option.font)
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(QPen(color))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, f"{int(float(remaining)) + 1}s")
        painter.restore()


class CodeTableView(QTableView):
    """Table with hover tracking (for hidden-code reveal) and copy-on-activate."""

    copyRequested = Signal(QModelIndex)
    editRequested = Signal(QModelIndex)
    autoPasteRequested = Signal(QModelIndex)
    contextMenuRequested = Signal(QPoint)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(36)
        self.horizontalHeader().setHighlightSections(False)
        # DefaultContextMenu (not CustomContextMenu) so contextMenuEvent can
        # tell a mouse right-click from the keyboard's context-menu request.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self._auto_paste = False
        self.entered.connect(self._on_entered)
        # A single left-click anywhere on a row copies that row's code. `clicked`
        # covers double-clicks too, so it is the only connection needed.
        self.clicked.connect(self.copyRequested)

    def _source_model(self) -> CodeTableModel | None:
        model = self.model()
        if isinstance(model, QSortFilterProxyModel):
            model = model.sourceModel()
        return model if isinstance(model, CodeTableModel) else None

    def _on_entered(self, index: QModelIndex) -> None:
        source = self._source_model()
        if source is None:
            return
        model = self.model()
        row = index.row()
        if isinstance(model, QSortFilterProxyModel):
            row = model.mapToSource(index).row()
        source.set_revealed_row(row)

    def set_auto_paste(self, enabled: bool) -> None:
        """When on, a right-click pastes instead of opening the menu."""
        self._auto_paste = bool(enabled)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        from PySide6.QtGui import QContextMenuEvent

        # Round-trip through global coordinates: whether the event arrives on
        # the view or its viewport, indexAt() wants viewport coordinates.
        position = self.viewport().mapFromGlobal(event.globalPos())
        index = self.indexAt(position)
        by_mouse = event.reason() == QContextMenuEvent.Reason.Mouse
        if self._auto_paste and by_mouse and index.isValid():
            self.setCurrentIndex(index)
            self.autoPasteRequested.emit(index)
            event.accept()
            return
        # Keyboard requests (Shift+F10, the menu key) always get the menu, so
        # Edit and Delete stay reachable while auto-paste is on.
        self.contextMenuRequested.emit(position)
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        source = self._source_model()
        if source is not None:
            source.set_revealed_row(-1)
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        index = self.currentIndex()
        if index.isValid():
            if event.matches(QKeySequence.StandardKey.Copy) or (
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self.copyRequested.emit(index)
                return
        super().keyPressEvent(event)
