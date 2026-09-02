"""Handing a code back to the window the user came from."""

from __future__ import annotations

import sys

import pytest

from otpvault import autopaste

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="auto-paste is Windows-only")


def test_support_matches_the_platform() -> None:
    assert autopaste.is_supported() == (sys.platform == "win32")


@windows_only
def test_the_current_foreground_window_can_be_read() -> None:
    hwnd = autopaste.current_foreground_window()
    assert isinstance(hwnd, int)
    if hwnd:
        assert isinstance(autopaste.window_title(hwnd), str)


@windows_only
def test_our_own_windows_are_recognised(qapp) -> None:
    """winId() is only a real HWND under the native platform plugin.

    The suite runs offscreen, where it is a placeholder, so this needs a real
    windowing session. The packaged build covers it natively via --selftest.
    """
    if qapp.platformName() != "windows":
        pytest.skip(f"needs the native platform plugin (running on {qapp.platformName()!r})")

    from PySide6.QtWidgets import QWidget

    widget = QWidget()
    widget.setWindowTitle("a window of ours")
    widget.show()
    qapp.processEvents()
    try:
        hwnd = int(widget.winId())
        assert autopaste.window_belongs_to_us(hwnd) is True
        assert autopaste.is_usable_target(hwnd) is False, "never paste into ourselves"
    finally:
        widget.close()


@windows_only
def test_a_dead_window_handle_is_not_a_target() -> None:
    assert autopaste.is_usable_target(0) is False
    assert autopaste.is_usable_target(0x7FFFFFFF) is False  # implausible handle


@windows_only
def test_focus_and_paste_refuse_a_dead_handle() -> None:
    assert autopaste.focus_window(0) is False
    assert autopaste.focus_window(0x7FFFFFFF) is False


# ------------------------------------------------------------------- tracker


class FakeWindows:
    """A stand-in foreground, so the selection rules can be tested directly."""

    def __init__(self, sequence: list[int]) -> None:
        self.sequence = list(sequence)
        self.current = 0

    def __call__(self) -> int:
        if self.sequence:
            self.current = self.sequence.pop(0)
        return self.current


def tracker_with(sequence: list[int], usable=lambda hwnd: True) -> autopaste.ForegroundTracker:
    tracker = autopaste.ForegroundTracker(provider=FakeWindows(sequence))
    tracker._is_usable = usable  # noqa: SLF001
    return tracker


def test_the_tracker_remembers_the_last_usable_window(qapp) -> None:
    tracker = tracker_with([111, 222, 333])
    for _ in range(3):
        tracker.sample()
    assert tracker.last_external_window() == 333


def test_the_tracker_ignores_windows_it_should_not_target(qapp) -> None:
    """Our own windows, and anything unusable, must not become the target."""
    ours = {222}
    tracker = tracker_with([111, 222, 222], usable=lambda hwnd: hwnd not in ours)
    for _ in range(3):
        tracker.sample()
    assert tracker.last_external_window() == 111, "it should have kept the last good one"


def test_a_zero_foreground_is_ignored(qapp) -> None:
    tracker = tracker_with([444, 0, 0])
    for _ in range(3):
        tracker.sample()
    assert tracker.last_external_window() == 444


def test_a_remembered_window_that_has_closed_is_dropped(qapp) -> None:
    alive = {555}
    tracker = tracker_with([555], usable=lambda hwnd: hwnd in alive)
    tracker.sample()
    assert tracker.last_external_window() == 555
    alive.clear()  # the window has since closed
    assert tracker.last_external_window() == 0


def test_forget_clears_the_target(qapp) -> None:
    tracker = tracker_with([666])
    tracker.sample()
    assert tracker.last_external_window() == 666
    tracker.forget()
    assert tracker.last_external_window() == 0


def test_start_and_stop_control_the_timer(qapp, pump) -> None:
    tracker = tracker_with([777, 888])
    assert not tracker.running
    tracker.start()
    assert tracker.running
    tracker.stop()
    assert not tracker.running


def test_the_timer_keeps_sampling_while_running(qapp, pump) -> None:
    tracker = tracker_with([1, 2, 3, 4, 5, 6, 7, 8])
    tracker._timer.setInterval(20)  # noqa: SLF001
    tracker.start()
    pump(300)
    tracker.stop()
    assert tracker.last_external_window() > 1, "the timer never fired"


@windows_only
def test_enter_is_a_separate_key_sequence() -> None:
    """send_enter must not smuggle the paste in with it, or vice versa."""
    import inspect

    paste_source = inspect.getsource(autopaste.send_paste)
    enter_source = inspect.getsource(autopaste.send_enter)
    assert "VK_V" in paste_source and "VK_RETURN" not in paste_source
    assert "VK_RETURN" in enter_source and "VK_V" not in enter_source


def test_send_enter_is_a_no_op_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(autopaste, "is_supported", lambda: False)
    assert autopaste.send_enter() is False
    assert autopaste.send_paste() is False


def test_the_enter_delay_leaves_room_for_the_field_to_settle() -> None:
    assert autopaste.ENTER_DELAY_MS > 0
    assert autopaste.FOCUS_SETTLE_MS >= 100, "below ~100ms the paste hits the old window"
