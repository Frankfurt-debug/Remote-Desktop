@echo off
setlocal
title Remote Desktop - Host
cd /d "%~dp0"

REM ============================================================
REM   SETTINGS - change these if you like
REM ============================================================
set "USERNAME_RD=admin"
REM set "PORT=8080"
REM set "TUNNEL=0"                      REM 0 = same-WiFi only, no internet
REM set "MIC_OUT_DEVICE=CABLE Input"    REM after installing VB-CABLE (see README)
REM ============================================================

REM Find a working Python. Set PY yourself if you have several installed.
if not defined PY set "PY=python"
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  set "PY=py"
  py -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo.
    echo Python was not found. Install Python 3.10+ from python.org and tick
    echo "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
  )
)

if not exist ".installed" (
  echo Installing dependencies, this takes a minute the first time...
  "%PY%" -m pip install --disable-pip-version-check -q -r host\requirements.txt
  if errorlevel 1 (
    echo.
    echo Dependency install failed. Try:  %PY% -m pip install -r host\requirements.txt
    pause
    exit /b 1
  )
  echo done > .installed
)

where cloudflared >nul 2>&1
if errorlevel 1 if not exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" (
  echo.
  echo cloudflared is not installed - it is what makes this reachable from
  echo outside your home network. Install it with:
  echo.
  echo     winget install Cloudflare.cloudflared
  echo.
  echo Continuing in same-WiFi-only mode...
  set "TUNNEL=0"
  timeout /t 4 >nul
)

"%PY%" host\host.py
echo.
echo Host stopped.
pause
