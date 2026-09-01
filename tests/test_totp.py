"""RFC 6238 Appendix B test vectors, plus otpauth URI handling."""

from __future__ import annotations

import base64

import pytest

from otpvault import totp

# RFC 6238 seeds, base32-encoded as authenticator apps expect them.
SEED_SHA1 = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
SEED_SHA256 = base64.b32encode(b"12345678901234567890123456789012").decode().rstrip("=")
SEED_SHA512 = base64.b32encode(
    b"1234567890123456789012345678901234567890123456789012345678901234"
).decode().rstrip("=")

RFC_VECTORS = [
    (59, "SHA1", SEED_SHA1, "94287082"),
    (59, "SHA256", SEED_SHA256, "46119246"),
    (59, "SHA512", SEED_SHA512, "90693936"),
    (1111111109, "SHA1", SEED_SHA1, "07081804"),
    (1111111109, "SHA256", SEED_SHA256, "68084774"),
    (1111111109, "SHA512", SEED_SHA512, "25091201"),
    (1111111111, "SHA1", SEED_SHA1, "14050471"),
    (1111111111, "SHA256", SEED_SHA256, "67062674"),
    (1111111111, "SHA512", SEED_SHA512, "99943326"),
    (1234567890, "SHA1", SEED_SHA1, "89005924"),
    (1234567890, "SHA256", SEED_SHA256, "91819424"),
    (1234567890, "SHA512", SEED_SHA512, "93441116"),
    (2000000000, "SHA1", SEED_SHA1, "69279037"),
    (2000000000, "SHA256", SEED_SHA256, "90698825"),
    (2000000000, "SHA512", SEED_SHA512, "38618901"),
    (20000000000, "SHA1", SEED_SHA1, "65353130"),
    (20000000000, "SHA256", SEED_SHA256, "77737706"),
    (20000000000, "SHA512", SEED_SHA512, "47863826"),
]


@pytest.mark.parametrize(("at", "algorithm", "secret", "expected"), RFC_VECTORS)
def test_rfc6238_vectors(at: int, algorithm: str, secret: str, expected: str) -> None:
    assert totp.totp(secret, at=at, period=30, digits=8, algorithm=algorithm) == expected


def test_six_digit_code_is_truncated_from_the_same_value() -> None:
    eight = totp.totp(SEED_SHA1, at=59, digits=8)
    six = totp.totp(SEED_SHA1, at=59, digits=6)
    assert six == eight[-6:]


def test_code_is_stable_inside_a_step_and_changes_across_steps() -> None:
    assert totp.totp(SEED_SHA1, at=100) == totp.totp(SEED_SHA1, at=119)
    assert totp.totp(SEED_SHA1, at=119) != totp.totp(SEED_SHA1, at=120)


def test_remaining_seconds() -> None:
    assert totp.remaining_seconds(30, at=100) == pytest.approx(20)
    assert totp.remaining_seconds(30, at=120) == pytest.approx(30)


@pytest.mark.parametrize(
    "messy",
    ["gezd gnbv gy3t qojq", "GEZDGNBVGY3TQOJQ====", "gezd-gnbv-gy3t-qojq", "  GEZDGNBVGY3TQOJQ  "],
)
def test_normalize_secret_accepts_human_formatting(messy: str) -> None:
    assert totp.normalize_secret(messy) == "GEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize("bad", ["", "   ", "not-base32!", "1", "8888"])
def test_normalize_secret_rejects_garbage(bad: str) -> None:
    with pytest.raises(totp.InvalidSecret):
        totp.normalize_secret(bad)


def test_hotp_rejects_unsupported_digits() -> None:
    with pytest.raises(ValueError):
        totp.hotp(b"key", 0, digits=5)


def test_format_code_groups_digits() -> None:
    assert totp.format_code("123456") == "123 456"
    assert totp.format_code("12345678") == "1234 5678"


def test_parse_otpauth_uri_full() -> None:
    uri = (
        "otpauth://totp/ACME%20Co:john.doe@example.com"
        f"?secret={SEED_SHA1}&issuer=ACME%20Co&algorithm=SHA256&digits=8&period=60"
    )
    fields = totp.parse_otpauth_uri(uri)
    assert fields == {
        "issuer": "ACME Co",
        "account": "john.doe@example.com",
        "secret": SEED_SHA1,
        "digits": 8,
        "period": 60,
        "algorithm": "SHA256",
    }


def test_parse_otpauth_uri_defaults() -> None:
    fields = totp.parse_otpauth_uri(f"otpauth://totp/alice@example.com?secret={SEED_SHA1}")
    assert fields["issuer"] == ""
    assert fields["account"] == "alice@example.com"
    assert fields["digits"] == 6
    assert fields["period"] == 30
    assert fields["algorithm"] == "SHA1"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "https://example.com",
        "otpauth://hotp/a?secret=GEZDGNBVGY3TQOJQ&counter=1",
        "otpauth://totp/a",
        "otpauth://totp/a?secret=GEZDGNBVGY3TQOJQ&digits=5",
        "otpauth://totp/a?secret=GEZDGNBVGY3TQOJQ&period=0",
        "otpauth://totp/a?secret=GEZDGNBVGY3TQOJQ&algorithm=MD5",
    ],
)
def test_parse_otpauth_uri_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        totp.parse_otpauth_uri(bad)


def test_uri_roundtrip() -> None:
    uri = totp.build_otpauth_uri(
        SEED_SHA1, issuer="ACME Co", account="john@example.com", digits=8, period=60, algorithm="SHA512"
    )
    assert totp.parse_otpauth_uri(uri) == {
        "issuer": "ACME Co",
        "account": "john@example.com",
        "secret": SEED_SHA1,
        "digits": 8,
        "period": 60,
        "algorithm": "SHA512",
    }


def test_random_secret_is_usable() -> None:
    secret = totp.random_secret()
    assert totp.normalize_secret(secret) == secret
    assert len(totp.totp(secret)) == 6
