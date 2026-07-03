@echo off
REM ===  Remote Desktop - SELF-HOSTED (Cloudflare tunnel, no Render/Metered)  ===
REM Runs the signaling server + host agent on THIS PC, and exposes them through a
REM free Cloudflare tunnel. Copy the wss:// URL it prints into the viewer.

set "PY=C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "CF=C:\Program Files (x86)\cloudflared\cloudflared.exe"
set "SIGNALING_URL=ws://localhost:8080"

echo Starting signaling server on localhost:8080 ...
start "RD Signaling" /d "%~dp0..\signaling-server" cmd /k node server.js
timeout /t 2 >nul

echo Starting host agent (connecting to local signaling) ...
start "RD Host" /d "%~dp0" cmd /k ""%PY%" host.py"
timeout /t 3 >nul

echo.
echo ==================================================================
echo  Cloudflare tunnel is starting. In the output below, find a line:
echo.
echo      https://SOMETHING.trycloudflare.com
echo.
echo  In the viewer's "Signaling server" box, enter it as:
echo      wss://SOMETHING.trycloudflare.com
echo.
echo  (The URL changes every time you restart this. Keep this window open.)
echo ==================================================================
echo.
"%CF%" tunnel --url http://localhost:8080
