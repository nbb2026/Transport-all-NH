param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath
)

$ErrorActionPreference = "Stop"
$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path
$desktop = [Environment]::GetFolderPath("Desktop")
if ([string]::IsNullOrWhiteSpace($desktop)) {
    throw "Windows Desktop path could not be resolved."
}

$shortcutPath = Join-Path $desktop "Magneto-Electro-Optical Transport V3.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $resolvedExe
$shortcut.WorkingDirectory = Split-Path -Parent $resolvedExe
$shortcut.IconLocation = "$resolvedExe,0"
$shortcut.Description = "Magneto-Electro-Optical Transport integrated experiment control V3 revision 3.2.0"
$shortcut.WindowStyle = 1
$shortcut.Save()

if (-not (Test-Path -LiteralPath $shortcutPath)) {
    throw "Desktop shortcut was not created: $shortcutPath"
}

Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host "  $shortcutPath"
