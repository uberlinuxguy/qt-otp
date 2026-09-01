"""Detect that the user walked away, so the vault can lock itself.

Three independent signals, all funnelled into `SessionWatcher`:

* Workstation lock / unlock
  - Windows: a message-only window in a worker thread registered for WTS
    session notifications (WM_WTSSESSION_CHANGE). Also catches sleep via
    WM_POWERBROADCAST.
  - Linux: org.freedesktop.login1 Session Lock/Unlock plus the screensaver's
    ActiveChanged, over DBus when QtDBus is available.
  - macOS: no native hook here; the idle timer below covers it.
* System idle — no keyboard/mouse anywhere for N seconds (GetLastInputInfo on
  Windows), falling back to in-app activity on other platforms.
* Explicit request from the UI (menu item / shortcut).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal, Slot

log = logging.getLogger(__name__)

# Events that count as "the user is still here".
_ACTIVITY_EVENTS = frozenset(
    {
        QEvent.Type.KeyPress,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseMove,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
    }
)


class SessionWatcher(QObject):
    """Emits `lockRequested(reason)` when the vault should lock itself."""

    lockRequested = Signal(str)
    sessionUnlocked = Signal()
    # Internal hop from the backend's worker thread to the GUI thread: Qt queues
    # this because the emitter runs on a different thread than this object lives on.
    _backendEvent = Signal(str)

    def __init__(
        self,
        idle_seconds: int = 300,
        watch_session_lock: bool = True,
        watch_suspend: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._idle_seconds = max(0, int(idle_seconds))
        self._watch_session_lock = watch_session_lock
        self._watch_suspend = watch_suspend
        self._armed = False
        self._last_activity = time.monotonic()

        self._backend: _LockBackend | None = None
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(5000)
        self._idle_timer.timeout.connect(self._check_idle)
        self._backendEvent.connect(self._handle_backend_event)

        app = QCoreApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ------------------------------------------------------------- settings

    def set_idle_seconds(self, seconds: int) -> None:
        self._idle_seconds = max(0, int(seconds))
        self.note_activity()

    def set_watch_session_lock(self, enabled: bool) -> None:
        self._watch_session_lock = bool(enabled)

    def set_watch_suspend(self, enabled: bool) -> None:
        self._watch_suspend = bool(enabled)

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Begin watching. Safe to call repeatedly."""
        if self._backend is None:
            self._backend = _make_backend(self._on_backend_event)
            if self._backend is not None:
                self._backend.start()
        self.note_activity()
        self._armed = True
        self._idle_timer.start()

    def stop(self) -> None:
        """Stop watching (called while already locked, and on shutdown)."""
        self._armed = False
        self._idle_timer.stop()

    def shutdown(self) -> None:
        self.stop()
        if self._backend is not None:
            self._backend.stop()
            self._backend = None
        app = QCoreApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "none"

    # -------------------------------------------------------------- signals

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() in _ACTIVITY_EVENTS:
            self._last_activity = time.monotonic()
        return super().eventFilter(obj, event)

    def _emit_lock(self, reason: str) -> None:
        if not self._armed:
            return
        self._armed = False
        self._idle_timer.stop()
        self.lockRequested.emit(reason)

    @Slot()
    def _check_idle(self) -> None:
        if not self._armed or self._idle_seconds <= 0:
            return
        idle = _system_idle_seconds()
        if idle is None:
            idle = time.monotonic() - self._last_activity
        if idle >= self._idle_seconds:
            self._emit_lock(f"no activity for {self._idle_seconds // 60 or 1} min")

    def _on_backend_event(self, kind: str) -> None:
        """Called on the backend's worker thread — signal emission only.

        Everything else (timers, arming state) has to happen on the GUI thread,
        so hand off through a queued signal.
        """
        self._backendEvent.emit(kind)

    @Slot(str)
    def _handle_backend_event(self, kind: str) -> None:
        if kind == "unlock":
            self.sessionUnlocked.emit()
            return
        if kind == "lock" and not self._watch_session_lock:
            return
        if kind == "suspend" and not self._watch_suspend:
            return
        reason = "the workstation was locked" if kind == "lock" else "the system went to sleep"
        self._emit_lock(reason)


# --------------------------------------------------------------------------
# Platform backends
# --------------------------------------------------------------------------


class _LockBackend:
    name = "none"

    def start(self) -> None: ...

    def stop(self) -> None: ...


def _make_backend(callback) -> _LockBackend | None:
    try:
        if sys.platform == "win32":
            return _WindowsBackend(callback)
        if sys.platform.startswith("linux"):
            return _DBusBackend(callback)
    except Exception as exc:  # noqa: BLE001 - never let this break startup
        log.warning("session-lock backend unavailable: %s", exc)
    return None


# ------------------------------ Windows -----------------------------------

if sys.platform == "win32":
    from ctypes import wintypes

    _WM_DESTROY = 0x0002
    _WM_CLOSE = 0x0010
    _WM_WTSSESSION_CHANGE = 0x02B1
    _WM_POWERBROADCAST = 0x0218

    _WTS_SESSION_LOCK = 0x7
    _WTS_SESSION_UNLOCK = 0x8
    _NOTIFY_FOR_THIS_SESSION = 0
    _PBT_APMSUSPEND = 0x0004

    _HWND_MESSAGE = -3
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    class _WindowsBackend(_LockBackend):
        """Message-only window listening for session-lock notifications."""

        name = "windows-wts"

        def __init__(self, callback) -> None:
            self._callback = callback
            self._hwnd: int | None = None
            self._ready = threading.Event()
            self._thread = threading.Thread(target=self._run, name="qt-otp-lockwatch", daemon=True)
            # Keep a reference: ctypes callbacks must outlive the window.
            self._wndproc = _WNDPROC(self._on_message)
            self._class_name = f"qt-otp-lockwatch-{id(self):x}"
            self._registered = False
            self._wtsapi = None

        def start(self) -> None:
            self._thread.start()
            self._ready.wait(timeout=5.0)

        def stop(self) -> None:
            hwnd = self._hwnd
            if hwnd:
                _user32.PostMessageW(wintypes.HWND(hwnd), _WM_CLOSE, 0, 0)
            self._thread.join(timeout=2.0)

        def _on_message(self, hwnd, msg, wparam, lparam):
            try:
                if msg == _WM_WTSSESSION_CHANGE:
                    if wparam == _WTS_SESSION_LOCK:
                        self._callback("lock")
                    elif wparam == _WTS_SESSION_UNLOCK:
                        self._callback("unlock")
                elif msg == _WM_POWERBROADCAST and wparam == _PBT_APMSUSPEND:
                    self._callback("suspend")
                elif msg == _WM_CLOSE:
                    self._unregister_notifications(hwnd)
                    _user32.DestroyWindow(wintypes.HWND(hwnd))
                    return 0
                elif msg == _WM_DESTROY:
                    _user32.PostQuitMessage(0)
                    return 0
            except Exception:  # noqa: BLE001 - a window proc must never raise
                log.exception("lock watcher callback failed")
            return _user32.DefWindowProcW(wintypes.HWND(hwnd), msg, wparam, lparam)

        def _unregister_notifications(self, hwnd) -> None:
            if self._wtsapi is not None:
                try:
                    self._wtsapi.WTSUnRegisterSessionNotification(wintypes.HWND(hwnd))
                except Exception:  # noqa: BLE001
                    pass

        def _run(self) -> None:
            try:
                hinst = _kernel32.GetModuleHandleW(None)
                wndclass = _WNDCLASSW()
                wndclass.lpfnWndProc = self._wndproc
                wndclass.hInstance = hinst
                wndclass.lpszClassName = self._class_name
                if not _user32.RegisterClassW(ctypes.byref(wndclass)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._registered = True

                hwnd = _user32.CreateWindowExW(
                    0, self._class_name, "qt-otp lock watcher", 0, 0, 0, 0, 0,
                    wintypes.HWND(_HWND_MESSAGE), None, hinst, None,
                )
                if not hwnd:
                    raise ctypes.WinError(ctypes.get_last_error())
                self._hwnd = int(hwnd)

                try:
                    self._wtsapi = ctypes.WinDLL("wtsapi32", use_last_error=True)
                    self._wtsapi.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
                    self._wtsapi.WTSRegisterSessionNotification.restype = wintypes.BOOL
                    self._wtsapi.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
                    self._wtsapi.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
                    if not self._wtsapi.WTSRegisterSessionNotification(hwnd, _NOTIFY_FOR_THIS_SESSION):
                        raise ctypes.WinError(ctypes.get_last_error())
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not subscribe to session notifications: %s", exc)
                    self._wtsapi = None
            except Exception:  # noqa: BLE001
                log.exception("lock watcher failed to start")
                self._ready.set()
                return
            finally:
                self._ready.set()

            msg = wintypes.MSG()
            while True:
                result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

            self._hwnd = None
            if self._registered:
                _user32.UnregisterClassW(self._class_name, _kernel32.GetModuleHandleW(None))
                self._registered = False

    def _system_idle_seconds() -> float | None:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # dwTime is a 32-bit tick count; mask the 64-bit counter to match it.
        now = _kernel32.GetTickCount64() & 0xFFFFFFFF
        elapsed = (now - info.dwTime) & 0xFFFFFFFF
        return elapsed / 1000.0

else:  # non-Windows

    def _system_idle_seconds() -> float | None:
        """No portable system-wide idle query; fall back to in-app activity."""
        return None


# -------------------------------- Linux -----------------------------------


class _DBusBackend(_LockBackend):
    """logind + screensaver signals via QtDBus (best effort)."""

    name = "dbus"

    def __init__(self, callback) -> None:
        from PySide6.QtDBus import QDBusConnection  # noqa: PLC0415 - optional dependency

        self._callback = callback
        self._connection_cls = QDBusConnection
        self._relay = _DBusRelay(callback)

    def start(self) -> None:
        QDBusConnection = self._connection_cls
        subscriptions = [
            (QDBusConnection.systemBus(), "org.freedesktop.login1.Session", "Lock", "onLock()"),
            (QDBusConnection.systemBus(), "org.freedesktop.login1.Session", "Unlock", "onUnlock()"),
            (QDBusConnection.systemBus(), "org.freedesktop.login1.Manager", "PrepareForSleep", "onSleep(bool)"),
            (QDBusConnection.sessionBus(), "org.freedesktop.ScreenSaver", "ActiveChanged", "onScreensaver(bool)"),
            (QDBusConnection.sessionBus(), "org.gnome.ScreenSaver", "ActiveChanged", "onScreensaver(bool)"),
        ]
        connected = 0
        for bus, interface, signal, slot in subscriptions:
            try:
                if bus.connect("", "", interface, signal, self._relay, f"1{slot}"):
                    connected += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("dbus subscribe failed for %s.%s: %s", interface, signal, exc)
        if not connected:
            log.info("no DBus lock signals available; relying on idle timeout")

    def stop(self) -> None:
        self._relay = None


class _DBusRelay(QObject):
    """Slot target for DBus signal subscriptions."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    @Slot()
    def onLock(self) -> None:  # noqa: N802 - DBus slot name
        self._callback("lock")

    @Slot()
    def onUnlock(self) -> None:  # noqa: N802
        self._callback("unlock")

    @Slot(bool)
    def onSleep(self, going_to_sleep: bool) -> None:  # noqa: N802
        if going_to_sleep:
            self._callback("suspend")

    @Slot(bool)
    def onScreensaver(self, active: bool) -> None:  # noqa: N802
        self._callback("lock" if active else "unlock")


__all__ = ["SessionWatcher"]
