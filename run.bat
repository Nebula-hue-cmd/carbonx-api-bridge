@echo off
setlocal
title CarbonX API Bridge
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python was not found on this computer.
    echo.
    echo The CarbonX Bridge needs Python 3.10 or newer.
    echo Close this window, install Python from https://python.org
    echo and tick "Add python.exe to PATH", then run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting CarbonX API Bridge...
echo Closing this window stops the bridge.
echo.
python -m carbonx_bridge
echo.
echo The bridge stopped. Press any key to close.
pause >nul