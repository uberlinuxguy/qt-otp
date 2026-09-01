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

    from PySide6.QtWidgets import QApplication

    from .config import Settings
    from .ui import icons
    from .ui.mainwindow import MainWindow
    from .vault import Vault

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

    window = MainWindow(vault, settings, path_overridden=bool(args.vault))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
