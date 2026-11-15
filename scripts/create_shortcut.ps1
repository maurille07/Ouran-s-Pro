# Creates the Ouranos-Pro shortcuts (Desktop + Start menu) with the app icon.
# The shortcut starts the app in its own window, with no black console window.
#
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "static\app.ico"

if (-not (Test-Path $pythonw)) {
    Write-Host "Ouranos-Pro is not set up yet. Run setup.ps1 first." -ForegroundColor Red
    exit 1
}

# The app name contains an accented letter; build it from its character code so this
# script stays plain ASCII (Windows PowerShell misreads other encodings).
$appName = "Ouran" + [char]0x00F2 + "s-Pro"

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    @{ Folder = [Environment]::GetFolderPath("Desktop");  Name = "$appName.lnk";              Arguments = "-m app.launcher" },
    @{ Folder = [Environment]::GetFolderPath("Desktop");  Name = "$appName + projector.lnk";  Arguments = "-m app.launcher --projector" },
    @{ Folder = [Environment]::GetFolderPath("Programs"); Name = "$appName.lnk";              Arguments = "-m app.launcher" }
)
foreach ($target in $targets) {
    $path = Join-Path $target.Folder $target.Name
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = $target.Arguments
    $shortcut.WorkingDirectory = $root
    $shortcut.Description = "Live Bible verse display"
    if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
    $shortcut.Save()
    Write-Host "Created: $path"
}
