@echo off
:: OpenFrequency Build Launcher
:: Double-click this file to start a build.
:: For advanced options, run build.ps1 directly in PowerShell.
::
:: Usage:
::   build.bat              -- standard build (EXE + ZIP)
::   build.bat msi          -- EXE + ZIP + MSI installer
::   build.bat fast         -- skip venv update, skip PyInstaller, just re-zip
::   build.bat msi fast     -- re-zip + re-package MSI

setlocal

set ARGS=
set BUILD_MSI=0
set SKIP_PYI=0

:parse
if "%~1"=="" goto run
if /I "%~1"=="msi"  set BUILD_MSI=1
if /I "%~1"=="fast" set SKIP_PYI=1
shift
goto parse

:run
set PS_ARGS=-ExecutionPolicy Bypass -NoProfile -File "%~dp0build.ps1"
if %BUILD_MSI%==1 set PS_ARGS=%PS_ARGS% -BuildMsi
if %SKIP_PYI%==1  set PS_ARGS=%PS_ARGS% -SkipVenv -SkipPyInstaller

echo.
echo  Starting OpenFrequency build...
echo  PowerShell args: %PS_ARGS%
echo.

powershell %PS_ARGS%

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  BUILD FAILED ^(exit code %ERRORLEVEL%^)
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo  Build finished. Press any key to close.
pause >nul
