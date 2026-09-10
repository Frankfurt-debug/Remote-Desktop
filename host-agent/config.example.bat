@echo off
REM ===  Copy this file to config.bat and edit it. config.bat is gitignored.  ===

REM --- REQUIRED: the shared secret the viewer must type in to connect. ---
REM Anyone with this string gets full mouse and keyboard control of this PC, so
REM make it long, and send it to the other person over a different channel than
REM the room code (a message, not the same page). Change it if it ever leaks.
set "ACCESS_KEY=change-me-to-a-long-shared-passphrase"

REM --- Room code: what the viewer types in the "Host code" box. ---
REM It is never sent to the signaling server in the clear (the server only ever
REM sees a hash of code + ACCESS_KEY), but pick something unique anyway.
set "ROOM=change-me"

REM --- Optional: your own directory server, so a viewer can find this PC by
REM code even though the Cloudflare tunnel URL changes on every restart.
REM Leave empty to skip publishing and just hand over the printed wss:// URL.
set "DIRECTORY_URL="

REM --- Optional: your own TURN relay, for symmetric-NAT networks. ---
set "TURN_URL="
set "TURN_USER="
set "TURN_PASS="

REM --- Optional: Python. Leave unset to use whatever "py"/"python" is on PATH. ---
set "PY="

REM --- Optional: after installing VB-CABLE (vb-audio.com), set this for a clean
REM mic with no echo. Then point the game's mic at "CABLE Output".
REM set "MIC_OUT_DEVICE=CABLE Input"
