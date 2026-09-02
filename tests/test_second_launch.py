"""Launching the app twice, for real.

The in-process tests cannot cover the request/reply round trip (two endpoints of
one pipe in a single process interfere on Windows), so this drives actual
processes: start the app, start it again, and check the second one hands over
and exits instead of opening a second window.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from otpvault.singleinstance import SingleInstance, instance_key

REPO = Path(__file__).resolve().parent.parent
START_TIMEOUT_S = 30
SECOND_LAUNCH_TIMEOUT_S = 30


def app_env(settings_file: Path) -> dict[str, str]:
    """Environment for a child app: offscreen, and its own settings file."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_OTP_SETTINGS_FILE"] = str(settings_file)
    env["PYTHONPATH"] = str(REPO)
    return env


def launch(vault: Path, settings_file: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "otpvault", "--vault", str(vault), "--verbose"],
        cwd=REPO,
        env=app_env(settings_file),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_until_listening(vault: Path, process: subprocess.Popen) -> None:
    """Block until the running app answers on its instance socket."""
    probe = SingleInstance(instance_key(vault))
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"the app exited early with {process.returncode}:\n{process.stdout.read()}")
        if probe._existing_instance_responds():  # noqa: SLF001
            return
        time.sleep(0.2)
    pytest.fail("the app never started listening for other launches")


def stop(process: subprocess.Popen) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    try:
        return process.stdout.read() or ""
    except Exception:  # noqa: BLE001
        return ""


@pytest.fixture()
def running_app(qapp, tmp_path: Path):
    """One app already running on its own vault."""
    vault = tmp_path / "vault.otpv"
    settings = tmp_path / "settings.ini"
    process = launch(vault, settings)
    wait_until_listening(vault, process)
    yield vault, settings, process
    stop(process)


def test_a_second_launch_hands_over_and_exits(running_app) -> None:
    vault, settings, first = running_app

    second = launch(vault, settings)
    try:
        output = second.communicate(timeout=SECOND_LAUNCH_TIMEOUT_S)[0]
    except subprocess.TimeoutExpired:
        stop(second)
        pytest.fail("the second launch never exited; it probably opened its own window")

    assert second.returncode == 0, f"the second launch failed:\n{output}"
    assert "brought it to the front" in output, (
        "the second launch did not report a successful handover; "
        f"the running instance may not have acknowledged it:\n{output}"
    )
    assert first.poll() is None, "the first instance should still be running"


def test_the_first_instance_keeps_running_after_several_launches(running_app) -> None:
    vault, settings, first = running_app

    for _ in range(3):
        later = launch(vault, settings)
        output = later.communicate(timeout=SECOND_LAUNCH_TIMEOUT_S)[0]
        assert later.returncode == 0, output

    assert first.poll() is None


def test_a_different_vault_gets_its_own_instance(running_app, tmp_path: Path) -> None:
    """Two vaults may be open at once; only the same one is refused."""
    _, settings, first = running_app
    other_vault = tmp_path / "other.otpv"

    other = launch(other_vault, settings)
    try:
        wait_until_listening(other_vault, other)
        assert other.poll() is None, "a different vault should open its own window"
        assert first.poll() is None
    finally:
        stop(other)


def test_the_handover_is_prompt(running_app) -> None:
    """A user double-clicking the icon should not wait around."""
    vault, settings, _ = running_app

    started = time.monotonic()
    second = launch(vault, settings)
    second.communicate(timeout=SECOND_LAUNCH_TIMEOUT_S)
    elapsed = time.monotonic() - started

    assert second.returncode == 0
    assert elapsed < 15, f"the handover took {elapsed:.1f}s"
