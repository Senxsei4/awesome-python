# ===========================================================================
#  Creates a Desktop shortcut to OracleAI.exe with the Oracle icon.
#  Use this if you downloaded the bare dist\OracleAI.exe instead of running the
#  installer. Right-click > "Run with PowerShell", or:
#      powershell -ExecutionPolicy Bypass -File create_desktop_shortcut.ps1
# ===========================================================================
$ErrorActionPreference = "Stop"

$here    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$exe     = Join-Path $here "dist\OracleAI.exe"
if (-not (Test-Path $exe)) { $exe = Join-Path $here "OracleAI.exe" }   # bare exe fallback
$icon    = Join-Path $here "assets\oracle.ico"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Oracle AI.lnk"

if (-not (Test-Path $exe)) {
    Write-Error "OracleAI.exe not found next to this script. Build it first (build_windows.bat)."
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = Split-Path -Parent $exe
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Description = "Oracle AI: Project Citadel auto-trader"
$lnk.Save()

Write-Host "Desktop shortcut created: $lnkPath"
