#Requires -Version 5.1
<#
.SYNOPSIS
    Quick rebuild — skips PyInstaller, just patches dist\ and repackages.

.DESCRIPTION
    Use this after code-only changes (no new dependencies, no new bundled files).
    It copies changed Python/template/static files straight into the existing
    dist\OpenFrequency\ tree, then re-zips and optionally rebuilds the MSI.

    Typical time: ~2-5 min (vs ~15 min for a full build).

.PARAMETER BuildMsi
    Also rebuild the MSI installer (adds ~1 min).

.PARAMETER FullBuild
    Delegate to build.ps1 for a complete PyInstaller rebuild.

.EXAMPLE
    .\quick_build.ps1              # ZIP only
    .\quick_build.ps1 -BuildMsi   # ZIP + MSI
    .\quick_build.ps1 -FullBuild  # full rebuild
#>
[CmdletBinding()]
param(
    [switch]$BuildMsi,
    [switch]$FullBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$DistDir  = Join-Path $RepoRoot "dist\OpenFrequency"
$VersionFile = Join-Path $RepoRoot "version.txt"
$Version  = if (Test-Path $VersionFile) { (Get-Content $VersionFile -Raw).Trim() } else { "dev" }
$ZipPath  = Join-Path $RepoRoot "dist\OpenFrequency-$Version.zip"
$MsiPath  = Join-Path $RepoRoot "dist\OpenFrequency-$Version-Setup.msi"

function Step($msg) {
    Write-Host ""
    Write-Host ("=" * 55) -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host ("=" * 55) -ForegroundColor Cyan
}
function OK($msg)   { Write-Host "  v $msg" -ForegroundColor Green  }
function INFO($msg) { Write-Host "  . $msg" -ForegroundColor Gray   }
function WARN($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function FAIL($msg) { Write-Host "  x $msg" -ForegroundColor Red; exit 1 }

# ── Delegate to full build if requested ────────────────────────────────────────
if ($FullBuild) {
    $extra = if ($BuildMsi) { @("-BuildMsi") } else { @() }
    & (Join-Path $RepoRoot "build.ps1") @extra
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "  OpenFrequency Quick Build" -ForegroundColor White
Write-Host "  Version : $Version"        -ForegroundColor White
Write-Host "  Mode    : patch dist\ + repackage (no PyInstaller)" -ForegroundColor Gray

# ── Sanity check ───────────────────────────────────────────────────────────────
if (-not (Test-Path (Join-Path $DistDir "OpenFrequency.exe"))) {
    FAIL "dist\OpenFrequency\OpenFrequency.exe not found. Run a full build first:`n  .\build.ps1 -BuildMsi"
}

# ── Step 1: Patch Python / template / static files ────────────────────────────
Step "Patching dist\ with latest source files"

# Directories to sync into dist\OpenFrequency\_internal\
# Each entry: @{ Src = <repo relative>; Dst = <dist relative> }
$syncDirs = @(
    @{ Src = "core";              Dst = "_internal\core"              },
    @{ Src = "templates";         Dst = "_internal\templates"         },
    @{ Src = "static";            Dst = "_internal\static"            },
    @{ Src = "data\locales";      Dst = "_internal\data\locales"      },
    @{ Src = "data\cabin";        Dst = "_internal\data\cabin"        },
    @{ Src = "data\airport_data"; Dst = "_internal\data\airport_data" }
)

# Single files to copy
$syncFiles = @(
    @{ Src = "app.py";       Dst = "_internal\app.py"       },
    @{ Src = "version.txt";  Dst = "_internal\version.txt"  },
    @{ Src = "version.txt";  Dst = "version.txt"            }
)

$copied = 0

foreach ($item in $syncDirs) {
    $srcPath = Join-Path $RepoRoot $item.Src
    $dstPath = Join-Path $DistDir  $item.Dst
    if (-not (Test-Path $srcPath)) { WARN "Source not found, skipping: $($item.Src)"; continue }
    if (-not (Test-Path $dstPath)) { New-Item -ItemType Directory -Path $dstPath -Force | Out-Null }

    $files = Get-ChildItem $srcPath -Recurse -File
    foreach ($file in $files) {
        $rel     = $file.FullName.Substring($srcPath.Length).TrimStart('\','/')
        $dstFile = Join-Path $dstPath $rel
        $dstDir  = Split-Path $dstFile -Parent
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
        # Only copy if newer or destination missing
        $dstInfo = Get-Item $dstFile -ErrorAction SilentlyContinue
        if (-not $dstInfo -or $file.LastWriteTimeUtc -gt $dstInfo.LastWriteTimeUtc) {
            Copy-Item $file.FullName -Destination $dstFile -Force
            $copied++
        }
    }
}

foreach ($item in $syncFiles) {
    $srcPath = Join-Path $RepoRoot $item.Src
    $dstPath = Join-Path $DistDir  $item.Dst
    if (-not (Test-Path $srcPath)) { continue }
    $dstDir = Split-Path $dstPath -Parent
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
    $srcInfo = Get-Item $srcPath
    $dstInfo = Get-Item $dstPath -ErrorAction SilentlyContinue
    if (-not $dstInfo -or $srcInfo.LastWriteTimeUtc -gt $dstInfo.LastWriteTimeUtc) {
        Copy-Item $srcPath -Destination $dstPath -Force
        $copied++
    }
}

OK "Patched $copied file(s) into dist\"

# ── Step 2: ZIP ────────────────────────────────────────────────────────────────
Step "Creating ZIP archive"

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

$sevenZip = @('7z','7za') | ForEach-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $sevenZip) {
    $candidate = 'C:\Program Files\7-Zip\7z.exe'
    if (Test-Path $candidate) { $sevenZip = $candidate }
}

if ($sevenZip) {
    INFO "Using 7-Zip (level 9)..."
    $sevExe = if ($sevenZip -is [string]) { $sevenZip } else { $sevenZip.Source }
    & $sevExe a -tzip -mx=9 -mmt=on $ZipPath "$DistDir\*" | Out-Null
    if ($LASTEXITCODE -ne 0) { FAIL "7-Zip failed." }
} else {
    INFO "7-Zip not found, using Compress-Archive..."
    Compress-Archive -Path "$DistDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
}

$SizeMB = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
$Sha256 = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()
"$Sha256  OpenFrequency-$Version.zip" | Set-Content "$ZipPath.sha256" -Encoding ASCII
OK "ZIP: $([System.IO.Path]::GetFileName($ZipPath)) ($SizeMB MB)"
Write-Host "  SHA-256: $Sha256" -ForegroundColor Yellow

# ── Step 3: MSI (optional) ────────────────────────────────────────────────────
if ($BuildMsi) {
    Step "Building MSI with WiX"

    $DotnetTools = Join-Path $env:USERPROFILE ".dotnet\tools"
    if (Test-Path $DotnetTools) { $env:PATH = "$DotnetTools;$env:PATH" }

    if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
        WARN "WiX not found — skipping MSI. Install: dotnet tool install --global wix"
    } else {
        if (Test-Path $MsiPath) { Remove-Item $MsiPath -Force }
        $MsiVersion = ($Version -replace '[^0-9.]','') -replace '\.+$',''
        if (-not ($MsiVersion -match '^\d+\.\d+')) { $MsiVersion = "3.9.0" }
        Push-Location (Join-Path $RepoRoot "installer")
        try {
            wix build OpenFrequency.wxs -out $MsiPath -d "OF_VERSION=$MsiVersion"
            if ($LASTEXITCODE -ne 0) { FAIL "WiX build failed." }
        } finally {
            Pop-Location
        }
        $MsiMB = [math]::Round((Get-Item $MsiPath).Length / 1MB, 1)
        OK "MSI: $([System.IO.Path]::GetFileName($MsiPath)) ($MsiMB MB)"
    }
}

# ── Done ───────────────────────────────────────────────────────────────────────
Step "Done"
Write-Host ""
Write-Host "  ZIP : $ZipPath" -ForegroundColor Green
if ($BuildMsi -and (Test-Path $MsiPath)) {
    Write-Host "  MSI : $MsiPath" -ForegroundColor Green
}
Write-Host ""
