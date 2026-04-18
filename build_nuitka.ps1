param(
    [switch]$Clean,
    [string]$Jobs = "-2",
    [switch]$LowMemory
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    $Python = "python"
}
$NuitkaCmd = Join-Path $Root ".venv\Scripts\nuitka.cmd"
if (!(Test-Path $NuitkaCmd)) {
    $NuitkaCmd = "nuitka"
}

$Version = & $Python -c "from core.version import APP_VERSION; print(APP_VERSION)"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Version)) {
    throw "Failed to resolve APP_VERSION from core.version"
}
$NumericVersion = & $Python -c "import re; from core.version import APP_VERSION; text = APP_VERSION.removeprefix('v'); nums = re.findall(r'\d+', text); nums = (nums + ['0','0','0','0'])[:4]; print('.'.join(nums))"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($NumericVersion)) {
    throw "Failed to derive numeric version for Nuitka metadata"
}

$OutputRoot = Join-Path $Root "dist\nuitka"
$BuildRoot = Join-Path $Root "build\nuitka"
$NuitkaWorkRoot = Join-Path $Root "build\nk"
$ReleaseDir = Join-Path $OutputRoot "OpenFrequency-$Version"
$IconPath = Join-Path $Root "static\favicon.ico"
if (!(Test-Path $IconPath)) {
    $IconPath = Join-Path $Root "OpenFrequency-Icon.png"
}

Get-Process | Where-Object { $_.ProcessName -like "OpenFrequency*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $OutputRoot
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $BuildRoot
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $NuitkaWorkRoot
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Force -Path $NuitkaWorkRoot | Out-Null

& $Python -m pip install -r requirements-build.txt

function Add-IncludeDataDirIfExists {
    param(
        [System.Collections.Generic.List[string]]$Args,
        [string]$SourceRelative,
        [string]$TargetRelative
    )
    $sourcePath = Join-Path $Root $SourceRelative
    if (Test-Path $sourcePath) {
        $Args.Add("--include-data-dir=$SourceRelative=$TargetRelative")
    }
}

function Add-IncludeDataFileIfExists {
    param(
        [System.Collections.Generic.List[string]]$Args,
        [string]$SourceRelative,
        [string]$TargetRelative
    )
    $sourcePath = Join-Path $Root $SourceRelative
    if (Test-Path $sourcePath) {
        $Args.Add("--include-data-files=$SourceRelative=$TargetRelative")
    }
}

[System.Collections.Generic.List[string]]$nuitkaArgs = @(
    "--standalone",
    "--assume-yes-for-downloads",
    "--windows-console-mode=disable",
    "--output-dir=$NuitkaWorkRoot",
    "--remove-output",
    "--product-name=OpenFrequency",
    "--company-name=XNGamingPanda",
    "--file-version=$NumericVersion",
    "--product-version=$NumericVersion",
    "--jobs=$Jobs",
    "--include-module=app",
    "--include-package=core",
    "--nofollow-import-to=cv2",
    "--nofollow-import-to=mediapipe",
    "--output-filename=OpenFrequency.exe"
)

if ($LowMemory) {
    $nuitkaArgs.Add("--low-memory")
}

Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "templates" -TargetRelative "templates"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "static\css" -TargetRelative "static\css"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "static\js" -TargetRelative "static\js"
Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "static\favicon.ico" -TargetRelative "static\favicon.ico"
Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "OpenFrequency-Icon.png" -TargetRelative "OpenFrequency-Icon.png"

Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "data\airport_data" -TargetRelative "data\airport_data"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "data\cabin" -TargetRelative "data\cabin"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "data\career" -TargetRelative "data\career"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "data\locales" -TargetRelative "data\locales"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "data\navdata" -TargetRelative "data\navdata"
Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "data\phraseology" -TargetRelative "data\phraseology"
Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "data\chatter_templates.json" -TargetRelative "data\chatter_templates.json"

Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "ffmpeg\bin\ffmpeg.exe" -TargetRelative "ffmpeg\bin\ffmpeg.exe"
Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "ffmpeg\bin\ffprobe.exe" -TargetRelative "ffmpeg\bin\ffprobe.exe"
Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "ffmpeg\LICENSE" -TargetRelative "ffmpeg\LICENSE"
Add-IncludeDataFileIfExists -Args $nuitkaArgs -SourceRelative "ffmpeg\README.txt" -TargetRelative "ffmpeg\README.txt"

Add-IncludeDataDirIfExists -Args $nuitkaArgs -SourceRelative "plugins" -TargetRelative "plugins"

if (Test-Path $IconPath) {
    $nuitkaArgs += "--windows-icon-from-ico=$IconPath"
}

$nuitkaArgs += "launcher.py"

& $NuitkaCmd @nuitkaArgs

$DistDir = Join-Path $NuitkaWorkRoot "launcher.dist"
if (!(Test-Path $DistDir)) {
    $DistDir = Join-Path $NuitkaWorkRoot "OpenFrequency.dist"
}
if (!(Test-Path $DistDir)) {
    throw "Nuitka standalone output directory not found."
}

if (Test-Path $ReleaseDir) {
    Remove-Item -Recurse -Force $ReleaseDir
}
Copy-Item -Recurse -Force $DistDir $ReleaseDir

$BundledConfig = Join-Path $ReleaseDir "config.json"
if (Test-Path $BundledConfig) {
    throw "config.json was bundled unexpectedly: $BundledConfig"
}

$BundledModels = Join-Path $ReleaseDir "models"
if (Test-Path $BundledModels) {
    throw "models were bundled unexpectedly: $BundledModels"
}

$BundledCabinMedia = Join-Path $ReleaseDir "static\cabin_media"
if (Test-Path $BundledCabinMedia) {
    Remove-Item -Recurse -Force $BundledCabinMedia
}

$BundledFfplay = Join-Path $ReleaseDir "ffmpeg\bin\ffplay.exe"
if (Test-Path $BundledFfplay) {
    Remove-Item -Force $BundledFfplay
}

$BundledFfmpegDocs = Join-Path $ReleaseDir "ffmpeg\doc"
if (Test-Path $BundledFfmpegDocs) {
    Remove-Item -Recurse -Force $BundledFfmpegDocs
}

$BundledFfmpegPresets = Join-Path $ReleaseDir "ffmpeg\presets"
if (Test-Path $BundledFfmpegPresets) {
    Remove-Item -Recurse -Force $BundledFfmpegPresets
}

$OptionalPathsToPrune = @(
    (Join-Path $ReleaseDir "data\reports"),
    (Join-Path $ReleaseDir "data\storage"),
    (Join-Path $ReleaseDir "data\ground_cache"),
    (Join-Path $ReleaseDir "__pycache__")
)
foreach ($path in $OptionalPathsToPrune) {
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
    }
}

Write-Host "Nuitka standalone build complete:"
Write-Host "  $ReleaseDir"
Write-Host "Run:"
Write-Host "  $ReleaseDir\\OpenFrequency.exe"
