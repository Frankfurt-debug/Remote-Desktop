@echo off
REM ===  Remote Desktop - SELF-HOSTED (Cloudflare tunnel + room code)  ===
REM Runs the signaling server + host on THIS PC. The host spawns the Cloudflare
REM tunnel, and publishes its URL under a ROOM CODE so viewers just type the code.

set "PY=C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "SIGNALING_URL=ws://localhost:8080"
set "LOCAL_PORT=8080"
set "TUNNEL=1"

REM >>> Pick a memorable, fairly unique code viewers will type <<<
set "ROOM=frankfurt-pc"

REM After installing VB-CABLE (vb-audio.com), uncomment the next line for clean
REM mic with no echo/leak. Then set the game's mic to "CABLE Output".
REM set "MIC_OUT_DEVICE=CABLE Input"

echo Starting signaling server on localhost:8080 ...
start "RD Signaling" /d "%~dp0..\signaling-server" cmd /k node server.js
timeout /t 2 >nul

echo.
echo ============================================================
echo   Your ROOM CODE is:   %ROOM%
echo   In the viewer's Signaling box, just type:  %ROOM%
echo   (Change ROOM above to something unique to you.)
echo ============================================================
echo.
"%PY%" "%~dp0host.py"
pause
