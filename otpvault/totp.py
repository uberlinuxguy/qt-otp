"""RFC 4226 / RFC 6238 HOTP + TOTP, and otpauth:// URI handling.

Deliberately dependency-free: everything here is hmac/hashlib/base64 so the
crypto surface of the app stays small and auditable.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import struct
import time
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

# Values are digest names; hmac resolves them through hashlib.
ALGORITHMS: dict[str, str] = {
    "SHA1": "sha1",
    "SHA256": "sha256",
    "SHA512": "sha512",
}

DEFAULT_ALGORITHM = "SHA1"
DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30
ALLOWED_DIGITS = (6, 7, 8)


class InvalidSecret(ValueError):
    """Raised when a shared secret is not usable base32."""


def normalize_algorithm(name: str | None) -> str:
    if not name:
        return DEFAULT_ALGORITHM
    key = name.strip().upper().replace("-", "")
    if key not in ALGORITHMS:
        raise ValueError(f"unsupported algorithm: {name!r}")
    return key


def normalize_secret(secret: str) -> str:
    """Return the canonical (uppercase, unpadded, ungrouped) base32 secret.

    Authenticator secrets get passed around with spaces, dashes and mixed case;
    accept all of that, then validate that it actually decodes.
    """
    if secret is None:
        raise InvalidSecret("secret is empty")
    cleaned = "".join(secret.split()).replace("-", "").replace("_", "").upper().rstrip("=")
    if not cleaned:
        raise InvalidSecret("secret is empty")
    decode_secret(cleaned)
    return cleaned


def decode_secret(secret: str) -> bytes:
    """Decode a base32 shared secret into raw key bytes."""
    cleaned = "".join((secret or "").split()).replace("-", "").replace("_", "").upper().rstrip("=")
    if not cleaned:
        raise InvalidSecret("secret is empty")
    padding = "=" * (-len(cleaned) % 8)
    try:
        key = base64.b32decode(cleaned + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidSecret("secret is not valid base32") from exc
    if not key:
        raise InvalidSecret("secret decodes to zero bytes")
    return key


def random_secret(nbytes: int = 20) -> str:
    """Generate a fresh base32 secret."""
    import secrets

    return base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def hotp(key: bytes, counter: int, digits: int = DEFAULT_DIGITS, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """RFC 4226 HOTP value as a zero-padded decimal string."""
    if digits not in ALLOWED_DIGITS:
        raise ValueError(f"digits must be one of {ALLOWED_DIGITS}")
    digestmod = ALGORITHMS[normalize_algorithm(algorithm)]
    mac = hmac.new(key, struct.pack(">Q", counter), digestmod).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp(
    secret: str,
    at: float | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """RFC 6238 TOTP value for `secret` at unix time `at` (default: now)."""
    if period <= 0:
        raise ValueError("period must be positive")
    timestamp = time.time() if at is None else at
    counter = int(timestamp // period)
    return hotp(decode_secret(secret), counter, digits, algorithm)


def remaining_seconds(period: int = DEFAULT_PERIOD, at: float | None = None) -> float:
    """Seconds left in the current time step."""
    timestamp = time.time() if at is None else at
    return period - (timestamp % period)


def format_code(code: str) -> str:
    """Group digits for readability: 123456 -> '123 456'."""
    if len(code) == 8:
        return f"{code[:4]} {code[4:]}"
    if len(code) in (6, 7):
        return f"{code[:3]} {code[3:]}"
    return code


def parse_otpauth_uri(uri: str) -> dict[str, object]:
    """Parse an `otpauth://totp/...` URI into entry fields.

    Returns a dict with keys: issuer, account, secret, digits, period, algorithm.
    """
    text = (uri or "").strip()
    if not text:
        raise ValueError("empty URI")
    parsed = urlparse(text)
    if parsed.scheme.lower() != "otpauth":
        raise ValueError("not an otpauth:// URI")
    kind = parsed.netloc.lower()
    if kind and kind != "totp":
        raise ValueError(f"unsupported OTP type {kind!r} (only totp is supported)")

    params = {k.lower(): v for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
    if "secret" not in params:
        raise ValueError("URI has no secret parameter")

    label = unquote(parsed.path or "").lstrip("/")
    issuer, account = "", label
    if ":" in label:
        issuer, account = label.split(":", 1)
    issuer = params.get("issuer", issuer).strip()
    account = account.strip()

    try:
        digits = int(params.get("digits", DEFAULT_DIGITS))
        period = int(params.get("period", DEFAULT_PERIOD))
    except ValueError as exc:
        raise ValueError("digits and period must be integers") from exc
    if digits not in ALLOWED_DIGITS:
        raise ValueError(f"unsupported digits value: {digits}")
    if period <= 0:
        raise ValueError("period must be positive")

    return {
        "issuer": issuer,
        "account": account,
        "secret": normalize_secret(params["secret"]),
        "digits": digits,
        "period": period,
        "algorithm": normalize_algorithm(params.get("algorithm")),
    }


def build_otpauth_uri(
    secret: str,
    issuer: str = "",
    account: str = "",
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Render entry fields back into an otpauth:// URI."""
    label = f"{issuer}:{account}" if issuer else account
    query = {
        "secret": normalize_secret(secret),
        "algorithm": normalize_algorithm(algorithm),
        "digits": str(digits),
        "period": str(period),
    }
    if issuer:
        query["issuer"] = issuer
    return f"otpauth://totp/{quote(label, safe='')}?{urlencode(query)}"
