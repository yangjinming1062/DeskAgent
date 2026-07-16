@echo off
REM ============================================================================
REM Zast Agent Installer (CMD wrapper)
REM ============================================================================
REM Forwards to install.ps1 in the same directory. Use this when
REM you're on a Windows CMD shell and only have install.cmd handy; if you
REM can, just run install.ps1 directly from PowerShell.
REM
REM Usage:
REM   install.cmd
REM   install.cmd -Manifest
REM   install.cmd -Stage welcome -Json
REM
REM All arguments are passed through to install.ps1 untouched.
REM ============================================================================

setlocal
set SCRIPT_DIR=%~dp0

echo.
echo  Zast Agent Installer
echo  Launching PowerShell installer from %SCRIPT_DIR%install.ps1 ...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -File "%SCRIPT_DIR%install.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. See %%LOCALAPPDATA%%\zast\logs\ for details.
    echo.
    pause
    exit /b 1
)
endlocal
