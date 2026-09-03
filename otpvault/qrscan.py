"""Read QR codes out of screen pixels.

Adding an account normally means copying a base32 secret by hand. Every service
that hands out one of those also shows a QR code containing the same thing as an
`otpauth://` URI, so this reads it straight off the screen instead.

Decoding is zxing-cpp; images come in as QImage and go out as URI strings. No
numpy: the greyscale bytes of a QImage are handed to zxing-cpp directly.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

OTPAUTH_SCHEME = "otpauth://"
#: Google Authenticator's batch export. A different, protobuf-encoded format
#: that is not an otpauth URI, so it is recognised only to explain itself.
MIGRATION_SCHEME = "otpauth-migration://"


class QrScanError(RuntimeError):
    """The decoder is unavailable or failed outright."""


def is_available() -> bool:
    """Whether QR decoding can be done at all."""
    return not unavailable_reason()


def unavailable_reason() -> str:
    """Why decoding is impossible, or an empty string if it is fine."""
    try:
        import zxingcpp
    except Exception as exc:  # noqa: BLE001 - a missing or broken wheel both count
        return f"{type(exc).__name__}: {exc}"
    return "" if hasattr(zxingcpp, "read_barcodes") else "zxingcpp has no read_barcodes()"


def decode_qr_codes(image: QImage) -> list[str]:
    """Every QR code found in `image`, as text, in the order found.

    Returns an empty list when there is nothing to find — that is a normal
    outcome, not an error. Raises QrScanError if the decoder itself is missing
    or fails.
    """
    if image is None or image.isNull():
        return []
    try:
        import zxingcpp
    except Exception as exc:  # noqa: BLE001
        raise QrScanError(f"the QR decoder is unavailable: {exc}") from exc

    # Grayscale8 is what the decoder wants anyway, and it makes the buffer
    # layout predictable; bytesPerLine carries any row padding.
    grey = image.convertToFormat(QImage.Format.Format_Grayscale8)
    # `raw` must be a named local, not an argument expression: ImageView keeps a
    # *view* of these bytes rather than copying them, so a temporary would be
    # freed before read_barcodes reads it — an access violation, not an error.
    raw = bytes(grey.constBits())
    try:
        view = zxingcpp.ImageView(
            raw,
            grey.width(),
            grey.height(),
            zxingcpp.ImageFormat.Lum,
            grey.bytesPerLine(),
        )
        results = zxingcpp.read_barcodes(view, formats=zxingcpp.BarcodeFormat.QRCode)
    except Exception as exc:  # noqa: BLE001
        raise QrScanError(f"the QR decoder failed: {exc}") from exc

    texts = [result.text for result in results if result.text]
    log.info("decoded %d QR code(s) from a %dx%d image", len(texts), image.width(), image.height())
    return texts


def find_otpauth_uris(image: QImage) -> list[str]:
    """The otpauth:// URIs among the QR codes in `image`, de-duplicated."""
    seen: list[str] = []
    for text in decode_qr_codes(image):
        if text.lower().startswith(OTPAUTH_SCHEME) and text not in seen:
            seen.append(text)
    return seen


def has_migration_payload(image: QImage) -> bool:
    """True if the image holds a Google Authenticator export QR.

    Worth telling apart: it *is* a QR code and it *does* hold accounts, but not
    in a form this app reads, so 'nothing found' would be a misleading answer.
    """
    return any(text.lower().startswith(MIGRATION_SCHEME) for text in decode_qr_codes(image))


__all__ = [
    "MIGRATION_SCHEME",
    "OTPAUTH_SCHEME",
    "QrScanError",
    "decode_qr_codes",
    "find_otpauth_uris",
    "has_migration_payload",
    "is_available",
    "unavailable_reason",
]
