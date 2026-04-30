param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function New-ZipFromDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$ZipPath
    )

    if (Test-Path $ZipPath) {
        Remove-Item -Force $ZipPath
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($SourceDir, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    $Python = "python"
}

Get-Process | Where-Object { $_.ProcessName -like "OpenFrequency*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "build")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist\OpenFrequency")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root "dist\packages")
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

$BundledCabinMedia = Join-Path $Root "dist\OpenFrequency\_internal\static\cabin_media"
if (Test-Path $BundledCabinMedia) {
    throw "static/cabin_media was bundled unexpectedly: $BundledCabinMedia"
}

$BundledModels = Join-Path $Root "dist\OpenFrequency\_internal\models"
if (Test-Path $BundledModels) {
    throw "models were bundled into the main package unexpectedly: $BundledModels"
}

# Read version from version.txt for unified version management
$VersionFile = Join-Path $Root "version.txt"
if (Test-Path $VersionFile) {
    $Version = Get-Content $VersionFile -Raw -Encoding UTF8
    $Version = $Version.Trim()
} else {
    $Version = "v3.9-beta-ef"
}

$PackageRoot = Join-Path $Root "dist\packages"
$MainStage = Join-Path $PackageRoot "OpenFrequency_$Version"
$ModelPackageRoot = Join-Path $PackageRoot "OpenFrequency_$Version-models"
$ModelStage = Join-Path $ModelPackageRoot "OpenFrequency\_internal\models"
$MainZip = Join-Path $PackageRoot "OpenFrequency_$Version-main.zip"
$ModelZip = Join-Path $PackageRoot "OpenFrequency_$Version-models.zip"
$SherpaSource = Join-Path $Root "models\sherpa-onnx-whisper-small"
$SherpaTarget = Join-Path $ModelStage "sherpa-onnx-whisper-small"
$RequiredModelFiles = @(
    "small-tokens.txt",
    "small-encoder.int8.onnx",
    "small-decoder.int8.onnx"
)

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $PackageRoot
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
Copy-Item -Recurse -Force (Join-Path $Root "dist\OpenFrequency") $MainStage
New-Item -ItemType Directory -Force -Path $SherpaTarget | Out-Null
foreach ($FileName in $RequiredModelFiles) {
    $SourceFile = Join-Path $SherpaSource $FileName
    if (!(Test-Path $SourceFile)) {
        throw "Required model file missing: $SourceFile"
    }
    Copy-Item -Force $SourceFile $SherpaTarget
}

New-ZipFromDirectory -SourceDir $MainStage -ZipPath $MainZip
New-ZipFromDirectory -SourceDir $ModelPackageRoot -ZipPath $ModelZip

Write-Host "Build complete. Run only these final outputs:"
Write-Host "  dist\OpenFrequency\OpenFrequency.exe"
Write-Host "  dist\OpenFrequency\OpenFrequency-Console.exe"
Write-Host "Split release packages:"
Write-Host "  dist\packages\OpenFrequency_$Version-main.zip"
Write-Host "  dist\packages\OpenFrequency_$Version-models.zip"
Write-Host "Do not run executables from build\openfrequency; that folder is PyInstaller intermediate output."
Write-Host "Runtime config/logs are stored under %APPDATA%\OpenFrequency"
