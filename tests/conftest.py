"""Shared fixtures. Qt tests run offscreen so the suite needs no display."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session (Qt allows only one)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def pump(qapp):
    """Run the Qt event loop for `ms` milliseconds."""
    from PySide6.QtCore import QEventLoop, QTimer

    def _pump(ms: int = 400) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    return _pump


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    """Redirect preferences to a per-test ini file.

    Uses the app's own QT_OTP_SETTINGS_FILE override rather than patching over
    `settings_store`, so the tests exercise the real code path and child
    processes launched by a test inherit the same isolation.

    (QSettings.setDefaultFormat() would not do: it does not retarget the
    2-argument constructor on Windows, so every Settings.save() in the suite
    would land in the user's real registry.)
    """
    from otpvault.config import SETTINGS_FILE_ENV

    store_file = str(tmp_path / "settings.ini")
    monkeypatch.setenv(SETTINGS_FILE_ENV, store_file)
    return store_file
