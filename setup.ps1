# Ouranos-Pro setup (Windows).
# Installs everything the app needs: Python (a private copy, if the PC has none),
# the libraries, the Bibles, the speech models and the Desktop shortcuts.
# Needs internet ONCE (about 2.5 GB). After this, the app works fully offline.
#
#   Double-click Setup.bat, or in a terminal:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Options (for testing):
#   -PrivatePython   always download the private copy of Python, even if the PC has one
#   -NoShortcuts     do not create the Desktop / Start-menu shortcuts

param(
    [switch]$PrivatePython,
    [switch]$NoShortcuts
)

# Native commands are checked with $LASTEXITCODE (stderr output must not abort the script).
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"      # downloads are many times faster without the progress bar
Set-Location $PSScriptRoot

# The outcome is also written to logs\setup-result.txt (useful when helping someone remotely).
function Write-Result($text) {
    try {
        New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "logs") | Out-Null
        Set-Content -Path (Join-Path $PSScriptRoot "logs\setup-result.txt") -Value "$(Get-Date -Format s) $text" -Encoding ascii
    } catch { }
}

function Fail($message) {
    Write-Result "FAILED: $message"
    Write-Host ""
    Write-Host "ERROR: $message" -ForegroundColor Red
    Write-Host "You can run the setup again: it continues where it stopped." -ForegroundColor Yellow
    exit 1
}

function Step($number, $text) {
    Write-Host ""
    Write-Host "[$number/6] $text" -ForegroundColor Cyan
}

# The official Python 3.11 package from nuget.org: a plain zip with a complete Python
# (pip and venv included). It is unpacked inside this folder only -- nothing is
# installed on the PC, no administrator rights are needed.
$PythonPackageUrl = "https://www.nuget.org/api/v2/package/python/3.11.9"

function Get-PrivatePython {
    $folder = Join-Path $PSScriptRoot "runtime\python"
    $exe = Join-Path $folder "tools\python.exe"
    if (Test-Path $exe) { return $exe }
    Write-Host "Downloading Python 3.11 (about 20 MB) ..."
    $zip = Join-Path $PSScriptRoot "runtime\python.zip"
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "runtime") | Out-Null
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $PythonPackageUrl -OutFile $zip
        if (Test-Path $folder) { Remove-Item -Recurse -Force $folder }
        Expand-Archive -LiteralPath $zip -DestinationPath $folder -Force
        Remove-Item -Force $zip
    } catch {
        Fail "Could not download Python. Check your internet connection. ($($_.Exception.Message))"
    }
    if (-not (Test-Path $exe)) { Fail "The Python download is incomplete. Please run the setup again." }
    return $exe
}

Write-Host "=== Ouranos-Pro setup ===" -ForegroundColor Cyan
Write-Host "This takes 20-40 minutes and needs internet. You can keep using the computer."

# --- 1. Python environment ---------------------------------------------------
Step 1 "Python"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $created = $false
    # Use a Python 3.11 / 3.12 already on this PC when there is one (best library support).
    if (-not $PrivatePython -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($version in @("3.11", "3.12")) {
            & py "-$version" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Using the Python $version already on this computer."
                & py "-$version" -m venv .venv
                if ($LASTEXITCODE -eq 0) { $created = $true }
                break
            }
        }
    }
    # Otherwise fetch a private copy -- the user never has to install Python.
    if (-not $created) {
        $python = Get-PrivatePython
        Write-Host "Creating the Python environment ..."
        & $python -m venv .venv
        if ($LASTEXITCODE -eq 0) { $created = $true }
    }
    if (-not $created -or -not (Test-Path $venvPython)) { Fail "Could not create the Python environment." }
} else {
    Write-Host "Already done."
}

# --- 2. Libraries ------------------------------------------------------------
Step 2 "Libraries (the longest download, about 1 GB)"
& $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPython -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Could not install the libraries. Check your internet connection and try again." }

# --- 3. Bible database (KJV + Louis Segond 1910) -----------------------------
Step 3 "Bibles"
& $venvPython scripts\build_bible_db.py
if ($LASTEXITCODE -ne 0) { Fail "Could not build the Bible database." }

# --- 4. Offline speech models (about 630 MB) ---------------------------------
# "small" is the default; "base" is the faster choice for older laptops.
Step 4 "Speech models"
foreach ($model in @("small", "base")) {
    & $venvPython scripts\download_models.py --whisper $model
    if ($LASTEXITCODE -ne 0) { Fail "Could not download the speech model '$model'." }
}

# --- 5. Quoted-verse detection: embedding model + one embedding per verse ----
Step 5 "Verse index for quoted verses (5-15 minutes of computing)"
& $venvPython scripts\download_models.py --embedding
if ($LASTEXITCODE -ne 0) { Fail "Could not download the embedding model." }
& $venvPython scripts\build_embeddings.py
if ($LASTEXITCODE -ne 0) { Fail "Could not build the verse embeddings." }

# --- 6. App icon and shortcuts (Desktop + Start menu) ------------------------
Step 6 "Shortcuts"
if (-not (Test-Path (Join-Path $PSScriptRoot "static\app.ico"))) {
    & $venvPython scripts\make_icon.py
}
if ($NoShortcuts) {
    Write-Host "Skipped (-NoShortcuts)."
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\create_shortcut.ps1")
}

Write-Result "OK"
Write-Host ""
Write-Host "Setup complete. Start the app with the Ouranos-Pro icon on your Desktop." -ForegroundColor Green
