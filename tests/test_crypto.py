"""Envelope encryption behaviour: roundtrip, wrong password, tamper detection."""

from __future__ import annotations

import base64
import json

import pytest

from otpvault import crypto

# Cheap KDF cost for tests; the app uses crypto.SCRYPT_N.
TEST_PARAMS_N = 2**12


def fast_params() -> crypto.KdfParams:
    return crypto.KdfParams(salt=b"0123456789abcdef", n=TEST_PARAMS_N)


def seal(plaintext: bytes, password: str = "correct horse") -> tuple[bytes, crypto.KdfParams]:
    params = fast_params()
    key = crypto.derive_key(password, params)
    return crypto.encrypt(plaintext, key, params), params


def test_roundtrip() -> None:
    raw, _ = seal(b'{"hello": "world"}')
    plaintext, key, params = crypto.decrypt(raw, "correct horse")
    assert plaintext == b'{"hello": "world"}'
    assert len(key) == crypto.KEY_BYTES
    assert params.n == TEST_PARAMS_N


def test_wrong_password_is_rejected() -> None:
    raw, _ = seal(b"secret")
    with pytest.raises(crypto.BadPassword):
        crypto.decrypt(raw, "wrong horse")


def test_ciphertext_differs_between_saves_of_identical_data() -> None:
    params = fast_params()
    key = crypto.derive_key("pw", params)
    first = crypto.encrypt(b"same", key, params)
    second = crypto.encrypt(b"same", key, params)
    assert first != second  # fresh nonce every time
    assert crypto.decrypt_with_key(first, key)[0] == crypto.decrypt_with_key(second, key)[0]


def test_tampered_ciphertext_is_rejected() -> None:
    raw, _ = seal(b"secret payload")
    envelope = json.loads(raw)
    blob = bytearray(base64.b64decode(envelope["ciphertext"]))
    blob[0] ^= 0xFF
    envelope["ciphertext"] = base64.b64encode(bytes(blob)).decode()
    with pytest.raises(crypto.BadPassword):
        crypto.decrypt(json.dumps(envelope).encode(), "correct horse")


def test_kdf_downgrade_is_rejected() -> None:
    """The header is authenticated, so editing the cost parameter fails."""
    raw, _ = seal(b"secret payload")
    envelope = json.loads(raw)
    envelope["kdf"]["n"] = 2**12 if envelope["kdf"]["n"] != 2**12 else 2**13
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(json.dumps(envelope).encode(), "correct horse")


def test_swapped_nonce_is_rejected() -> None:
    raw, _ = seal(b"secret payload")
    envelope = json.loads(raw)
    envelope["nonce"] = base64.b64encode(b"\x00" * crypto.NONCE_BYTES).decode()
    with pytest.raises(crypto.BadPassword):
        crypto.decrypt(json.dumps(envelope).encode(), "correct horse")


@pytest.mark.parametrize(
    "mutation",
    [
        {"magic": "something-else"},
        {"version": 99},
        {"cipher": "AES-128-GCM"},
        {"kdf": {"name": "pbkdf2", "n": 1, "r": 8, "p": 1, "dklen": 32, "salt": "AAAAAAAAAAA="}},
    ],
)
def test_unsupported_envelopes_are_format_errors(mutation: dict) -> None:
    raw, _ = seal(b"payload")
    envelope = json.loads(raw)
    envelope.update(mutation)
    with pytest.raises(crypto.VaultFormatError):
        crypto.decrypt(json.dumps(envelope).encode(), "correct horse")


def test_garbage_file_is_a_format_error() -> None:
    with pytest.raises(crypto.VaultFormatError):
        crypto.decrypt(b"this is not json", "pw")


def test_zeroize_wipes_the_key() -> None:
    key = crypto.derive_key("pw", fast_params())
    assert any(key)
    crypto.zeroize(key)
    assert not any(key)


def test_rotate_salt_changes_the_derived_key() -> None:
    params = fast_params()
    rotated = crypto.rotate_salt(params)
    assert rotated.salt != params.salt
    assert crypto.derive_key("pw", params) != crypto.derive_key("pw", rotated)
