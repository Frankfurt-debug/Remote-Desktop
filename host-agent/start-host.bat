@echo off
REM ===  Remote Desktop - Host launcher (external signaling server)  ===
REM Double-click this file to start sharing this PC's screen.
REM Close the window (or press Ctrl+C) to stop.
REM Settings live in config.bat — copy config.example.bat to create it.

cd /d "%~dp0"
call "%~dp0_common.bat" || exit /b 1

if "%SIGNALING_URL%"=="" set "SIGNALING_URL=wss://your-own-server.example.com"

echo Starting remote desktop host...
echo Signaling: %SIGNALING_URL%
echo Room code: %ROOM%
echo (Leave this window open. Close it to stop sharing.)
echo.

%PY% host.py

echo.
echo Host stopped. Press any key to close.
pause >nul
