"""Small theme-aware color helpers.

Qt's `palette(mid)` is almost invisible on dark themes, so secondary text and
error text are derived from the live palette instead of hard-coded.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget

ERROR_LIGHT_BG = "#d1242f"
ERROR_DARK_BG = "#ff7b72"


def is_dark(widget: QWidget) -> bool:
    window = widget.palette().color(QPalette.ColorRole.Window)
    return window.lightness() < 128


def muted_style(widget: QWidget, alpha: int = 160) -> str:
    """Stylesheet for secondary text that stays legible in either theme."""
    color = widget.palette().color(QPalette.ColorRole.WindowText)
    return f"color: rgba({color.red()}, {color.green()}, {color.blue()}, {alpha});"


def error_color(widget: QWidget) -> str:
    return ERROR_DARK_BG if is_dark(widget) else ERROR_LIGHT_BG


def error_style(widget: QWidget) -> str:
    return f"color: {error_color(widget)};"


MUTED_ALPHA = 160


def muted_color(widget: QWidget, alpha: int = MUTED_ALPHA) -> QColor:
    """The same secondary-text colour as `muted_style`, as a QColor."""
    color = widget.palette().color(QPalette.ColorRole.WindowText)
    color.setAlpha(alpha)
    return color
