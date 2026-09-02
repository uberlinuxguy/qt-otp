"""One instance per vault, and the handoff that brings the first one forward."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from otpvault.singleinstance import SingleInstance, instance_key


@pytest.fixture()
def key() -> str:
    """A key unique to this test, so a real running app never interferes."""
    return f"qt-otp-test-{uuid.uuid4().hex[:16]}"


@pytest.fixture()
def primary(qapp, key: str):
    guard = SingleInstance(key)
    assert guard.try_acquire(), "the first instance should always win the key"
    yield guard
    guard.release()


def test_the_first_instance_becomes_primary(primary) -> None:
    assert primary.is_primary


def test_a_second_instance_is_refused(primary, key: str) -> None:
    second = SingleInstance(key)
    assert second.try_acquire() is False
    assert not second.is_primary


def send_activate(key: str, pump) -> None:
    """Deliver the message a second launch sends, then let the primary handle it.

    Deliberately does not wait for the reply. Two endpoints in one process
    interfere on Windows: while a client sits in a blocking wait, the server
    side of the same pipe does not get its readyRead delivered. Real instances
    are separate processes, so the full request/reply round trip is covered by
    test_second_launch.py instead.
    """
    from PySide6.QtNetwork import QLocalSocket

    from otpvault.singleinstance import ACTIVATE_MESSAGE

    socket = QLocalSocket()
    socket.connectToServer(key)
    assert socket.waitForConnected(2000), f"could not reach the instance: {socket.errorString()}"
    socket.write(ACTIVATE_MESSAGE)
    socket.flush()
    pump(400)  # the primary reads and emits on its own event loop
    socket.abort()
    pump(100)


def test_a_second_launch_asks_the_first_to_come_forward(primary, key: str, pump) -> None:
    activations: list[bool] = []
    primary.activateRequested.connect(lambda: activations.append(True))

    send_activate(key, pump)

    assert activations == [True], "the running instance was never asked to activate"


def test_repeated_launches_each_ask_once(primary, key: str, pump) -> None:
    activations: list[bool] = []
    primary.activateRequested.connect(lambda: activations.append(True))

    for _ in range(3):
        send_activate(key, pump)

    assert len(activations) == 3


def test_an_unrecognised_message_is_ignored(primary, key: str, pump) -> None:
    """Something else on the pipe must not yank the window to the front."""
    from PySide6.QtNetwork import QLocalSocket

    activations: list[bool] = []
    primary.activateRequested.connect(lambda: activations.append(True))

    socket = QLocalSocket()
    socket.connectToServer(key)
    assert socket.waitForConnected(2000)
    socket.write(b"who are you")
    socket.flush()
    pump(400)
    socket.abort()

    assert activations == []


def test_notify_fails_when_nothing_is_listening(qapp, key: str) -> None:
    lonely = SingleInstance(key)
    assert lonely.notify_existing() is False


def test_releasing_lets_the_next_instance_take_over(qapp, key: str) -> None:
    first = SingleInstance(key)
    assert first.try_acquire() is True
    first.release()
    assert not first.is_primary

    second = SingleInstance(key)
    assert second.try_acquire() is True, "the key should be free again"
    second.release()


def test_a_socket_left_by_a_crash_is_taken_over(qapp, key: str) -> None:
    """A dead instance must not lock the app out forever."""
    from PySide6.QtNetwork import QLocalServer

    # A listening server with nobody handling connections stands in for the
    # socket a crashed process leaves behind on Unix.
    stale = QLocalServer()
    stale.listen(key)
    stale.close()  # the name may survive the listener

    guard = SingleInstance(key)
    assert guard.try_acquire() is True
    guard.release()


def test_different_keys_do_not_collide(qapp) -> None:
    first = SingleInstance(f"qt-otp-test-{uuid.uuid4().hex[:16]}")
    second = SingleInstance(f"qt-otp-test-{uuid.uuid4().hex[:16]}")
    assert first.try_acquire() is True
    assert second.try_acquire() is True
    first.release()
    second.release()


# ------------------------------------------------------------------- the key


def test_the_key_is_stable_for_one_vault(tmp_path: Path) -> None:
    path = tmp_path / "vault.otpv"
    assert instance_key(path) == instance_key(path)


def test_the_key_differs_between_vaults(tmp_path: Path) -> None:
    """Two different vaults may be open at once; the same one may not."""
    assert instance_key(tmp_path / "a.otpv") != instance_key(tmp_path / "b.otpv")


def test_the_key_ignores_how_the_path_was_written(tmp_path: Path) -> None:
    direct = instance_key(tmp_path / "vault.otpv")
    roundabout = instance_key(tmp_path / "sub" / ".." / "vault.otpv")
    assert direct == roundabout, "the same file reached two ways is still one vault"


def test_the_key_is_case_insensitive_on_windows(tmp_path: Path) -> None:
    import sys

    if sys.platform != "win32":
        pytest.skip("case-insensitive paths are a Windows concern")
    lower = instance_key(tmp_path / "vault.otpv")
    upper = instance_key(Path(str(tmp_path).upper()) / "VAULT.OTPV")
    assert lower == upper


def test_the_key_leaks_neither_path_nor_user(tmp_path: Path) -> None:
    """On Windows the pipe namespace is machine-wide, so the name is hashed."""
    import getpass

    key = instance_key(tmp_path / "secret-place" / "vault.otpv")
    assert "secret-place" not in key
    assert getpass.getuser().lower() not in key.lower()
    assert key.startswith("qt-otp-")
    assert len(key) < 100  # socket names have length limits
