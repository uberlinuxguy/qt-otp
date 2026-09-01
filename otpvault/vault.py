"""The encrypted vault: entries, unlock/lock lifecycle, atomic persistence."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import crypto, totp
from .crypto import BadPassword, CryptoError, VaultFormatError  # re-exported for callers

APP_DIR_NAME = "qt-otp"
VAULT_FILENAME = "vault.otpv"
PAYLOAD_VERSION = 1


class VaultLocked(RuntimeError):
    """Raised when an operation needs an unlocked vault."""


def default_vault_dir() -> Path:
    """Per-user data directory for the vault file."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def default_vault_path() -> Path:
    return default_vault_dir() / VAULT_FILENAME


@dataclass
class OtpEntry:
    """One authenticator token."""

    issuer: str = ""
    account: str = ""
    secret: str = ""
    digits: int = totp.DEFAULT_DIGITS
    period: int = totp.DEFAULT_PERIOD
    algorithm: str = totp.DEFAULT_ALGORITHM
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.issuer = (self.issuer or "").strip()
        self.account = (self.account or "").strip()
        self.notes = self.notes or ""
        self.algorithm = totp.normalize_algorithm(self.algorithm)
        self.digits = int(self.digits)
        self.period = int(self.period)
        if self.digits not in totp.ALLOWED_DIGITS:
            raise ValueError(f"digits must be one of {totp.ALLOWED_DIGITS}")
        if self.period <= 0:
            raise ValueError("period must be positive")
        self.secret = totp.normalize_secret(self.secret)
        if not (self.issuer or self.account):
            raise ValueError("an entry needs an issuer or an account name")

    @property
    def label(self) -> str:
        if self.issuer and self.account:
            return f"{self.issuer} — {self.account}"
        return self.issuer or self.account

    def code(self, at: float | None = None) -> str:
        return totp.totp(self.secret, at=at, period=self.period, digits=self.digits, algorithm=self.algorithm)

    def remaining(self, at: float | None = None) -> float:
        return totp.remaining_seconds(self.period, at=at)

    def to_uri(self) -> str:
        return totp.build_otpauth_uri(
            self.secret,
            issuer=self.issuer,
            account=self.account,
            digits=self.digits,
            period=self.period,
            algorithm=self.algorithm,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "issuer": self.issuer,
            "account": self.account,
            "secret": self.secret,
            "digits": self.digits,
            "period": self.period,
            "algorithm": self.algorithm,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, object]) -> "OtpEntry":
        return cls(
            issuer=str(obj.get("issuer", "")),
            account=str(obj.get("account", "")),
            secret=str(obj.get("secret", "")),
            digits=int(obj.get("digits", totp.DEFAULT_DIGITS)),
            period=int(obj.get("period", totp.DEFAULT_PERIOD)),
            algorithm=str(obj.get("algorithm", totp.DEFAULT_ALGORITHM)),
            notes=str(obj.get("notes", "")),
            id=str(obj.get("id") or uuid.uuid4().hex),
            created_at=float(obj.get("created_at", time.time())),
        )

    @classmethod
    def from_uri(cls, uri: str) -> "OtpEntry":
        fields = totp.parse_otpauth_uri(uri)
        return cls(**fields)  # type: ignore[arg-type]

    def wipe(self) -> None:
        """Drop the secret from this object (called when locking)."""
        self.secret = ""


class Vault:
    """An encrypted collection of OTP entries backed by a single file.

    While locked, `entries` is empty and no key material is held. Every mutation
    is written straight through to disk so a lock never loses data.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_vault_path()
        self._entries: list[OtpEntry] = []
        self._key: bytearray | None = None
        self._params: crypto.KdfParams | None = None

    # ---------------------------------------------------------------- state

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def locked(self) -> bool:
        return self._key is None

    @property
    def entries(self) -> list[OtpEntry]:
        return list(self._entries)

    def _require_unlocked(self) -> None:
        if self.locked:
            raise VaultLocked("vault is locked")

    # ------------------------------------------------------------ lifecycle

    def create(self, password: str) -> None:
        """Create a brand new empty vault. Fails if the file already exists."""
        if not password:
            raise ValueError("password must not be empty")
        if self.exists:
            raise FileExistsError(f"vault already exists: {self.path}")
        self._params = crypto.new_kdf_params()
        self._key = crypto.derive_key(password, self._params)
        self._entries = []
        self.save()

    def unlock(self, password: str) -> None:
        """Decrypt the vault file with `password`.

        Raises BadPassword, VaultFormatError, or OSError.
        """
        raw = self.path.read_bytes()
        plaintext, key, params = crypto.decrypt(raw, password)
        try:
            entries = self._decode_payload(plaintext)
        except Exception:
            crypto.zeroize(key)
            raise
        finally:
            del plaintext
        self._key, self._params, self._entries = key, params, entries

    def lock(self) -> None:
        """Forget the key and every plaintext secret."""
        crypto.zeroize(self._key)
        self._key = None
        for entry in self._entries:
            entry.wipe()
        self._entries = []

    def change_password(self, new_password: str) -> None:
        """Re-encrypt the vault under a new password (fresh salt)."""
        self._require_unlocked()
        if not new_password:
            raise ValueError("password must not be empty")
        assert self._params is not None
        params = crypto.rotate_salt(self._params)
        key = crypto.derive_key(new_password, params)
        old_key, old_params = self._key, self._params
        self._key, self._params = key, params
        try:
            self.save()
        except Exception:
            self._key, self._params = old_key, old_params
            crypto.zeroize(key)
            raise
        crypto.zeroize(old_key)

    def verify_password(self, password: str) -> bool:
        """Check a password against the on-disk vault without changing state."""
        try:
            plaintext, key, _ = crypto.decrypt(self.path.read_bytes(), password)
        except (CryptoError, OSError):
            return False
        crypto.zeroize(key)
        del plaintext
        return True

    # ----------------------------------------------------------- mutations

    def add(self, entry: OtpEntry) -> None:
        self._require_unlocked()
        self._entries.append(entry)
        self.save()

    def update(self, entry: OtpEntry) -> None:
        self._require_unlocked()
        for index, existing in enumerate(self._entries):
            if existing.id == entry.id:
                self._entries[index] = entry
                self.save()
                return
        raise KeyError(f"no entry with id {entry.id}")

    def remove(self, entry_id: str) -> None:
        self._require_unlocked()
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.id != entry_id]
        if len(self._entries) == before:
            raise KeyError(f"no entry with id {entry_id}")
        self.save()

    def reorder(self, entry_ids: list[str]) -> None:
        """Reorder entries to match `entry_ids` (unknown ids ignored)."""
        self._require_unlocked()
        by_id = {e.id: e for e in self._entries}
        ordered = [by_id.pop(eid) for eid in entry_ids if eid in by_id]
        ordered.extend(by_id.values())
        self._entries = ordered
        self.save()

    # --------------------------------------------------------- persistence

    def _encode_payload(self) -> bytes:
        payload = {
            "payload_version": PAYLOAD_VERSION,
            "updated_at": time.time(),
            "entries": [entry.to_dict() for entry in self._entries],
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _decode_payload(plaintext: bytes) -> list[OtpEntry]:
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise VaultFormatError("vault contents are not valid JSON") from exc
        if not isinstance(payload, dict):
            raise VaultFormatError("vault contents have an unexpected shape")
        version = payload.get("payload_version")
        if version != PAYLOAD_VERSION:
            raise VaultFormatError(f"unsupported payload version: {version!r}")
        raw_entries = payload.get("entries") or []
        if not isinstance(raw_entries, list):
            raise VaultFormatError("vault entries are not a list")
        entries: list[OtpEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                raise VaultFormatError("vault entry is not an object")
            entries.append(OtpEntry.from_dict(item))
        return entries

    def save(self) -> None:
        """Encrypt and write the vault atomically (temp file + replace)."""
        self._require_unlocked()
        assert self._key is not None and self._params is not None
        raw = crypto.encrypt(self._encode_payload(), self._key, self._params)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".tmp{os.getpid()}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        """Make the vault owner-only where the OS supports it."""
        if sys.platform == "win32":
            return  # NTFS ACLs are inherited from the user's AppData directory
        if not self.path.is_file():
            return
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def move_to(self, destination: Path | str, overwrite: bool = False) -> Path:
        """Move the vault file to `destination` and point this vault at it.

        Works locked or unlocked — the in-memory key is unaffected, and later
        saves go to the new location. If there is no file yet (the user is
        choosing where the vault will live), this just repoints the path.

        Raises FileExistsError if something is already there and `overwrite` is
        False, and OSError if the move itself fails; the path is left unchanged
        in both cases.
        """
        destination = Path(destination).expanduser()
        if destination.is_dir():
            destination = destination / self.path.name
        if destination == self.path:
            return self.path

        if destination.exists():
            if not overwrite:
                raise FileExistsError(f"{destination} already exists")
            if destination.is_dir():
                raise IsADirectoryError(f"{destination} is a directory")

        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            # shutil.move handles crossing drives, which os.replace cannot.
            shutil.move(str(self.path), str(destination))
        self.path = destination
        self._restrict_permissions()
        return self.path

    def export_copy(self, destination: Path | str) -> Path:
        """Write an encrypted backup copy of the current vault file."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.path.read_bytes())
        return destination


__all__ = [
    "BadPassword",
    "CryptoError",
    "OtpEntry",
    "Vault",
    "VaultFormatError",
    "VaultLocked",
    "default_vault_dir",
    "default_vault_path",
]
