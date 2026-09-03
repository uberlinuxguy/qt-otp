"""Reading accounts off QR codes in screen pixels."""

from __future__ import annotations

import pytest

from otpvault import qrscan

zxingcpp = pytest.importorskip("zxingcpp", reason="QR decoding needs zxing-cpp")

URI = "otpauth://totp/ACME:me@example.com?secret=GEZDGNBVGY3TQOJQ&issuer=ACME"
SECOND_URI = "otpauth://totp/Other:you@example.com?secret=GEZDGNBVGY3TQOJQ&issuer=Other"


def qr_image(text: str, scale: int = 6):
    """Render `text` as a QR code, the way a website would show one."""
    from PySide6.QtGui import QImage

    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    matrix = zxingcpp.write_barcode_to_image(barcode, scale=scale)
    height, width = matrix.shape[0], matrix.shape[1]
    # Copy: the QImage must not alias a buffer that goes out of scope.
    return QImage(
        bytes(memoryview(matrix)), width, height, width, QImage.Format.Format_Grayscale8
    ).copy()


def screen_with(*codes, size=(900, 700), positions=None):
    """A pretend screenshot: QR codes drawn on a page with other content."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    screen = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    screen.fill(QColor("#f7f7f7"))
    painter = QPainter(screen)
    painter.setPen(QColor("#202020"))
    painter.drawText(40, 40, "Scan this with your authenticator app")
    spots = positions or [(360 + 300 * index, 240) for index in range(len(codes))]
    for code, (x, y) in zip(codes, spots):
        qr = qr_image(code)
        # Drawn at natural size: scaling a QR down is what makes it unreadable.
        painter.drawImage(QRect(x, y, qr.width(), qr.height()), qr)
    painter.end()
    return screen


def test_the_decoder_is_available() -> None:
    assert qrscan.is_available() is True
    assert qrscan.unavailable_reason() == ""


def test_a_qr_code_on_a_page_is_read(qapp) -> None:
    assert qrscan.find_otpauth_uris(screen_with(URI)) == [URI]


def test_a_cropped_region_around_the_code_is_read(qapp) -> None:
    from PySide6.QtCore import QRect

    screen = screen_with(URI, positions=[(360, 240)])
    region = screen.copy(QRect(340, 220, 320, 320))
    assert qrscan.find_otpauth_uris(region) == [URI]


def test_the_bare_code_with_no_surroundings_is_read(qapp) -> None:
    assert qrscan.find_otpauth_uris(qr_image(URI)) == [URI]


def test_several_codes_in_one_capture_are_all_read(qapp) -> None:
    found = qrscan.find_otpauth_uris(screen_with(URI, SECOND_URI))
    assert sorted(found) == sorted([URI, SECOND_URI])


def test_duplicates_are_collapsed(qapp) -> None:
    assert qrscan.find_otpauth_uris(screen_with(URI, URI)) == [URI]


def test_a_page_with_no_qr_code_yields_nothing(qapp) -> None:
    from PySide6.QtGui import QColor, QImage

    plain = QImage(400, 300, QImage.Format.Format_RGB32)
    plain.fill(QColor("#ffffff"))
    assert qrscan.decode_qr_codes(plain) == []
    assert qrscan.find_otpauth_uris(plain) == []


def test_a_qr_code_that_is_not_an_account_is_ignored(qapp) -> None:
    """A wifi or URL QR on the same page must not be mistaken for an account."""
    image = screen_with("https://example.com/not-an-account")
    assert qrscan.decode_qr_codes(image), "the code itself should still be read"
    assert qrscan.find_otpauth_uris(image) == []


def test_a_google_authenticator_export_is_recognised(qapp) -> None:
    """It holds accounts, but not in a form this app reads."""
    migration = "otpauth-migration://offline?data=Ch4KCkhlbGxvV29ybGQ"
    image = screen_with(migration)
    assert qrscan.find_otpauth_uris(image) == []
    assert qrscan.has_migration_payload(image) is True


def test_an_ordinary_miss_is_not_a_migration_payload(qapp) -> None:
    assert qrscan.has_migration_payload(screen_with(URI)) is False


def test_a_null_image_is_handled(qapp) -> None:
    from PySide6.QtGui import QImage

    assert qrscan.decode_qr_codes(QImage()) == []
    assert qrscan.decode_qr_codes(None) == []


def test_a_code_shrunk_below_readability_fails_cleanly(qapp) -> None:
    """Documents the real limit: too few pixels per module cannot be read.

    The app tells the user to select a bigger area rather than pretending.
    """
    from PySide6.QtCore import Qt

    tiny = qr_image(URI).scaled(
        40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    assert qrscan.find_otpauth_uris(tiny) == []


def test_a_scanned_uri_becomes_a_usable_entry(qapp) -> None:
    from otpvault.vault import OtpEntry

    (uri,) = qrscan.find_otpauth_uris(screen_with(URI))
    entry = OtpEntry.from_uri(uri)
    assert entry.issuer == "ACME"
    assert entry.account == "me@example.com"
    assert len(entry.code()) == 6


def test_the_decoder_being_missing_is_reported(qapp, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_zxing(name, *args, **kwargs):
        if name == "zxingcpp":
            raise ImportError("no zxingcpp here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_zxing)
    assert qrscan.is_available() is False
    assert "no zxingcpp here" in qrscan.unavailable_reason()
    with pytest.raises(qrscan.QrScanError):
        qrscan.decode_qr_codes(qr_image(URI))
