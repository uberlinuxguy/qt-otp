"""Password-based encryption for the vault file.

File layout (UTF-8 JSON, single line):

    {
      "magic": "qt-otp-vault",
      "version": 1,
      "cipher": "AES-256-GCM",
      "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1, "dklen": 32, "salt": "<b64>"},
      "nonce": "<b64>",
      "ciphertext": "<b64>"
    }

The header (everything but `ciphertext`) is fed to AES-GCM as additional
authenticated data, so KDF parameters cannot be downgraded by editing the file.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass, replace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = "qt-otp-vault"
VERSION = 1
CIPHER = "AES-256-GCM"

SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32

# ~32 MiB of memory, ~0.1 s on a typical desktop. Raise N (power of two) to harden.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


class CryptoError(Exception):
    """Base class for vault crypto failures."""


class BadPassword(CryptoError):
    """Wrong password, or the file has been tampered with."""


class VaultFormatError(CryptoError):
    """The file is not a vault this version understands."""


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except Exception as exc:  # noqa: BLE001 - any malformed value is a format error
        raise VaultFormatError("malformed base64 field") from exc


@dataclass(frozen=True)
class KdfParams:
    salt: bytes
    n: int = SCRYPT_N
    r: int = SCRYPT_R
    p: int = SCRYPT_P
    dklen: int = KEY_BYTES
    name: str = "scrypt"

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "n": self.n,
            "r": self.r,
            "p": self.p,
            "dklen": self.dklen,
            "salt": _b64e(self.salt),
        }

    @classmethod
    def from_json(cls, obj: object) -> "KdfParams":
        if not isinstance(obj, dict):
            raise VaultFormatError("missing kdf section")
        name = obj.get("name")
        if name != "scrypt":
            raise VaultFormatError(f"unsupported kdf: {name!r}")
        try:
            params = cls(
                salt=_b64d(obj["salt"]),
                n=int(obj["n"]),
                r=int(obj["r"]),
                p=int(obj["p"]),
                dklen=int(obj["dklen"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VaultFormatError("malformed kdf parameters") from exc
        if params.dklen != KEY_BYTES:
            raise VaultFormatError("unsupported key length")
        if params.n < 2**12 or params.n & (params.n - 1):
            raise VaultFormatError("implausible scrypt cost parameter")
        if not 1 <= params.r <= 64 or not 1 <= params.p <= 16:
            raise VaultFormatError("implausible scrypt parameters")
        if len(params.salt) < 8:
            raise VaultFormatError("salt too short")
        return params


def new_kdf_params() -> KdfParams:
    return KdfParams(salt=secrets.token_bytes(SALT_BYTES))


def rotate_salt(params: KdfParams) -> KdfParams:
    return replace(params, salt=secrets.token_bytes(SALT_BYTES))


def derive_key(password: str, params: KdfParams) -> bytearray:
    """Stretch `password` into a vault key.

    Returns a mutable buffer so the caller can zeroize it when locking.
    """
    kdf = Scrypt(salt=params.salt, length=params.dklen, n=params.n, r=params.r, p=params.p)
    return bytearray(kdf.derive(password.encode("utf-8")))


def zeroize(buf: bytearray | None) -> None:
    """Best-effort wipe of a mutable secret buffer."""
    if buf:
        for i in range(len(buf)):
            buf[i] = 0


def _header(params: KdfParams, nonce: bytes) -> dict[str, object]:
    return {
        "magic": MAGIC,
        "version": VERSION,
        "cipher": CIPHER,
        "kdf": params.to_json(),
        "nonce": _b64e(nonce),
    }


def _aad(header: dict[str, object]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt(plaintext: bytes, key: bytes | bytearray, params: KdfParams) -> bytes:
    """Seal `plaintext` into vault file bytes."""
    nonce = os.urandom(NONCE_BYTES)
    header = _header(params, nonce)
    ciphertext = AESGCM(bytes(key)).encrypt(nonce, plaintext, _aad(header))
    envelope = dict(header)
    envelope["ciphertext"] = _b64e(ciphertext)
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse(raw: bytes) -> tuple[KdfParams, bytes, bytes, bytes]:
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise VaultFormatError("file is not valid JSON") from exc
    if not isinstance(envelope, dict) or envelope.get("magic") != MAGIC:
        raise VaultFormatError("file is not a qt-otp vault")
    if envelope.get("version") != VERSION:
        raise VaultFormatError(f"unsupported vault version: {envelope.get('version')!r}")
    if envelope.get("cipher") != CIPHER:
        raise VaultFormatError(f"unsupported cipher: {envelope.get('cipher')!r}")
    if "nonce" not in envelope or "ciphertext" not in envelope:
        raise VaultFormatError("vault is missing required fields")

    params = KdfParams.from_json(envelope.get("kdf"))
    nonce = _b64d(envelope["nonce"])
    if len(nonce) != NONCE_BYTES:
        raise VaultFormatError("bad nonce length")
    ciphertext = _b64d(envelope["ciphertext"])
    aad = _aad(_header(params, nonce))
    return params, nonce, ciphertext, aad


def inspect(raw: bytes) -> dict[str, object]:
    """Describe a vault file without needing the password.

    Everything here comes from the authenticated header, so it is enough to
    tell a real vault from a stray file before doing anything with it. Raises
    VaultFormatError if the bytes are not a vault this version understands.
    """
    params, _nonce, ciphertext, _aad = _parse(raw)
    return {
        "version": VERSION,
        "cipher": CIPHER,
        "kdf": params.name,
        "kdf_cost": params.n,
        "ciphertext_bytes": len(ciphertext),
    }


def decrypt_with_key(raw: bytes, key: bytes | bytearray) -> tuple[bytes, KdfParams]:
    params, nonce, ciphertext, aad = _parse(raw)
    try:
        plaintext = AESGCM(bytes(key)).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise BadPassword("wrong key or corrupted vault") from exc
    return plaintext, params


def decrypt(raw: bytes, password: str) -> tuple[bytes, bytearray, KdfParams]:
    """Open vault file bytes with `password`.

    Returns (plaintext, derived key, kdf params). The key comes back so the
    session can re-encrypt on save without asking for the password again.
    """
    params, nonce, ciphertext, aad = _parse(raw)
    key = derive_key(password, params)
    try:
        plaintext = AESGCM(bytes(key)).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        zeroize(key)
        raise BadPassword("wrong password or corrupted vault") from exc
    return plaintext, key, params
