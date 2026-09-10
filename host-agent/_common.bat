@echo off
REM ===  Shared setup: load config.bat and locate Python.  ===
REM Sourced by start-host.bat and start-selfhosted.bat via "call".

if not exist "%~dp0config.bat" (
  echo.
  echo   No config.bat found.
  echo   Copy config.example.bat to config.bat and set ACCESS_KEY and ROOM in it.
  echo.
  pause
  exit /b 1
)
call "%~dp0config.bat"

if "%ACCESS_KEY%"=="" goto :nokey
if "%ACCESS_KEY%"=="change-me-to-a-long-shared-passphrase" goto :nokey

REM Find Python: whatever config.bat set, else the py launcher, else python.
if not "%PY%"=="" goto :havepy
where py >nul 2>&1 && (set "PY=py -3" & goto :havepy)
where python >nul 2>&1 && (set "PY=python" & goto :havepy)
echo.
echo   Python 3.10+ not found. Install it from python.org and tick "Add to PATH",
echo   or set PY in config.bat to the full path of python.exe.
echo.
pause
exit /b 1

:nokey
echo.
echo   ACCESS_KEY is not set in config.bat.
echo   Without it anyone who reaches this PC's server can control it, so the
echo   host refuses to start. Set a long passphrase and try again.
echo.
pause
exit /b 1

:havepy
exit /b 0
