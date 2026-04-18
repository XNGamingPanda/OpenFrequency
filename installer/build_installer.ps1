#Requires -Version 5.1
<#
.SYNOPSIS
    Build the OpenFrequency MSI installer.

.DESCRIPTION
    1. Verifies prerequisites (Python, PyInstaller, WiX 4 dotnet tool).
    2. Runs PyInstaller to produce dist\OpenFrequency\.
    3. Runs `wix build` to produce dist\OpenFrequency-Setup.msi.

.EXAMPLE
    cd <repo root>
    .\installer\build_installer.ps1

.NOTES
    WiX 4 must be installed as a .NET global tool:
        dotnet tool install --global wix
    PyInstaller must be available in the active Python environment:
        pip install pyinstaller
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Paths ──────────────────────────────────────────────────────────────────────
$RepoRoot    = (Resolve-Path "$PSScriptRoot\..").Path
$SpecFile    = Join-Path $RepoRoot "openfrequency.spec"
$DistDir     = Join-Path $RepoRoot "dist\OpenFrequency"
$InstallerDir= Join-Path $RepoRoot "installer"
$WxsFile     = Join-Path $InstallerDir "OpenFrequency.wxs"
$LicenseRtf  = Join-Path $InstallerDir "LICENSE.rtf"
$VersionFile = Join-Path $RepoRoot "version.txt"

# ── Read version ───────────────────────────────────────────────────────────────
if (Test-Path $VersionFile) {
    $env:OF_VERSION = (Get-Content $VersionFile -Raw).Trim()
} else {
    $env:OF_VERSION = "1.0.0"
}
$OutputMsi = Join-Path $RepoRoot "dist\OpenFrequency-$($env:OF_VERSION)-Setup.msi"
Write-Host "  Version: $($env:OF_VERSION)"

# ── Ensure LICENSE.rtf exists (WiX UI requires it) ────────────────────────────
if (-not (Test-Path $LicenseRtf)) {
    $rtfContent = '{\rtf1\ansi OpenFrequency is provided as-is without warranty. See project repository for full license details.}'
    Set-Content -Path $LicenseRtf -Value $rtfContent -Encoding ASCII
    Write-Host "  Created placeholder LICENSE.rtf"
}

# ── Helpers ────────────────────────────────────────────────────────────────────
function Require-Command($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Error "Required command '$cmd' not found. $hint"
        exit 1
    }
}

function Section($msg) {
    Write-Host ""
    Write-Host "══════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "══════════════════════════════════════════" -ForegroundColor Cyan
}

# ── Prerequisite checks ────────────────────────────────────────────────────────
Section "Checking prerequisites"

Require-Command "python"      "Install Python 3.10+ and ensure it is on PATH."
Require-Command "pyinstaller" "Run: pip install pyinstaller"
Require-Command "wix"         "Run: dotnet tool install --global wix"

Write-Host "  python     : $(python --version)"
Write-Host "  pyinstaller: $(pyinstaller --version)"
Write-Host "  wix        : $(wix --version)"

# ── Step 1: PyInstaller ────────────────────────────────────────────────────────
Section "Running PyInstaller"

Push-Location $RepoRoot
try {
    if (-not (Test-Path $SpecFile)) {
        Write-Error "Spec file not found: $SpecFile"
        exit 1
    }

    Write-Host "Building from spec: $SpecFile"
    pyinstaller $SpecFile --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed."; exit 1 }
} finally {
    Pop-Location
}

if (-not (Test-Path $DistDir)) {
    Write-Error "PyInstaller output not found at: $DistDir"
    exit 1
}
Write-Host "  Output: $DistDir"

# ── Step 2: WiX build ─────────────────────────────────────────────────────────
Section "Running WiX build"

# Resolve the icon path — wix needs it relative to the wxs working directory
if (-not (Test-Path (Join-Path $DistDir "OpenFrequency.exe"))) {
    Write-Warning "OpenFrequency.exe not found in dist — icon extraction will fail."
    Write-Warning "Ensure your PyInstaller spec names the exe 'OpenFrequency'."
}

Write-Host "Building MSI from: $WxsFile"
Write-Host "  OF_VERSION = $($env:OF_VERSION)"
wix build $WxsFile -out $OutputMsi -d "OF_VERSION=$($env:OF_VERSION)"

if ($LASTEXITCODE -ne 0) { Write-Error "WiX build failed."; exit 1 }

# ── Done ───────────────────────────────────────────────────────────────────────
Section "Build complete"
Write-Host "  MSI: $OutputMsi" -ForegroundColor Green
$size = [math]::Round((Get-Item $OutputMsi).Length / 1MB, 1)
Write-Host "  Size: ${size} MB" -ForegroundColor Green
