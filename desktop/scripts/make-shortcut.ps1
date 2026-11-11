# Run from anywhere: powershell -ExecutionPolicy Bypass -File desktop/scripts/make-shortcut.ps1
param([string]$Desktop = (Split-Path -Parent $PSScriptRoot))
# A StrategyLab shortcut on the desktop and in the Start menu: Electron, run on the repository's
# desktop/ folder, with the app's icon and the same AppUserModelID the window sets.
$electron = Join-Path $Desktop "node_modules\electron\dist\electron.exe"
$icon = Join-Path $Desktop "build\icon.ico"
if (-not (Test-Path $electron)) { throw "electron.exe not found; run npm install in desktop/" }
$places = @(
  [Environment]::GetFolderPath("Desktop"),
  (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs")
)
$shell = New-Object -ComObject WScript.Shell
foreach ($place in $places) {
  $link = $shell.CreateShortcut((Join-Path $place "StrategyLab.lnk"))
  $link.TargetPath = $electron
  $link.Arguments = "."
  $link.WorkingDirectory = $Desktop
  $link.IconLocation = "$icon,0"
  $link.Description = "StrategyLab"
  $link.Save()
  Write-Output "made $(Join-Path $place 'StrategyLab.lnk')"
}
