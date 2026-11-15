# Builds dist\Ouranos-Pro-Setup.exe: ONE small file to send to other people.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#
# What the installer does on the other person's PC:
#   1. unpacks the program into  %LOCALAPPDATA%\Programs\Ouranos-Pro  (no administrator rights);
#   2. runs setup.ps1 there, which downloads Python, the libraries, the Bibles and the
#      speech models (about 2.5 GB, 20-40 minutes) and creates the Desktop shortcuts.
# Running the installer again later updates the program and keeps the downloads.
#
# It is made with IExpress, a tool that is part of Windows, so nothing needs to be
# installed to build it. The models are NOT inside the file: that keeps it small
# enough to share with a simple link.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
# IExpress cannot handle accented letters in paths, so the build happens in the temp folder.
$work = Join-Path $env:TEMP "ouranos-installer-build"
$stage = Join-Path $work "stage"
$package = Join-Path $work "package"

if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force -Path $stage, $package, $dist | Out-Null

# --- 1. Collect the program files (no downloads, no personal settings) -------
$folders = @("app", "static", "scripts")
foreach ($folder in $folders) {
    Copy-Item -Recurse -Path (Join-Path $root $folder) -Destination (Join-Path $stage $folder)
}
Get-ChildItem -Path $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $stage "data") | Out-Null
Copy-Item (Join-Path $root "data\books.json") (Join-Path $stage "data\books.json")
foreach ($file in @("requirements.txt", "setup.ps1", "run.ps1", "Setup.bat",
                    "Start Ouranos-Pro.bat", "Start with projector.bat", "README.md")) {
    Copy-Item (Join-Path $root $file) (Join-Path $stage $file)
}
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath (Join-Path $package "payload.zip") -Force

# --- 2. The script the installer runs after unpacking itself -----------------
$installCmd = @'
@echo off
title Ouranos-Pro Setup
set "TARGET=%LOCALAPPDATA%\Programs\Ouranos-Pro"
if not "%OURANOS_INSTALL_DIR%"=="" set "TARGET=%OURANOS_INSTALL_DIR%"
echo.
echo  Ouranos-Pro will be installed in:
echo    %TARGET%
echo.
echo  Keep this window open and stay connected to the internet.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "New-Item -ItemType Directory -Force -Path $env:TARGET | Out-Null; Expand-Archive -LiteralPath '%~dp0payload.zip' -DestinationPath $env:TARGET -Force"
if errorlevel 1 (
  echo  ERROR: the program files could not be copied.
  if "%OURANOS_NO_PAUSE%"=="" pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%TARGET%\setup.ps1" %OURANOS_SETUP_ARGS%
echo.
if "%OURANOS_NO_PAUSE%"=="" pause
'@
Set-Content -Path (Join-Path $package "install.cmd") -Value $installCmd -Encoding ascii

# --- 3. IExpress description file ----------------------------------------------
$target = Join-Path $work "Ouranos-Pro-Setup.exe"
$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=Install Ouranos-Pro on this computer? It needs internet and about 6 GB of free disk space.
DisplayLicense=
FinishMessage=
TargetName=$target
FriendlyName=Ouranos-Pro Setup
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=install.cmd
UserQuietInstCmd=install.cmd
FILE0="payload.zip"
FILE1="install.cmd"
[SourceFiles]
SourceFiles0=$package\
[SourceFiles0]
%FILE0%=
%FILE1%=
"@
$sedPath = Join-Path $work "installer.sed"
Set-Content -Path $sedPath -Value $sed -Encoding ascii

# --- 4. Build ------------------------------------------------------------------
$iexpress = Join-Path $env:SystemRoot "System32\iexpress.exe"
if (-not (Test-Path $iexpress)) { throw "IExpress was not found in Windows (System32\iexpress.exe)." }
Start-Process -FilePath $iexpress -ArgumentList "/N", "/Q", $sedPath -Wait   # IExpress rejects a quoted path
if (-not (Test-Path $target)) { throw "IExpress did not produce the installer." }

$final = Join-Path $dist "Ouranos-Pro-Setup.exe"
Copy-Item $target $final -Force
$sizeMb = [math]::Round((Get-Item $final).Length / 1MB, 2)
Write-Host "Installer ready: $final ($sizeMb MB)" -ForegroundColor Green
