"""The auto-lock triggers.

The Windows cases drive the real message-only window by posting the same
messages the OS sends on Win+L, so the whole path — window proc, worker thread,
queued signal — is under test.
"""

from __future__ import annotations

import sys

import pytest

from otpvault import lockwatch
from otpvault.lockwatch import SessionWatcher

WM_WTSSESSION_CHANGE = 0x02B1
WM_POWERBROADCAST = 0x0218
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
PBT_APMSUSPEND = 0x0004

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows session notifications")


@pytest.fixture()
def watcher(qapp, pump):
    """A started watcher with the idle timer disabled."""
    watch = SessionWatcher(idle_seconds=0)
    events: list[str] = []
    unlocks: list[bool] = []
    watch.lockRequested.connect(events.append)
    watch.sessionUnlocked.connect(lambda: unlocks.append(True))
    watch.start()
    pump(300)
    watch.events = events  # type: ignore[attr-defined]
    watch.unlocks = unlocks  # type: ignore[attr-defined]
    yield watch
    watch.shutdown()
    pump(200)


def post(hwnd: int, msg: int, wparam: int) -> None:
    import ctypes
    from ctypes import wintypes

    ok = ctypes.windll.user32.PostMessageW(
        wintypes.HWND(hwnd), msg, wintypes.WPARAM(wparam), wintypes.LPARAM(0)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


@windows_only
def test_backend_registers_for_session_notifications(watcher) -> None:
    assert watcher.backend_name == "windows-wts"
    assert watcher._backend._hwnd  # noqa: SLF001 - the window is the thing under test
    assert watcher._backend._wtsapi is not None  # noqa: SLF001


@windows_only
def test_workstation_lock_requests_a_lock(watcher, pump) -> None:
    post(watcher._backend._hwnd, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK)  # noqa: SLF001
    pump(500)
    assert watcher.events == ["the workstation was locked"]


@windows_only
def test_session_unlock_does_not_request_a_lock(watcher, pump) -> None:
    post(watcher._backend._hwnd, WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK)  # noqa: SLF001
    pump(500)
    assert watcher.unlocks == [True]
    assert watcher.events == []


@windows_only
def test_suspend_requests_a_lock(watcher, pump) -> None:
    post(watcher._backend._hwnd, WM_POWERBROADCAST, PBT_APMSUSPEND)  # noqa: SLF001
    pump(500)
    assert watcher.events == ["the system went to sleep"]


@windows_only
def test_disabled_triggers_are_ignored(watcher, pump) -> None:
    watcher.set_watch_session_lock(False)
    watcher.set_watch_suspend(False)
    post(watcher._backend._hwnd, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK)  # noqa: SLF001
    post(watcher._backend._hwnd, WM_POWERBROADCAST, PBT_APMSUSPEND)  # noqa: SLF001
    pump(500)
    assert watcher.events == []


@windows_only
def test_only_the_first_trigger_fires_until_rearmed(watcher, pump) -> None:
    """One lock request per unlock session: the vault is already locked after it."""
    hwnd = watcher._backend._hwnd  # noqa: SLF001
    post(hwnd, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK)
    pump(400)
    post(hwnd, WM_POWERBROADCAST, PBT_APMSUSPEND)
    pump(400)
    assert watcher.events == ["the workstation was locked"]

    watcher.start()  # what MainWindow does on the next unlock
    post(hwnd, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK)
    pump(400)
    assert len(watcher.events) == 2


@windows_only
def test_system_idle_query_is_sane() -> None:
    idle = lockwatch._system_idle_seconds()  # noqa: SLF001
    assert idle is not None
    assert 0 <= idle < 86400


@pytest.mark.parametrize(
    ("stub_idle", "rewind_activity", "expect_lock"),
    [
        (lambda: 999.0, 0, True),  # the whole machine has been idle
        (lambda: 0.0, 600, False),  # user is active elsewhere: system idle wins
        (lambda: None, 600, True),  # no OS support: fall back to in-app idle
        (lambda: None, 0, False),  # no OS support, but we were just used
    ],
)
def test_idle_auto_lock(qapp, pump, monkeypatch, stub_idle, rewind_activity, expect_lock) -> None:
    monkeypatch.setattr(lockwatch, "_system_idle_seconds", stub_idle)
    watch = SessionWatcher(idle_seconds=60, watch_session_lock=False, watch_suspend=False)
    events: list[str] = []
    watch.lockRequested.connect(events.append)
    watch.start()
    watch._last_activity -= rewind_activity  # noqa: SLF001
    watch._idle_timer.setInterval(100)  # noqa: SLF001
    watch._idle_timer.start()  # noqa: SLF001
    try:
        pump(500)
    finally:
        watch.shutdown()
    if expect_lock:
        assert len(events) == 1
        assert "no activity" in events[0]
    else:
        assert events == []


def test_idle_lock_can_be_disabled(qapp, pump, monkeypatch) -> None:
    monkeypatch.setattr(lockwatch, "_system_idle_seconds", lambda: 999999.0)
    watch = SessionWatcher(idle_seconds=0, watch_session_lock=False, watch_suspend=False)
    events: list[str] = []
    watch.lockRequested.connect(events.append)
    watch.start()
    watch._idle_timer.setInterval(100)  # noqa: SLF001
    watch._idle_timer.start()  # noqa: SLF001
    try:
        pump(400)
    finally:
        watch.shutdown()
    assert events == []


def test_activity_resets_the_idle_clock(qapp, pump, monkeypatch) -> None:
    monkeypatch.setattr(lockwatch, "_system_idle_seconds", lambda: None)
    watch = SessionWatcher(idle_seconds=60, watch_session_lock=False, watch_suspend=False)
    events: list[str] = []
    watch.lockRequested.connect(events.append)
    watch.start()
    watch._last_activity -= 600  # noqa: SLF001
    watch.note_activity()  # ... but the user just did something
    watch._idle_timer.setInterval(100)  # noqa: SLF001
    watch._idle_timer.start()  # noqa: SLF001
    try:
        pump(400)
    finally:
        watch.shutdown()
    assert events == []


def test_shutdown_releases_the_backend(qapp, pump) -> None:
    watch = SessionWatcher(idle_seconds=0)
    watch.start()
    pump(300)
    watch.shutdown()
    pump(200)
    assert watch.backend_name == "none"
