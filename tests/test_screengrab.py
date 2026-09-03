"""The region selector: what gets cut out of the frozen screen."""

from __future__ import annotations

import pytest

from otpvault.ui.screengrab import CLICK_THRESHOLD_PX, ScreenRegionSelector


def frozen_screen(width=400, height=300, ratio=1.0):
    """A stand-in screenshot with position-dependent colours, so a crop can be
    told apart from the whole image."""
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(int(width * ratio), int(height * ratio), QImage.Format.Format_RGB32)
    image.fill(QColor("#101010"))
    painter = QPainter(image)
    for x in range(0, image.width(), 20):
        painter.setPen(QColor(x % 256, 128, 200))
        painter.drawLine(x, 0, x, image.height())
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


@pytest.fixture()
def selector(qapp):
    screen = qapp.primaryScreen()
    widget = ScreenRegionSelector(screen, frozen_screen())
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def drag(widget, qapp, start, end, button=None):
    """Press, move, release — a real drag through the event system."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    button = button or Qt.MouseButton.LeftButton
    QTest.mousePress(widget, button, Qt.KeyboardModifier.NoModifier, QPoint(*start))
    QTest.mouseMove(widget, QPoint(*end))
    QTest.mouseRelease(widget, button, Qt.KeyboardModifier.NoModifier, QPoint(*end))
    qapp.processEvents()


def test_a_drag_captures_that_rectangle(selector, qapp) -> None:
    captured = []
    selector.regionChosen.connect(captured.append)

    drag(selector, qapp, (100, 60), (220, 160))

    assert len(captured) == 1
    image = captured[0]
    assert (image.width(), image.height()) == (120, 100)


def test_the_captured_pixels_come_from_the_selected_place(selector, qapp) -> None:
    """Not just the right size: the right part of the screen."""
    captured = []
    selector.regionChosen.connect(captured.append)
    expected = selector._frozen.copy(100, 60, 120, 100)  # noqa: SLF001

    drag(selector, qapp, (100, 60), (220, 160))

    assert captured[0] == expected


def test_a_backwards_drag_is_normalised(selector, qapp) -> None:
    captured = []
    selector.regionChosen.connect(captured.append)

    drag(selector, qapp, (220, 160), (100, 60))  # dragged up and to the left

    assert (captured[0].width(), captured[0].height()) == (120, 100)


def test_a_click_captures_the_whole_screen(selector, qapp) -> None:
    captured = []
    selector.regionChosen.connect(captured.append)

    drag(selector, qapp, (150, 150), (150 + CLICK_THRESHOLD_PX - 2, 150))

    assert len(captured) == 1
    assert captured[0] == selector._frozen  # noqa: SLF001


def test_a_high_dpi_selection_captures_device_pixels(qapp) -> None:
    """On a scaled display the frozen image is bigger than the widget."""
    screen = qapp.primaryScreen()
    widget = ScreenRegionSelector(screen, frozen_screen(ratio=2.0))
    widget.show()
    qapp.processEvents()
    captured = []
    widget.regionChosen.connect(captured.append)
    try:
        drag(widget, qapp, (50, 50), (150, 130))
        # 100x80 logical becomes 200x160 real pixels, which is what a decoder wants.
        assert (captured[0].width(), captured[0].height()) == (200, 160)
    finally:
        widget.close()


def test_a_selection_beyond_the_edge_is_clipped(selector, qapp) -> None:
    captured = []
    selector.regionChosen.connect(captured.append)

    drag(selector, qapp, (350, 250), (600, 500))  # past the bottom-right corner

    image = captured[0]
    assert image.width() <= selector._frozen.width()  # noqa: SLF001
    assert image.height() <= selector._frozen.height()  # noqa: SLF001
    assert not image.isNull()


def test_right_click_cancels(selector, qapp) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    cancelled = []
    selector.cancelled.connect(lambda: cancelled.append(True))

    QTest.mousePress(selector, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(100, 100))
    qapp.processEvents()

    assert cancelled == [True]


def test_escape_cancels(selector, qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    cancelled = []
    selector.cancelled.connect(lambda: cancelled.append(True))

    QTest.keyClick(selector, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert cancelled == [True]


def test_select_region_returns_nothing_without_screens(qapp) -> None:
    from otpvault.ui.screengrab import select_region

    assert select_region(frozen=[]) is None


def test_the_undimmed_cutout_is_exactly_the_selection(qapp) -> None:
    """What looks selected must be what gets captured.

    Probed by brightness: the overlay draws the frozen screen, dims everything
    outside the selection, and leaves the selection untouched.
    """
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QColor, QImage

    frozen = QImage(400, 300, QImage.Format.Format_RGB32)
    frozen.fill(QColor("#ffffff"))
    widget = ScreenRegionSelector(qapp.primaryScreen(), frozen)
    widget.setGeometry(QRect(0, 0, 400, 300))
    widget.show()
    qapp.processEvents()
    try:
        widget._origin = QPoint(100, 80)  # noqa: SLF001
        widget._current = QPoint(300, 200)  # noqa: SLF001
        widget.update()
        qapp.processEvents()
        shot = widget.grab().toImage()
        selection = widget._selection()  # noqa: SLF001

        def brightness(x, y):
            return QColor(shot.pixel(x, y)).value()

        assert brightness(selection.center().x(), selection.center().y()) == 255
        assert brightness(selection.left() + 3, selection.top() + 3) == 255
        assert brightness(selection.right() - 3, selection.bottom() - 3) == 255
        for outside in (
            (selection.left() - 6, selection.center().y()),
            (selection.right() + 6, selection.center().y()),
            (selection.center().x(), selection.top() - 6),
            (selection.center().x(), selection.bottom() + 6),
        ):
            assert brightness(*outside) < 255, f"{outside} should be dimmed"
    finally:
        widget.close()
