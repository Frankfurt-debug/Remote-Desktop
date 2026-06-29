@echo off
REM ===  Remote Desktop - Host launcher  ===
REM Double-click this file to start sharing this PC's screen.
REM Close the window (or press Ctrl+C) to stop.

set "PY=C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "SIGNALING_URL=wss://remote-desktop-signaling.onrender.com"

cd /d "%~dp0"

echo Starting remote desktop host...
echo Signaling: %SIGNALING_URL%
echo (Leave this window open. Close it to stop sharing.)
echo.

"%PY%" host.py

echo.
echo Host stopped. Press any key to close.
pause >nul
