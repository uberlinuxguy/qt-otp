"""Vault lifecycle: create, unlock, persist, lock (wipe), change password."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otpvault import crypto
from otpvault.vault import BadPassword, OtpEntry, Vault, VaultFormatError, VaultLocked

PASSWORD = "hunter2-hunter2"
SECRET = "GEZDGNBVGY3TQOJQ"


@pytest.fixture(autouse=True)
def cheap_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt fast in tests without weakening the shipped default."""
    real_new = crypto.KdfParams

    def fast_params() -> crypto.KdfParams:
        return real_new(salt=b"0123456789abcdef", n=2**12)

    monkeypatch.setattr(crypto, "new_kdf_params", fast_params)


@pytest.fixture()
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "vault.otpv"


def make_entry(issuer: str = "GitHub", account: str = "me@example.com") -> OtpEntry:
    return OtpEntry(issuer=issuer, account=account, secret=SECRET)


def test_create_then_unlock_roundtrip(vault_path: Path) -> None:
    vault = Vault(vault_path)
    assert not vault.exists
    vault.create(PASSWORD)
    assert vault.exists
    vault.add(make_entry())
    vault.lock()

    reopened = Vault(vault_path)
    reopened.unlock(PASSWORD)
    assert [e.label for e in reopened.entries] == ["GitHub — me@example.com"]
    assert reopened.entries[0].secret == SECRET


def test_secret_is_not_stored_in_the_clear(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    raw = vault_path.read_bytes()
    assert SECRET.encode() not in raw
    assert b"GitHub" not in raw
    assert json.loads(raw)["magic"] == crypto.MAGIC


def test_wrong_password_is_rejected(vault_path: Path) -> None:
    Vault(vault_path).create(PASSWORD)
    with pytest.raises(BadPassword):
        Vault(vault_path).unlock("not the password")


def test_lock_wipes_entries_and_key(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    entry = make_entry()
    vault.add(entry)
    held = vault.entries[0]

    vault.lock()
    assert vault.locked
    assert vault.entries == []
    assert held.secret == ""  # the object the UI was holding lost its secret too
    with pytest.raises(VaultLocked):
        vault.add(make_entry())
    with pytest.raises(VaultLocked):
        vault.save()


def test_unlock_after_lock_works(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    vault.lock()
    vault.unlock(PASSWORD)
    assert len(vault.entries) == 1


def test_create_refuses_to_clobber_an_existing_vault(vault_path: Path) -> None:
    Vault(vault_path).create(PASSWORD)
    with pytest.raises(FileExistsError):
        Vault(vault_path).create("another password")


def test_update_and_remove(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    entry = make_entry()
    vault.add(entry)

    entry.account = "other@example.com"
    vault.update(entry)
    assert Vault(vault_path)  # sanity
    vault.lock()
    vault.unlock(PASSWORD)
    assert vault.entries[0].account == "other@example.com"

    vault.remove(vault.entries[0].id)
    assert vault.entries == []
    with pytest.raises(KeyError):
        vault.remove("nope")


def test_change_password(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    old_salt = vault._params.salt  # noqa: SLF001 - asserting the salt actually rotates

    vault.change_password("a brand new password")
    assert vault._params.salt != old_salt  # noqa: SLF001
    assert len(vault.entries) == 1

    vault.lock()
    with pytest.raises(BadPassword):
        vault.unlock(PASSWORD)
    vault.unlock("a brand new password")
    assert len(vault.entries) == 1


def test_verify_password(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    assert vault.verify_password(PASSWORD)
    assert not vault.verify_password("wrong")


def test_reorder(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    a, b, c = (make_entry(issuer=name) for name in ("A", "B", "C"))
    for entry in (a, b, c):
        vault.add(entry)
    vault.reorder([c.id, a.id])
    assert [e.issuer for e in vault.entries] == ["C", "A", "B"]


def test_export_copy_is_still_encrypted(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    backup = vault.export_copy(tmp_path / "backup.otpv")

    restored = Vault(backup)
    restored.unlock(PASSWORD)
    assert len(restored.entries) == 1
    assert SECRET.encode() not in backup.read_bytes()


def test_save_is_atomic_and_leaves_no_temp_files(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    assert list(vault_path.parent.iterdir()) == [vault_path]


def test_corrupted_payload_is_reported(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    # Re-seal a valid envelope around an unusable payload.
    raw = crypto.encrypt(b"not the payload we expect", vault._key, vault._params)  # noqa: SLF001
    vault_path.write_bytes(raw)
    with pytest.raises(VaultFormatError):
        Vault(vault_path).unlock(PASSWORD)


def test_entry_validation() -> None:
    with pytest.raises(ValueError):
        OtpEntry(issuer="", account="", secret=SECRET)
    with pytest.raises(ValueError):
        OtpEntry(issuer="x", secret=SECRET, digits=5)
    with pytest.raises(ValueError):
        OtpEntry(issuer="x", secret=SECRET, period=0)
    with pytest.raises(ValueError):
        OtpEntry(issuer="x", secret="not base32!")


def test_entry_from_uri_and_back() -> None:
    entry = OtpEntry.from_uri(f"otpauth://totp/ACME:me@example.com?secret={SECRET}&issuer=ACME&digits=8")
    assert entry.issuer == "ACME"
    assert entry.digits == 8
    assert len(entry.code()) == 8
    assert OtpEntry.from_uri(entry.to_uri()).secret == SECRET


def test_entry_dict_roundtrip_keeps_identity() -> None:
    entry = make_entry()
    clone = OtpEntry.from_dict(entry.to_dict())
    assert clone.id == entry.id
    assert clone.to_dict() == entry.to_dict()


# --------------------------------------------------------------- relocation


def test_move_to_moves_the_file_and_repoints_the_vault(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    target = tmp_path / "elsewhere" / "codes.otpv"

    assert vault.move_to(target) == target
    assert vault.path == target
    assert target.is_file()
    assert not vault_path.exists()
    assert not vault.locked  # moving does not disturb the open session

    vault.add(make_entry(issuer="Second"))  # later saves follow the vault to the new path
    reopened = Vault(target)
    reopened.unlock(PASSWORD)
    assert len(reopened.entries) == 2


def test_move_to_before_the_vault_exists_just_repoints(tmp_path: Path) -> None:
    """First-run case: the user picks a location before creating anything."""
    vault = Vault(tmp_path / "default.otpv")
    target = tmp_path / "chosen" / "vault.otpv"
    vault.move_to(target)
    assert vault.path == target
    assert not target.exists()

    vault.create(PASSWORD)
    assert target.is_file()
    assert not (tmp_path / "default.otpv").exists()


def test_move_to_the_same_path_is_a_no_op(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    before = vault_path.read_bytes()
    assert vault.move_to(vault_path) == vault_path
    assert vault_path.read_bytes() == before


def test_move_to_a_directory_keeps_the_filename(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    destination = tmp_path / "folder"
    destination.mkdir()
    assert vault.move_to(destination) == destination / vault_path.name
    assert (destination / vault_path.name).is_file()


def test_move_to_refuses_to_clobber_by_default(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    occupied = tmp_path / "taken.otpv"
    occupied.write_bytes(b"someone else's data")

    with pytest.raises(FileExistsError):
        vault.move_to(occupied)
    assert vault.path == vault_path  # unchanged
    assert vault_path.is_file()
    assert occupied.read_bytes() == b"someone else's data"


def test_move_to_can_overwrite_when_asked(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    occupied = tmp_path / "taken.otpv"
    occupied.write_bytes(b"someone else's data")

    vault.move_to(occupied, overwrite=True)
    assert vault.path == occupied
    reopened = Vault(occupied)
    reopened.unlock(PASSWORD)
    assert len(reopened.entries) == 1


def test_move_to_creates_missing_parent_directories(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    target = tmp_path / "a" / "b" / "c" / "vault.otpv"
    vault.move_to(target)
    assert target.is_file()


def test_move_to_works_while_locked(vault_path: Path, tmp_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())
    vault.lock()

    target = tmp_path / "moved.otpv"
    vault.move_to(target)
    assert vault.path == target
    vault.unlock(PASSWORD)
    assert len(vault.entries) == 1


# ---------------------------------------------------------------- importing


def test_inspect_file_describes_a_vault(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.add(make_entry())

    details = Vault.inspect_file(vault_path)
    assert details["cipher"] == crypto.CIPHER
    assert details["kdf"] == "scrypt"
    assert details["version"] == crypto.VERSION
    assert details["file_bytes"] == vault_path.stat().st_size


@pytest.mark.parametrize("content", [b"", b"not json", b'{"magic": "something-else"}'])
def test_inspect_file_rejects_things_that_are_not_vaults(tmp_path: Path, content: bytes) -> None:
    impostor = tmp_path / "impostor.otpv"
    impostor.write_bytes(content)
    with pytest.raises(VaultFormatError):
        Vault.inspect_file(impostor)


def test_inspect_file_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        Vault.inspect_file(tmp_path / "nothing-here.otpv")


def test_import_copies_a_vault_and_leaves_the_source_alone(tmp_path: Path) -> None:
    source_dir = tmp_path / "usb"
    source_dir.mkdir()
    source = source_dir / "backup.otpv"
    original = Vault(source)
    original.create(PASSWORD)
    original.add(make_entry())
    original.lock()
    before = source.read_bytes()

    target = tmp_path / "app" / "vault.otpv"
    imported = Vault(target)
    assert imported.import_from(source) == target

    assert source.read_bytes() == before, "the source must not be consumed"
    assert target.read_bytes() == before
    imported.unlock(PASSWORD)
    assert [e.label for e in imported.entries] == ["GitHub — me@example.com"]


def test_import_refuses_a_file_that_is_not_a_vault(tmp_path: Path) -> None:
    junk = tmp_path / "holiday.jpg"
    junk.write_bytes(b"\xff\xd8\xff\xe0 not a vault")
    target = tmp_path / "vault.otpv"

    with pytest.raises(VaultFormatError):
        Vault(target).import_from(junk)
    assert not target.exists(), "nothing should be written when the source is rejected"


def test_import_will_not_clobber_an_existing_vault(vault_path: Path, tmp_path: Path) -> None:
    existing = Vault(vault_path)
    existing.create(PASSWORD)
    existing.lock()
    untouched = vault_path.read_bytes()

    source = tmp_path / "other.otpv"
    other = Vault(source)
    other.create("a different password")
    other.lock()

    with pytest.raises(FileExistsError):
        Vault(vault_path).import_from(source)
    assert vault_path.read_bytes() == untouched

    Vault(vault_path).import_from(source, overwrite=True)
    assert vault_path.read_bytes() == source.read_bytes()


def test_import_refuses_to_import_a_vault_onto_itself(vault_path: Path) -> None:
    vault = Vault(vault_path)
    vault.create(PASSWORD)
    vault.lock()
    with pytest.raises(ValueError):
        vault.import_from(vault_path)


def test_import_requires_a_locked_vault(vault_path: Path, tmp_path: Path) -> None:
    source = tmp_path / "source.otpv"
    Vault(source).create(PASSWORD)

    open_vault = Vault(tmp_path / "open.otpv")
    open_vault.create(PASSWORD)
    with pytest.raises(VaultLocked):
        open_vault.import_from(source)


def test_import_leaves_no_temp_files(tmp_path: Path) -> None:
    source = tmp_path / "source.otpv"
    Vault(source).create(PASSWORD)
    target_dir = tmp_path / "app"
    target = target_dir / "vault.otpv"

    Vault(target).import_from(source)
    assert list(target_dir.iterdir()) == [target]


def test_point_at_adopts_a_vault_without_moving_it(tmp_path: Path) -> None:
    synced = tmp_path / "synced" / "vault.otpv"
    synced.parent.mkdir()
    original = Vault(synced)
    original.create(PASSWORD)
    original.add(make_entry())
    original.lock()

    app_vault = Vault(tmp_path / "default.otpv")
    assert app_vault.point_at(synced) == synced
    assert synced.is_file(), "the file stays where it was"
    assert not (tmp_path / "default.otpv").exists()

    app_vault.unlock(PASSWORD)
    assert len(app_vault.entries) == 1


def test_point_at_refuses_a_file_that_is_not_a_vault(tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("shopping list", encoding="utf-8")
    vault = Vault(tmp_path / "vault.otpv")
    with pytest.raises(VaultFormatError):
        vault.point_at(junk)
    assert vault.path == tmp_path / "vault.otpv", "the path must not change on failure"
