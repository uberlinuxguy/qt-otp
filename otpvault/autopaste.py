"""Hand a code straight to the window you were working in.

With this on, right-clicking a row copies the code, gives focus back to the
window that had it before you came to qt-otp, sends Ctrl+V there, and then
presses Enter to submit it.

Two things make that possible on Windows:

* the foreground window has to be remembered *before* qt-otp takes focus, so a
  tracker samples it on a timer and keeps the last window that was not ours;
* only the foreground process may hand the foreground on, which is exactly the
  position qt-otp is in when you click on it.

It is off by default. Synthetic keystrokes go wherever focus actually is, so
the code is typed — and submitted — into whatever window that turns out to be.
The caller is expected to tell the user which window was targeted.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)

SAMPLE_INTERVAL_MS = 400
#: Windows needs a moment to actually move the foreground before keys will land
#: in the new window; below ~100 ms the paste tends to hit the old one.
FOCUS_SETTLE_MS = 180
#: And a moment between the paste and Enter, so a field that validates or
#: reformats what it just received has finished before the form is submitted.
ENTER_DELAY_MS = 120

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56
    VK_RETURN = 0x0D

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        # The union must be sized by its largest member, so all three are here
        # even though only the keyboard one is ever filled in.
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int


def is_supported() -> bool:
    """Only Windows for now: the rest needs per-desktop plumbing."""
    return sys.platform == "win32"


def current_foreground_window() -> int:
    """The HWND with focus right now, or 0."""
    if not is_supported():
        return 0
    return int(_user32.GetForegroundWindow() or 0)


def window_belongs_to_us(hwnd: int) -> bool:
    if not is_supported() or not hwnd:
        return False
    owner = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
    return owner.value == _kernel32.GetCurrentProcessId()


def window_title(hwnd: int) -> str:
    """A window's title, for telling the user where a code was sent."""
    if not is_supported() or not hwnd:
        return ""
    length = _user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
    return buffer.value


def is_usable_target(hwnd: int) -> bool:
    """A window worth handing a code to: real, visible, titled, and not ours."""
    if not is_supported() or not hwnd:
        return False
    if not _user32.IsWindow(wintypes.HWND(hwnd)):
        return False
    if not _user32.IsWindowVisible(wintypes.HWND(hwnd)):
        return False
    if window_belongs_to_us(hwnd):
        return False
    return bool(window_title(hwnd))


def focus_window(hwnd: int) -> bool:
    """Give the foreground back to `hwnd`.

    Allowed because qt-otp is the foreground process at the moment the user
    clicks in it — Windows lets the foreground process pass focus on.
    """
    if not is_supported() or not hwnd:
        return False
    if not _user32.IsWindow(wintypes.HWND(hwnd)):
        return False
    return bool(_user32.SetForegroundWindow(wintypes.HWND(hwnd)))


def _key_event(vk: int, up: bool) -> "_INPUT":
    event = _INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = _KEYBDINPUT(
        wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0, time=0, dwExtraInfo=None
    )
    return event


def _send(events: tuple) -> bool:
    array = (_INPUT * len(events))(*events)
    sent = _user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))
    if sent != len(events):
        log.warning(
            "SendInput delivered %d of %d events: %s", sent, len(events), ctypes.get_last_error()
        )
        return False
    return True


def send_paste() -> bool:
    """Press Ctrl+V wherever focus now is."""
    if not is_supported():
        return False
    return _send(
        (
            _key_event(VK_CONTROL, False),
            _key_event(VK_V, False),
            _key_event(VK_V, True),
            _key_event(VK_CONTROL, True),
        )
    )


def send_enter() -> bool:
    """Press Enter wherever focus now is, to submit what was just pasted.

    Sent separately from the paste, and only after it succeeded: a form that has
    not received the code yet would otherwise be submitted empty.
    """
    if not is_supported():
        return False
    return _send((_key_event(VK_RETURN, False), _key_event(VK_RETURN, True)))


class ForegroundTracker(QObject):
    """Remembers the last foreground window that was not one of ours.

    Sampled on a timer because by the time qt-otp learns it has been activated,
    the window the user came from is already no longer the foreground one.
    """

    def __init__(self, parent: QObject | None = None, provider=None) -> None:
        super().__init__(parent)
        # Injectable so the selection rules can be tested without real windows.
        self._provider = provider or current_foreground_window
        self._is_usable = is_usable_target
        self._last_external: int = 0
        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        self._timer.timeout.connect(self.sample)

    def start(self) -> None:
        if is_supported() or self._provider is not current_foreground_window:
            self.sample()
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def sample(self) -> None:
        hwnd = int(self._provider() or 0)
        if hwnd and self._is_usable(hwnd):
            self._last_external = hwnd

    def last_external_window(self) -> int:
        """The remembered window, if it is still worth pasting into."""
        if self._last_external and self._is_usable(self._last_external):
            return self._last_external
        return 0

    def forget(self) -> None:
        self._last_external = 0


__all__ = [
    "ENTER_DELAY_MS",
    "FOCUS_SETTLE_MS",
    "ForegroundTracker",
    "current_foreground_window",
    "focus_window",
    "is_supported",
    "is_usable_target",
    "send_enter",
    "send_paste",
    "window_title",
]
