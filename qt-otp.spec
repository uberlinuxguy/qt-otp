# PyInstaller build: one self-contained qt-otp.exe.
#
#   python tools/make_icon.py            # renders build/qt-otp.ico from the SVG
#   pyinstaller --noconfirm qt-otp.spec  # writes dist/qt-otp.exe
#
# Windowed (no console) single file. UPX is deliberately off: it saves a few MB
# and reliably attracts antivirus false positives.

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
sys.path.insert(0, str(SPEC_DIR))

from otpvault import __version__  # noqa: E402 - needs the path above

APP_NAME = "qt-otp"
ICON_FILE = SPEC_DIR / "build" / f"{APP_NAME}.ico"

if not ICON_FILE.is_file():
    raise SystemExit(f"{ICON_FILE} is missing — run 'python tools/make_icon.py' first")

# Data files keep their package-relative layout, so otpvault.ui.icons finds the
# SVGs with the same path arithmetic it uses from a source checkout.
RESOURCES = SPEC_DIR / "otpvault" / "resources"
datas = [
    (str(RESOURCES / "qt-otp-icon.svg"), "otpvault/resources"),
    (str(RESOURCES / "qt-otp-about.svg"), "otpvault/resources"),
]

excludes = [
    # Never imported on Windows: lockwatch only touches QtDBus on Linux.
    "PySide6.QtDBus",
    # Test-only and stdlib bulk that would otherwise ride along.
    "PySide6.QtTest",
    "tkinter",
    "unittest",
    "pytest",
    "pydoc_data",
    "lib2to3",
]


def windows_version_info():
    """A version resource, so the file's Properties tab is not blank."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    parts = [int(p) for p in __version__.split(".")][:4]
    version = tuple(parts + [0] * (4 - len(parts)))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=version, prodvers=version),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",  # US English, Unicode
                        [
                            StringStruct("CompanyName", APP_NAME),
                            StringStruct("FileDescription", "qt-otp — encrypted TOTP vault"),
                            StringStruct("FileVersion", __version__),
                            StringStruct("InternalName", APP_NAME),
                            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                            StringStruct("ProductName", APP_NAME),
                            StringStruct("ProductVersion", __version__),
                            StringStruct("LegalCopyright", "Licensed under the Apache License 2.0"),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


a = Analysis(  # noqa: F821
    [str(SPEC_DIR / "tools" / "entrypoint.py")],
    pathex=[str(SPEC_DIR)],
    datas=datas,
    hiddenimports=["otpvault.selftest"],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name=APP_NAME,
    icon=str(ICON_FILE),
    version=windows_version_info(),
    console=False,  # GUI app: no console window
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
)
