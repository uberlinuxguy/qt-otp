"""Post-build smoke test, for checking a packaged executable.

Freezing an app breaks things that pass in a source checkout: the OpenSSL
backend behind scrypt, bundled data files, Qt plugins, ctypes DLL lookups. This
runs the app's real machinery once and reports what worked, so a broken build
never reaches a release.

    python -m otpvault --selftest report.json
"""

from __future__ import annotations

import json
import platform
import sys
import tempfile
import time
from pathlib import Path

from . import __version__

# RFC 6238 Appendix B: base32 of the 20-byte seed "12345678901234567890",
# SHA1, 8 digits, T=59.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_AT = 59
RFC_ALGORITHM = "SHA1"
RFC_EXPECTED = "94287082"


def _frozen_root() -> Path | None:
    """The bundle directory when running from a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def check_qt_application() -> str:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return f"platform plugin in use: {app.platformName()!r}"


def check_svg_support() -> str:
    from PySide6.QtGui import QImageReader

    formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
    if "svg" not in formats:
        raise AssertionError("the Qt SVG image plugin is missing; the app icon would not render")
    return "svg image plugin present"


def check_windows_platform_plugin() -> str:
    """A GUI build must carry qwindows.dll even when this runs offscreen."""
    if sys.platform != "win32":
        return "not applicable"
    root = _frozen_root()
    if root is None:
        return "not frozen; nothing to verify"
    matches = list(root.rglob("qwindows.dll"))
    if not matches:
        raise AssertionError("qwindows.dll is not in the bundle; the app could not open a window")
    return f"qwindows.dll bundled ({matches[0].relative_to(root)})"


def check_app_icon() -> str:
    from .ui import icons

    if not icons.ICON_PATH.is_file():
        raise AssertionError(f"the icon resource is missing from the build: {icons.ICON_PATH}")
    icon = icons.app_icon()
    if icon.isNull():
        raise AssertionError("the app icon failed to render")
    sizes = sorted({s.width() for s in icon.availableSizes()})
    missing = sorted(set(icons.ICON_SIZES) - set(sizes))
    if missing:
        raise AssertionError(f"icon sizes missing: {missing}")
    return f"rendered at {sizes}"


def check_totp() -> str:
    from . import totp

    code = totp.totp(RFC_SECRET, at=RFC_AT, digits=8, algorithm=RFC_ALGORITHM)
    if code != RFC_EXPECTED:
        raise AssertionError(f"RFC 6238 vector failed: got {code}, want {RFC_EXPECTED}")
    return f"RFC 6238 vector matches ({code})"


def check_vault_roundtrip() -> str:
    """The real scrypt + AES-GCM path, which needs a working OpenSSL backend."""
    from .vault import OtpEntry, Vault

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "selftest.otpv"
        started = time.perf_counter()
        vault = Vault(path)
        vault.create("selftest-password")
        vault.add(OtpEntry(issuer="Selftest", account="me", secret=RFC_SECRET))
        elapsed = time.perf_counter() - started
        raw = path.read_bytes()
        if RFC_SECRET.encode() in raw or b"Selftest" in raw:
            raise AssertionError("the vault file was not encrypted")
        vault.lock()
        vault.unlock("selftest-password")
        if len(vault.entries) != 1 or vault.entries[0].secret != RFC_SECRET:
            raise AssertionError("the vault did not survive a lock/unlock cycle")
        try:
            Vault(path).unlock("wrong password")
        except Exception:  # noqa: BLE001 - any rejection is the point
            pass
        else:
            raise AssertionError("a wrong password was accepted")
    return f"create/save/lock/unlock ok, scrypt took {elapsed * 1000:.0f} ms"


def check_lock_watcher() -> str:
    from .lockwatch import SessionWatcher

    watcher = SessionWatcher(idle_seconds=0)
    try:
        watcher.start()
        backend = watcher.backend_name
        if sys.platform == "win32" and backend != "windows-wts":
            raise AssertionError(f"session-lock detection unavailable (backend: {backend})")
    finally:
        watcher.shutdown()
    return f"backend {backend!r} started and stopped"


def check_main_window() -> str:
    from .config import Settings
    from .ui.mainwindow import MainWindow
    from .vault import Vault

    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(
            Vault(Path(directory) / "selftest.otpv"),
            Settings(idle_lock_seconds=0, minimize_to_tray=False),
        )
        try:
            if not window.locked:
                raise AssertionError("the window did not start locked")
        finally:
            window._quitting = True  # noqa: SLF001
            window.close()
    return "opens locked, on the unlock screen"


def check_vault_location() -> str:
    from .config import Settings

    path = Settings().resolved_vault_path()  # read-only: never writes settings
    if not path.is_absolute():
        raise AssertionError(f"the default vault path is not absolute: {path}")
    return f"default location resolves to {path}"


CHECKS = (
    ("qt_application", check_qt_application),
    ("svg_support", check_svg_support),
    ("windows_platform_plugin", check_windows_platform_plugin),
    ("app_icon", check_app_icon),
    ("totp", check_totp),
    ("vault_roundtrip", check_vault_roundtrip),
    ("lock_watcher", check_lock_watcher),
    ("main_window", check_main_window),
    ("vault_location", check_vault_location),
)


def run(report_path: Path | str | None = None) -> int:
    """Run every check. Returns 0 if all passed, 1 otherwise."""
    results = []
    for name, check in CHECKS:
        try:
            detail = check()
            ok, detail = True, detail
        except Exception as exc:  # noqa: BLE001 - a failed check is data, not a crash
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "ok": ok, "detail": detail})
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {detail}", flush=True)

    report = {
        "ok": all(r["ok"] for r in results),
        "version": __version__,
        "frozen": _frozen_root() is not None,
        "executable": sys.executable,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "checks": results,
    }
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"report written to {report_path}", flush=True)

    failed = [r["name"] for r in results if not r["ok"]]
    print(("FAILED: " + ", ".join(failed)) if failed else "all checks passed", flush=True)
    return 1 if failed else 0
