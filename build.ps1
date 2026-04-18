#Requires -Version 5.1
<#
.SYNOPSIS
    OpenFrequency one-click build script.

.DESCRIPTION
    Builds OpenFrequency from source in a clean virtual environment:
      1. Creates / reuses .venv_build
      2. Installs requirements.txt into the venv
      3. Runs PyInstaller → dist\OpenFrequency\
      4. Zips the output → dist\OpenFrequency-<version>.zip
      5. (Optional) Runs WiX to produce dist\OpenFrequency-<version>-Setup.msi

.PARAMETER SkipVenv
    Skip virtual-environment creation / update (faster when already set up).

.PARAMETER SkipPyInstaller
    Skip PyInstaller step (use existing dist\OpenFrequency\).

.PARAMETER BuildMsi
    Also run WiX 4 to produce an MSI installer after PyInstaller.

.PARAMETER Version
    Override the version string (default: reads version.txt).

.EXAMPLE
    # Full clean build
    .\build.ps1

    # Full build + MSI
    .\build.ps1 -BuildMsi

    # Re-zip only (PyInstaller already ran)
    .\build.ps1 -SkipVenv -SkipPyInstaller

.NOTES
    WiX 4 install:  dotnet tool install --global wix
#>

[CmdletBinding()]
param(
    [switch]$SkipVenv,
    [switch]$SkipPyInstaller,
    [switch]$BuildMsi,
    [string]$Version = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Paths ──────────────────────────────────────────────────────────────────────
$RepoRoot   = $PSScriptRoot
$VenvDir    = Join-Path $RepoRoot ".venv_build"
$SpecFile   = Join-Path $RepoRoot "openfrequency.spec"
$DistDir    = Join-Path $RepoRoot "dist\OpenFrequency"
$VersionFile= Join-Path $RepoRoot "version.txt"

# ── Helpers ────────────────────────────────────────────────────────────────────
function Step($msg) {
    Write-Host ""
    Write-Host ("=" * 55) -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host ("=" * 55) -ForegroundColor Cyan
}
function OK($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function INFO($msg) { Write-Host "  · $msg" -ForegroundColor Gray  }
function WARN($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function FAIL($msg) { Write-Host "  ✗ $msg" -ForegroundColor Red; exit 1 }

# ── Version ────────────────────────────────────────────────────────────────────
if (-not $Version) {
    if (Test-Path $VersionFile) {
        $Version = (Get-Content $VersionFile -Raw).Trim()
    } else {
        $Version = "dev"
    }
}
$env:OF_VERSION = $Version
$ZipPath = Join-Path $RepoRoot "dist\OpenFrequency-$Version.zip"
$MsiPath = Join-Path $RepoRoot "dist\OpenFrequency-$Version-Setup.msi"

Write-Host ""
Write-Host "  OpenFrequency Build Script" -ForegroundColor White
Write-Host "  Version : $Version"         -ForegroundColor White
Write-Host "  Root    : $RepoRoot"        -ForegroundColor White

# ── Step 1: Virtual environment ────────────────────────────────────────────────
if (-not $SkipVenv) {
    Step "Setting up build virtual environment"

    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $PythonExe) { FAIL "python not found on PATH." }
    INFO "Using Python: $PythonExe ($(python --version))"

    if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
        INFO "Creating .venv_build ..."
        python -m venv $VenvDir
        OK "Virtual environment created."
    } else {
        INFO ".venv_build already exists, updating packages."
    }

    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    $VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

    INFO "Installing / updating requirements.txt ..."
    & $VenvPip install --upgrade pip --quiet
    & $VenvPip install -r (Join-Path $RepoRoot "requirements.txt") --quiet
    & $VenvPip install pyinstaller --quiet
    OK "Dependencies ready."
} else {
    WARN "Skipping venv setup (-SkipVenv)."
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        FAIL ".venv_build not found. Run without -SkipVenv first."
    }
}

$VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"

# ── Step 2: PyInstaller ────────────────────────────────────────────────────────
if (-not $SkipPyInstaller) {
    Step "Running PyInstaller"

    if (-not (Test-Path $SpecFile)) { FAIL "Spec not found: $SpecFile" }

    Push-Location $RepoRoot
    try {
        & $VenvPyInstaller $SpecFile --noconfirm --clean
        if ($LASTEXITCODE -ne 0) { FAIL "PyInstaller failed (exit $LASTEXITCODE)." }
    } finally {
        Pop-Location
    }

    if (-not (Test-Path (Join-Path $DistDir "OpenFrequency.exe"))) {
        FAIL "OpenFrequency.exe not found after build."
    }
    OK "PyInstaller complete → $DistDir"
} else {
    WARN "Skipping PyInstaller (-SkipPyInstaller)."
    if (-not (Test-Path $DistDir)) { FAIL "dist\OpenFrequency not found. Run without -SkipPyInstaller first." }
}

# ── Step 3: Zip ────────────────────────────────────────────────────────────────
Step "Creating ZIP archive"

INFO "Compressing dist\OpenFrequency\ → $ZipPath ..."
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path "$DistDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal

$SizeMB  = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
$Sha256  = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()

OK "ZIP created: $([System.IO.Path]::GetFileName($ZipPath)) ($SizeMB MB)"
Write-Host ""
Write-Host "  SHA-256: $Sha256" -ForegroundColor Yellow
Write-Host "  (paste this into RELEASE_NOTES.md)" -ForegroundColor Gray

# Write sha256 to a sidecar file so CI / release scripts can read it easily
"$Sha256  OpenFrequency-$Version.zip" | Set-Content "$ZipPath.sha256" -Encoding ASCII

# ── Step 4: WiX MSI (optional) ────────────────────────────────────────────────
if ($BuildMsi) {
    Step "Building MSI with WiX 4"

    $WixExe = (Get-Command wix -ErrorAction SilentlyContinue)?.Source
    if (-not $WixExe) {
        WARN "WiX not found. Install with: dotnet tool install --global wix"
        WARN "Skipping MSI build."
    } else {
        INFO "wix: $(wix --version)"
        $WxsFile    = Join-Path $RepoRoot "installer\OpenFrequency.wxs"
        $LicenseRtf = Join-Path $RepoRoot "installer\LICENSE.rtf"

        # Auto-create placeholder LICENSE.rtf if missing
        if (-not (Test-Path $LicenseRtf)) {
            '{\rtf1\ansi OpenFrequency — see project repository for full license.}' |
                Set-Content $LicenseRtf -Encoding ASCII
            INFO "Created placeholder LICENSE.rtf"
        }

        Push-Location (Join-Path $RepoRoot "installer")
        try {
            wix build $WxsFile -out $MsiPath -d "OF_VERSION=$Version"
            if ($LASTEXITCODE -ne 0) { FAIL "WiX build failed." }
        } finally {
            Pop-Location
        }

        $MsiMB = [math]::Round((Get-Item $MsiPath).Length / 1MB, 1)
        OK "MSI created: $([System.IO.Path]::GetFileName($MsiPath)) ($MsiMB MB)"
    }
}

# ── Done ───────────────────────────────────────────────────────────────────────
Step "Build complete"
Write-Host ""
Write-Host "  Outputs:" -ForegroundColor White
Write-Host "    EXE dir : $DistDir"  -ForegroundColor Green
Write-Host "    ZIP     : $ZipPath"  -ForegroundColor Green
if ($BuildMsi -and (Test-Path $MsiPath)) {
    Write-Host "    MSI     : $MsiPath" -ForegroundColor Green
}
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Update RELEASE_NOTES.md with the SHA-256 above"
Write-Host "    2. git tag v$Version && git push --tags"
Write-Host "    3. gh release create v$Version dist\OpenFrequency-$Version.zip --prerelease"
Write-Host ""
