# Launch qt-otp, creating the virtualenv on first run.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    $base = @(
        "C:\Program Files\Python313\python.exe",
        "C:\Program Files\Python312\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $base) {
        throw "No 64-bit Python found. Install Python 3.10+ (64-bit) and re-run."
    }
    Write-Host "Creating virtualenv with $base"
    & $base -m venv (Join-Path $root ".venv")
    & $python -m pip install --disable-pip-version-check -q -e $root
}

& $python -m otpvault @args
