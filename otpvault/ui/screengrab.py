"""Pick a region of the screen, snipping-tool style.

Each screen is frozen into an image first, and the overlay draws that image
back. Dimming and the selection rectangle are then painted over a still
picture, and the pixels handed to the decoder are the ones that were on screen
when the user asked — not whatever the overlay happens to be covering.

Captures at native device pixels: a QR code shown small needs every pixel it
has to decode, so nothing is scaled down on the way through.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

#: A drag shorter than this in either direction counts as a click, which means
#: "just use this whole screen" — handy when the QR is the only thing on it.
CLICK_THRESHOLD_PX = 8

DIM = QColor(0, 0, 0, 110)
BORDER = QColor("#2f81f7")
HINT_BACKGROUND = QColor(0, 0, 0, 190)
HINT_TEXT = QColor("#ffffff")
HINT = "Drag around the QR code · click for this whole screen · Esc to cancel"


class ScreenRegionSelector(QWidget):
    """A full-screen overlay over one screen, showing a frozen copy of it."""

    regionChosen = Signal(QImage)
    cancelled = Signal()

    def __init__(self, screen, frozen: QImage) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setWindowTitle("qt-otp — select the QR code")

        self._screen = screen
        self._frozen = frozen
        self._ratio = frozen.devicePixelRatio() or 1.0
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        self.setGeometry(screen.geometry())
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.cancelled)

    # ------------------------------------------------------------------ input

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._current = event.position().toPoint()
        selection = self._selection()
        if selection.width() < CLICK_THRESHOLD_PX or selection.height() < CLICK_THRESHOLD_PX:
            # Treated as a click: hand over the whole screen.
            self.regionChosen.emit(self._frozen)
            return
        self.regionChosen.emit(self._crop(selection))

    def _selection(self) -> QRect:
        """The dragged rectangle, sized by the distance moved.

        Built from a size rather than two corners: QRect(p1, p2) treats both as
        inclusive, which would capture a pixel more than was dragged in each
        direction.
        """
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(
            min(self._origin.x(), self._current.x()),
            min(self._origin.y(), self._current.y()),
            abs(self._current.x() - self._origin.x()),
            abs(self._current.y() - self._origin.y()),
        )

    def _crop(self, logical: QRect) -> QImage:
        """Map a selection in widget coordinates onto the frozen pixels."""
        ratio = self._ratio
        device = QRect(
            int(logical.x() * ratio),
            int(logical.y() * ratio),
            max(1, int(logical.width() * ratio)),
            max(1, int(logical.height() * ratio)),
        )
        cropped = self._frozen.copy(device.intersected(self._frozen.rect()))
        log.info(
            "captured %dx%d device pixels from %s", cropped.width(), cropped.height(), self._screen.name()
        )
        return cropped

    # ----------------------------------------------------------------- paint

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.drawImage(self.rect(), self._frozen)
        selection = self._selection()

        if selection.isNull():
            painter.fillRect(self.rect(), DIM)
        else:
            # Dim everything except the selection, so it reads as a cut-out.
            for band in (
                QRect(0, 0, self.width(), selection.top()),
                QRect(0, selection.bottom() + 1, self.width(), self.height() - selection.bottom()),
                QRect(0, selection.top(), selection.left(), selection.height()),
                QRect(
                    selection.right() + 1,
                    selection.top(),
                    self.width() - selection.right(),
                    selection.height(),
                ),
            ):
                painter.fillRect(band, DIM)
            painter.setPen(QPen(BORDER, 2))
            painter.drawRect(selection)

        self._paint_hint(painter)
        painter.end()

    def _paint_hint(self, painter: QPainter) -> None:
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(HINT)
        box = QRect(0, 0, text_width + 28, metrics.height() + 18)
        box.moveCenter(QPoint(self.width() // 2, 0))
        box.moveTop(40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(HINT_BACKGROUND)
        painter.drawRoundedRect(box, 8, 8)
        painter.setPen(QPen(HINT_TEXT))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, HINT)


def freeze_screens() -> list[tuple[object, QImage]]:
    """Grab every screen at native resolution, newest state first."""
    frozen = []
    for screen in QGuiApplication.screens():
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            log.warning("could not grab %s", screen.name())
            continue
        image = pixmap.toImage()
        image.setDevicePixelRatio(pixmap.devicePixelRatio())
        frozen.append((screen, image))
    return frozen


def select_region(frozen=None) -> QImage | None:
    """Show the overlay and block until the user picks a region or cancels.

    `frozen` is injectable so the flow can be driven in tests without a screen.
    Returns the captured image, or None if cancelled.
    """
    frames = freeze_screens() if frozen is None else list(frozen)
    if not frames:
        return None

    loop = QEventLoop()
    captured: list[QImage] = []
    overlays: list[ScreenRegionSelector] = []

    def finish(image: QImage | None) -> None:
        if image is not None:
            captured.append(image)
        for overlay in overlays:
            overlay.close()
        if loop.isRunning():
            loop.quit()

    for screen, image in frames:
        overlay = ScreenRegionSelector(screen, image)
        overlay.regionChosen.connect(finish)
        overlay.cancelled.connect(lambda: finish(None))
        overlays.append(overlay)

    for overlay in overlays:
        overlay.show()
        overlay.raise_()
    if overlays:
        overlays[0].activateWindow()
        overlays[0].setFocus()

    loop.exec()
    return captured[0] if captured else None


__all__ = ["CLICK_THRESHOLD_PX", "ScreenRegionSelector", "freeze_screens", "select_region"]
