"""Render the app SVG into a multi-resolution Windows .ico.

The .ico is a build artifact, not a checked-in asset: the SVG stays the single
source of truth. Sizes come from `otpvault.ui.icons`, and the frames are
rendered by the same code path the running app uses, so the executable's icon
and the window's icon cannot drift apart.

Qt's own .ico writer only emits one frame, so the container is packed here.
PNG-compressed frames are used, which Windows has understood since Vista.

Usage:  python tools/make_icon.py [output.ico]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT = REPO_ROOT / "build" / "qt-otp.ico"
# 256 is what Explorer shows in large-icon views; 16 is the title bar.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_frames(sizes: tuple[int, ...]) -> list[tuple[int, bytes]]:
    """Render the app icon to PNG bytes at each size."""
    from PySide6.QtCore import QBuffer
    from PySide6.QtWidgets import QApplication

    from otpvault.ui import icons

    app = QApplication.instance() or QApplication([])
    assert app is not None  # keeps linters quiet about the unused handle

    icon = icons.app_icon()
    if icon.isNull():
        raise SystemExit(f"the app icon is empty; is {icons.ICON_PATH} present?")

    frames: list[tuple[int, bytes]] = []
    for size in sizes:
        pixmap = icon.pixmap(size, size)
        if pixmap.isNull() or pixmap.width() != size:
            raise SystemExit(f"could not render the icon at {size}px")
        # QBuffer's own byte array: passing a temporary QByteArray leaves a
        # dangling reference and takes the interpreter down with it.
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        if not pixmap.save(buffer, "PNG"):
            raise SystemExit(f"could not encode the {size}px frame as PNG")
        frames.append((size, bytes(buffer.data())))
    return frames


def pack_ico(frames: list[tuple[int, bytes]]) -> bytes:
    """Build an ICO container around already-encoded PNG frames."""
    header = struct.pack("<HHH", 0, 1, len(frames))  # reserved, type=icon, count
    offset = len(header) + 16 * len(frames)
    directory, payload = b"", b""
    for size, data in frames:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 0 means 256 in the ICO header
            0 if size >= 256 else size,
            0,  # palette size: 0 for true colour
            0,  # reserved
            1,  # colour planes
            32,  # bits per pixel
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    return header + directory + payload


def main(argv: list[str]) -> int:
    output = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    frames = png_frames(ICO_SIZES)
    output.write_bytes(pack_ico(frames))
    print(f"wrote {output} ({output.stat().st_size} bytes, sizes: {[s for s, _ in frames]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
