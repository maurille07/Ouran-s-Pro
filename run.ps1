# Ouranos-Pro launcher (Windows).
#
#   .\run.ps1            start the app and open the operator screen
#   .\run.ps1 -Kiosk     also open the projector screen full-screen on the 2nd monitor
#   .\run.ps1 -NoBrowser start the server only
#   .\run.ps1 -Port 9000 use another port
#
# Stop the app with Ctrl+C in this window.

param(
    [switch]$Kiosk,
    [switch]$NoBrowser,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Ouranos-Pro is not set up yet. Run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $PSScriptRoot "data\bible.sqlite"))) {
    Write-Host "The Bible database is missing. Run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

$base = "http://127.0.0.1:$Port"

function Find-Browser {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($path in $candidates) { if ($path -and (Test-Path $path)) { return $path } }
    return $null
}

# The browser windows are opened by a small helper that waits until the server answers.
$kioskCommand = ""
if ($Kiosk) {
    $browser = Find-Browser
    if ($browser) {
        # Put the kiosk window on the second monitor (the projector) when there is one.
        Add-Type -AssemblyName System.Windows.Forms
        $screens = [System.Windows.Forms.Screen]::AllScreens
        $target = $screens | Where-Object { -not $_.Primary } | Select-Object -First 1
        if (-not $target) {
            $target = $screens | Select-Object -First 1
            Write-Host "No second monitor detected: the projector screen will open on this screen (Alt+F4 closes it)." -ForegroundColor Yellow
        }
        $x = $target.Bounds.X
        $y = $target.Bounds.Y
        $profileDir = Join-Path $env:LOCALAPPDATA "OuranosPro\kiosk-profile"
        $kioskCommand = "Start-Process -FilePath '$browser' -ArgumentList '--kiosk $base/display --user-data-dir=`"$profileDir`" --window-position=$x,$y --no-first-run --no-default-browser-check --disable-session-crashed-bubble --autoplay-policy=no-user-gesture-required';"
    } else {
        Write-Host "Edge/Chrome not found: open $base/display manually on the projector and press F11." -ForegroundColor Yellow
    }
}
$operatorCommand = ""
if (-not $NoBrowser) { $operatorCommand = "Start-Process '$base/operator';" }

if ($kioskCommand -or $operatorCommand) {
    $wait = "for (`$i = 0; `$i -lt 60; `$i++) { try { Invoke-WebRequest -UseBasicParsing '$base/api/state' -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } };"
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", "$wait $operatorCommand $kioskCommand"
}

Write-Host "Ouranos-Pro is starting ..." -ForegroundColor Cyan
Write-Host "  Operator screen : $base/operator"
Write-Host "  Projector screen: $base/display"
Write-Host "  Stop with Ctrl+C"
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --log-level warning
