@echo off
REM ===  Restart the self-hosted Remote Desktop (after a code update)  ===
REM Double-click this: it kills the host, signaling server and tunnel, then
REM relaunches start-selfhosted.bat. Your client auto-reconnects by room code.

echo Stopping host, signaling server and tunnel...
taskkill /F /IM cloudflared.exe >nul 2>&1
taskkill /F /IM python.exe      >nul 2>&1
taskkill /F /IM node.exe        >nul 2>&1
timeout /t 2 >nul

echo Relaunching...
start "" "%~dp0start-selfhosted.bat"
exit
