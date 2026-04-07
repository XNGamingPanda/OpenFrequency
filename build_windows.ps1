param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    $Python = "python"
}

Get-Process | Where-Object { $_.ProcessName -like "OpenFrequency*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "build")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist\OpenFrequency")
}

& $Python -m pip install -r requirements-build.txt
& $Python -m PyInstaller --clean --noconfirm openfrequency.spec

$BuildExeDir = Join-Path $Root "build\openfrequency"
if (Test-Path $BuildExeDir) {
    Get-ChildItem -Path $BuildExeDir -Filter "OpenFrequency*.exe" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

$BundledConfig = Join-Path $Root "dist\OpenFrequency\config.json"
if (Test-Path $BundledConfig) {
    throw "config.json was bundled unexpectedly: $BundledConfig"
}

Write-Host "Build complete. Run only these final outputs:"
Write-Host "  dist\OpenFrequency\OpenFrequency.exe"
Write-Host "  dist\OpenFrequency\OpenFrequency-Console.exe"
Write-Host "Do not run executables from build\openfrequency; that folder is PyInstaller intermediate output."
Write-Host "Runtime config/logs are stored under %APPDATA%\OpenFrequency"
