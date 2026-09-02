"""Entry point: `python -m otpvault` (or the `qt-otp` script)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import APP_DISPLAY_NAME, APP_NAME, ORG_NAME, __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="qt-otp", description=f"{APP_DISPLAY_NAME} — encrypted TOTP vault")
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        metavar="PATH",
        help="vault file to open for this run only, ignoring the saved location",
    )
    parser.add_argument("--verbose", action="store_true", help="log lock/unlock activity to stderr")
    parser.add_argument(
        "--selftest",
        nargs="?",
        const="",
        default=None,
        metavar="REPORT.json",
        help="run internal checks and exit, optionally writing a JSON report "
        "(used to smoke-test packaged builds)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity.

    Without this, Windows groups the window under the host python.exe and shows
    python's icon in the taskbar instead of ours.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{ORG_NAME}.{APP_NAME}")
    except Exception:  # noqa: BLE001 - cosmetic only, never worth failing startup
        logging.getLogger(__name__).debug("could not set the taskbar app id", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.selftest is not None:
        from .selftest import run

        return run(args.selftest or None)

    _set_windows_app_id()

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .config import Settings
    from .singleinstance import SingleInstance, instance_key
    from .ui import icons
    from .ui.mainwindow import MainWindow
    from .vault import Vault

    log = logging.getLogger(__name__)

    app = QApplication(sys.argv[:1] + (argv or []))
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    # Application-wide default, so dialogs and the tray inherit it too.
    app.setWindowIcon(icons.app_icon())
    # The tray icon keeps the app alive after the window is closed; MainWindow
    # calls QApplication.quit() when the user really means to exit.
    app.setQuitOnLastWindowClosed(False)

    settings = Settings.load()
    # --vault is a one-off override: it is not written back to the settings.
    vault = Vault(settings.resolved_vault_path(args.vault))

    # One copy per vault. A second launch hands focus to the first and stops.
    guard = SingleInstance(instance_key(vault.path))
    if not guard.try_acquire():
        if guard.notify_existing():
            log.info("an instance is already open for %s; brought it to the front", vault.path)
            return 0
        QMessageBox.warning(
            None,
            APP_DISPLAY_NAME,
            f"{APP_DISPLAY_NAME} already has this vault open, but that window is not "
            f"responding.\n\n{vault.path}\n\nClose the other copy and try again.",
        )
        return 1

    window = MainWindow(
        vault, settings, path_overridden=bool(args.vault), instance_guard=guard
    )
    # Deliberately not parented to the window: `guard` is held by this frame for
    # the life of the app, and release() below must not race Qt deleting it.
    guard.activateRequested.connect(window.activate)
    window.show()
    try:
        return app.exec()
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
