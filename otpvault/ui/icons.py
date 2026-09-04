"""Application icons.

The app ships `otpvault/resources/qt-otp-icon.svg` and renders it once per size
Windows and the tray actually ask for. Every size is drawn from the vector
source at its native resolution, so nothing is downscaled from a bitmap and the
16px tray icon stays as crisp as the 256px one. Sizes are still baked into the
QIcon rather than rendered on demand, so the locked wash below has real pixels
to work on.

The renderer letterboxes onto a transparent square: taskbars and tray areas lay
out square icons, and stretching the shield to fill a non-square box would look
worse. The shipped artwork is already square, so this is a no-op for it.

The locked variant is the same artwork under a grey wash, so a glance at the
taskbar or tray says whether the vault is open. If the file is missing or fails
to parse, the drawn fallback below keeps the app usable.

`about_pixmap` renders a second, larger piece of artwork — the cat holding the
shield, the same drawing the README opens with — for the About dialog. It is
not square and not letterboxed: the dialog has room for the whole thing.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

RESOURCE_DIR = Path(__file__).resolve().parent.parent / "resources"
ICON_PATH = RESOURCE_DIR / "qt-otp-icon.svg"
ABOUT_PATH = RESOURCE_DIR / "qt-otp-about.svg"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ABOUT_HEIGHT = 132

FALLBACK_ACCENT = QColor("#2f6b45")
FALLBACK_DIM = QColor("#8a94a6")
LOCKED_WASH = QColor(148, 148, 148, 135)

# QPixmap needs a QGuiApplication, so icons are built lazily and cached. The
# cache is dropped on aboutToQuit: pixmaps must not outlive the application.
_cache: dict[str, QIcon] = {}
_cleanup_connected = False


def _cached(key: str, build) -> QIcon:
    global _cleanup_connected
    icon = _cache.get(key)
    if icon is None:
        icon = build()
        _cache[key] = icon
        app = QCoreApplication.instance()
        if app is not None and not _cleanup_connected:
            app.aboutToQuit.connect(_cache.clear)
            _cleanup_connected = True
    return icon


def _source_renderer() -> QSvgRenderer | None:
    if not ICON_PATH.is_file():
        return None
    renderer = QSvgRenderer(str(ICON_PATH))
    return renderer if renderer.isValid() else None


def _target_rect(renderer: QSvgRenderer, size: int) -> QRectF:
    """The size x size box the artwork is drawn into, centred and unstretched."""
    default = renderer.defaultSize()
    if default.isEmpty():
        return QRectF(0, 0, size, size)
    scale = min(size / default.width(), size / default.height())
    width = default.width() * scale
    height = default.height() * scale
    return QRectF((size - width) / 2, (size - height) / 2, width, height)


def _square_pixmap(renderer: QSvgRenderer, size: int) -> QPixmap:
    """Render the vector source straight to a size x size transparent square."""
    square = QPixmap(size, size)
    square.fill(Qt.GlobalColor.transparent)
    painter = QPainter(square)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, _target_rect(renderer, size))
    painter.end()
    return square


def _greyed(pixmap: QPixmap) -> QPixmap:
    """Wash the artwork with grey, keeping its alpha shape."""
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    painter.fillRect(result.rect(), LOCKED_WASH)
    painter.end()
    return result


def _from_file(transform=None) -> QIcon | None:
    renderer = _source_renderer()
    if renderer is None:
        return None
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = _square_pixmap(renderer, size)
        icon.addPixmap(transform(pixmap) if transform else pixmap)
    return icon


def app_icon() -> QIcon:
    """The window, taskbar and tray icon."""
    return _cached("app", lambda: _from_file() or _drawn_icon(FALLBACK_ACCENT))


def locked_icon() -> QIcon:
    """A greyed variant shown while the vault is locked."""
    return _cached("locked", lambda: _from_file(_greyed) or _drawn_icon(FALLBACK_DIM))


def about_pixmap(height: int = ABOUT_HEIGHT, ratio: float = 1.0) -> QPixmap:
    """The About dialog artwork, `height` logical pixels tall.

    `ratio` is the dialog's device pixel ratio: the vector is rendered at that
    many real pixels per logical one, so the drawing stays sharp on a HiDPI
    screen. Falls back to the app icon if the artwork is missing.
    """
    renderer = QSvgRenderer(str(ABOUT_PATH)) if ABOUT_PATH.is_file() else None
    if renderer is None or not renderer.isValid():
        return app_icon().pixmap(height, height)
    default = renderer.defaultSize()
    if default.isEmpty():
        return app_icon().pixmap(height, height)
    width = max(1, round(height * default.width() / default.height()))
    ratio = max(1.0, ratio)
    pixmap = QPixmap(round(width * ratio), round(height * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, width * ratio, height * ratio))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


# --------------------------------------------------------------------------
# Fallback: drawn in code, used only when the resource file is unavailable.
# --------------------------------------------------------------------------


def _drawn_pixmap(size: int, color: QColor, glyph: str = "•••") -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.24, size * 0.24)
    painter.setPen(QPen(QColor("#ffffff")))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.58))
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return pixmap


def _drawn_icon(color: QColor) -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(_drawn_pixmap(size, color))
    return icon
