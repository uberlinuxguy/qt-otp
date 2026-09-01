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

    QSettings.setDefaultFormat() is not enough — it does not retarget the
    2-argument constructor on Windows, so without this every Settings.save()
    in the suite would land in the user's real registry.
    """
    from PySide6.QtCore import QSettings

    from otpvault import config

    store_file = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        config, "settings_store", lambda: QSettings(store_file, QSettings.Format.IniFormat)
    )
    return store_file
