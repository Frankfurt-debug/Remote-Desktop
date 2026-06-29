# Browser Remote Desktop

Stream a Windows PC's screen to a browser tab on another network with full
keyboard/mouse control, including Pointer Lock for gaming. Parsec-lite.

```
remote-desktop/
├─ signaling-server/   Node.js WebSocket broker (offer/answer relay)
├─ host-agent/         Python agent: screen capture + input injection + WebRTC
└─ viewer/             Single-file browser viewer (WebRTC video + input)
```

## How it works

```
 Browser viewer  <──WebSocket──>  Signaling server  <──WebSocket──>  Host agent
        │                                                                 │
        └──────────────── WebRTC (video + data channel) ─────────────────┘
                              (peer-to-peer once connected)
```

The signaling server only brokers the handshake. Once the WebRTC connection is
up, video (host → browser) and input (browser → host) flow peer-to-peer.

## 1. Signaling server

Deploy on a VPS or free host (Render/Railway/Fly). It needs a public, ideally
`wss://` (TLS) URL — browsers block `ws://` from an `https://` page.

```bash
cd signaling-server
npm install
node server.js          # listens on $PORT or 8080
```

## 2. Host agent (the Windows PC you want to control)

Requires **Python 3.10+** (not currently installed on this machine — grab it
from python.org and tick "Add to PATH").

```bash
cd host-agent
pip install -r requirements.txt
set SIGNALING_URL=wss://your-server.example.com
set ROOM=default
python host.py
```

Other env vars: `MONITOR` (mss monitor index, default 1), `FPS` (default 30),
and `TURN_URL` / `TURN_USER` / `TURN_PASS` (see NAT note below).

## 3. Browser viewer

Open `viewer/index.html` — host it anywhere static (the signaling server can
serve it too, GitHub Pages, etc.). Enter the signaling URL + room and click
**Connect**, or pass them in the URL:

```
index.html?server=wss://your-server.example.com&room=default&autoconnect=1
```

Click the video to capture the mouse (Pointer Lock); press **Esc** to release.

## Networking / NAT

ICE is **non-trickle**: both sides gather all candidates and embed them in the
SDP, so only offer/answer cross the signaling server.

Google STUN is configured by default and works for most home networks. If both
peers are behind **symmetric NAT** (some mobile/carrier networks), STUN fails
and you need a **TURN** relay:

* Host: set `TURN_URL` / `TURN_USER` / `TURN_PASS`.
* Viewer: add the matching entry to the `ICE` array in `index.html`.

A free option for testing is a self-hosted [coturn](https://github.com/coturn/coturn).

## Caveats

* **Windows display scaling:** input maps via normalised coordinates, so it's
  resolution-independent, but if absolute clicks land slightly off, set the
  display to 100% scaling.
* **Anti-cheat:** injected input (`SendInput`) may be flagged by kernel-level
  anti-cheat in some online games. Fine for single-player and the desktop.
* **Security:** there is no authentication. Anyone who knows the signaling URL
  and room name can connect. Use a hard-to-guess room name and put the signaling
  server behind TLS at minimum; add real auth before exposing this broadly.
