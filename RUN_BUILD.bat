@echo off
setlocal
title TunnelPulse Build
cd /d "%~dp0"

echo Starting TunnelPulse build...
echo (If this window closes immediately after this line, PowerShell itself
echo  never got to run the script - check that Windows PowerShell is
echo  installed and reachable.)
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_tunnelpulse.ps1"

echo.
echo ============================================================
echo  Build script exited. Press any key to close this window.
echo ============================================================
pause >nul
