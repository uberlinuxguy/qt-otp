# qt-otp

A Qt desktop app that holds your TOTP (authenticator) codes in a single
password-encrypted file, shows the live 6/8-digit codes, and locks itself the
moment you lock your workstation.

![code list](docs/codes.png)

## What it does

- **Many accounts, one file.** Add codes by hand or by pasting an
  `otpauth://totp/...` URI from a QR code. Issuer, account, algorithm
  (SHA1/SHA256/SHA512), digit count (6/7/8) and period are all per-entry.
- **Encrypted at rest.** The vault is AES-256-GCM; the key comes from your
  master password via scrypt (N=32768, r=8, p=1). The KDF header is
  authenticated, so nobody can edit the file to weaken the cost parameters.
  Nothing is written in the clear — not even issuer names.
- **Password at startup.** The app opens on an unlock screen. Wrong guesses are
  slowed down after three tries.
- **You choose where the vault lives.** The create screen on first run offers the
  location before anything is written, and Tools → Settings can change it later,
  which moves the existing vault file to the new place. See
  [Where the vault lives](#where-the-vault-lives).
- **Locks when you walk away.** See [Locking](#locking).
- **Live codes.** Each row shows the current code and a countdown bar; the code
  turns amber under 10 s and red under 5 s.
- **Click to copy.** Clicking anywhere on a row copies that row's code, and the
  status bar confirms it: `Copied the code for GitHub — you@example.com to the
  clipboard · clipboard clears in 20s`. The clipboard self-clears after 20 s
  (configurable), and clears immediately when the vault locks.

## Locking

The vault re-locks — dropping the key and every plaintext secret out of memory —
on any of these:

| Trigger | How it is detected |
| --- | --- |
| You lock your workstation (Win+L, screensaver, RDP disconnect, switch user) | Windows: a message-only window subscribed to WTS session notifications (`WM_WTSSESSION_CHANGE`). Linux: `org.freedesktop.login1` `Lock`/`Unlock` and the screensaver's `ActiveChanged` over DBus. |
| The machine goes to sleep | `WM_POWERBROADCAST` / `PrepareForSleep` |
| No keyboard or mouse activity for 5 min (configurable) | `GetLastInputInfo` on Windows — system-wide, not just this app — falling back to in-app activity elsewhere |
| The window is minimized | optional, off by default |
| You press Ctrl+L, or quit | — |

macOS has no native hook here, so it relies on the inactivity timer. The status
bar tells you which detector is active, and Tools → Settings turns each one off.

The window, taskbar and tray icon greys out while the vault is locked, so the
lock state is readable without switching to the app.

## Install and run

### Windows: just the .exe

Grab `qt-otp-vX.X-windows-x64.exe` from the
[latest release](../../releases/latest) and run it. One self-contained file
(~47 MB), no installer and no Python needed; it keeps its vault in the same
place as a source install.

The executable is not code-signed, so SmartScreen will warn on first run —
**More info** then **Run anyway**. Every release ships a `.sha256` sidecar if
you would rather verify the download:

```powershell
(Get-FileHash .\qt-otp-v1.0-windows-x64.exe -Algorithm SHA256).Hash
```

### From source

Needs 64-bit Python 3.10+ (PySide6 publishes no 32-bit Windows wheels).

```powershell
& "C:\Program Files\Python313\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m otpvault
```

Or just `.\run.ps1`, which does the same thing and reuses the venv.

```text
python -m otpvault [--vault PATH] [--verbose]
```

## Where the vault lives

By default the vault is `%APPDATA%\qt-otp\vault.otpv` on Windows
(`~/.local/share/qt-otp/vault.otpv` on Linux, `~/Library/Application Support` on
macOS). Three ways to put it somewhere else:

![first run](docs/first-run.png)

1. **On first run**, the create screen shows where the vault is about to be
   written, with a **Change…** button. Nothing is written until you set the
   password, so choosing a location costs nothing.
2. **Later**, Tools → Settings → *Vault file*. Changing it **moves the existing
   vault file** to the new location and remembers it; the open session keeps
   working and the next save goes to the new path. If a file is already there you
   are warned before anything is replaced, and a failed move leaves the vault
   exactly where it was.
3. **For one run only**, `--vault PATH`. That overrides the saved location
   without changing it, and the Settings row is disabled for that run.

A synced folder (Nextcloud, Dropbox) works — the file is encrypted and safe to
sync — but avoid writing to it from two machines at once.

## Keyboard

| Key | Action |
| --- | --- |
| `Ctrl+N` | Add code |
| `Ctrl+E` | Edit selected code |
| `Del` | Delete selected code |
| `Ctrl+C` / `Enter` | Copy the selected code (or just click the row) |
| `Ctrl+F` | Search |
| `Ctrl+L` | Lock now |
| `Ctrl+Q` | Quit |

## Backups

`Vault → Export encrypted backup…` copies the encrypted file as-is, so the copy
needs the same master password. **There is no password recovery.** If you forget
the master password the codes are gone — keep each service's recovery codes
somewhere else.

## Layout

```text
otpvault/
  totp.py       RFC 4226/6238 HOTP+TOTP and otpauth:// URIs (stdlib only)
  crypto.py     scrypt + AES-256-GCM envelope, authenticated header
  vault.py      entries, unlock/lock lifecycle, atomic writes
  lockwatch.py  session-lock / sleep / idle detection per platform
  config.py     QSettings-backed preferences (nothing sensitive)
  ui/           unlock screen, code table, dialogs, icons
  resources/    qt-otp-icon.svg, rendered per size for the window, taskbar and tray
  selftest.py   post-build checks, run with --selftest
tests/          RFC 6238 vectors, crypto tamper cases, vault lifecycle
tools/          make_icon.py (SVG -> .ico), entrypoint.py (PyInstaller script)
qt-otp.spec     PyInstaller build definition
```

## Building the executable

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,build]"
.\.venv\Scripts\python.exe tools\make_icon.py          # build\qt-otp.ico from the SVG
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean qt-otp.spec
```

That writes `dist\qt-otp.exe`: windowed, single file, UPX off (it saves a few
MB and reliably trips antivirus heuristics). The `.ico` is generated from
`qt-otp-icon.svg` by the same code path the app uses for its window icon, so the
two cannot drift apart.

Freezing breaks things that pass in a checkout — the OpenSSL backend behind
scrypt, bundled data files, Qt plugins, ctypes DLL lookups — so the build has a
self-check:

```powershell
.\dist\qt-otp.exe --selftest report.json | Out-Host
```

It exercises the real machinery (scrypt round-trip, SVG rendering, the
session-lock watcher, opening the main window) and exits non-zero if anything is
wrong. Onefile builds extract to a temp directory on launch, so first start
takes a second or two.

The pipe is load-bearing: PowerShell does not wait for GUI-subsystem
executables, so without it the checks print after your next prompt and
`$LASTEXITCODE` belongs to the previous command. In a script, use
`Start-Process -Wait -PassThru` and read its `ExitCode` — which is what the
release workflow does.

## Releasing

1. Bump `__version__` in [`otpvault/__init__.py`](otpvault/__init__.py) — the
   only place a version number lives; `pyproject.toml` reads it from there.
2. Tag and push:

   ```powershell
   git tag v1.1
   git push origin v1.1
   ```

[`.github/workflows/release.yml`](.github/workflows/release.yml) then runs on a
Windows runner: it refuses tags that disagree with `__version__`, runs the test
suite, builds the exe, smoke-tests it with `--selftest`, and publishes a release
with the executable and its SHA-256. Re-running a tag replaces the assets
instead of failing. `workflow_dispatch` builds without releasing, if you want to
rehearse.

The trigger is the `vX.X` shape (`v1.0`, `v2.11`). To release patch tags too,
add `'v[0-9]+.[0-9]+.[0-9]+'` to the `tags:` list.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

155 tests, no display required (Qt runs offscreen):

- the full RFC 6238 vector table for all three hash algorithms, plus URI parsing;
- wrong-password, tampered-ciphertext and KDF-downgrade rejection;
- the vault's create/lock/unlock/rekey behaviour, including that locking really
  does blank the secrets the UI was holding;
- the auto-lock triggers — the Windows cases post the same
  `WM_WTSSESSION_CHANGE` / `WM_POWERBROADCAST` messages the OS sends on Win+L
  into the real message-only window, so the whole path is covered;
- the main window end to end: create, render codes, click-to-copy, search, lock,
  retry with a wrong password, re-unlock;
- the icon: that the SVG ships inside the package, renders at every size, and
  that the locked variant is a desaturated version of the same artwork;
- the vault location: moving a live vault, refusing to clobber another file
  without confirmation, keeping the old path when a move fails, and never
  persisting a `--vault` override;
- the release pipeline: that the workflow still triggers on `vX.X`, tests before
  it publishes, smoke-tests the executable, and references files that exist.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Security notes, honestly

- While unlocked, secrets are plaintext in the process's memory — as they must
  be to compute codes. Python strings can't be reliably wiped; locking clears
  the key buffer and drops the entry objects' secrets, which is the practical
  limit. Locking often is the real defense.
- The vault file's confidentiality rests entirely on your master password.
  scrypt at 32 MiB makes guessing expensive, not impossible: use a long
  passphrase.
- No process-memory hardening, no anti-screenshot tricks, no protection against
  someone with code execution as your user. It defends a stolen file and an
  unattended screen, not a compromised account.
