@echo off
REM ===  Remote Desktop - SELF-HOSTED (Cloudflare tunnel + room code)  ===
REM Runs the signaling server + host on THIS PC. The host spawns the Cloudflare
REM tunnel and prints the URL viewers connect to. If DIRECTORY_URL is set in
REM config.bat it also publishes that URL so viewers can find it by room code.
REM Settings live in config.bat — copy config.example.bat to create it.

cd /d "%~dp0"
call "%~dp0_common.bat" || exit /b 1

set "SIGNALING_URL=ws://localhost:8080"
set "LOCAL_PORT=8080"
set "TUNNEL=1"

echo Starting signaling server on localhost:8080 ...
start "RD Signaling" /d "%~dp0..\signaling-server" cmd /k node server.js
timeout /t 2 >nul

echo.
echo ============================================================
echo   Your ROOM CODE is:   %ROOM%
echo   The viewer types that in the "Host code" box, plus the
echo   access key you agreed on. Send the key separately.
echo ============================================================
echo.
%PY% "%~dp0host.py"
pause
