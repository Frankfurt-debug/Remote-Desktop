# Browser Remote Desktop

Stream a Windows PC's screen to a browser tab on another network with full
keyboard/mouse control, including Pointer Lock for gaming. Parsec-lite.

```
remote-desktop/
├─ signaling-server/   Node.js WebSocket broker (offer/answer relay + directory)
├─ host-agent/         Python agent: screen capture + input injection + WebRTC
└─ viewer/             Single-file browser viewer (WebRTC video + input)
```

> **Read [SECURITY.md](SECURITY.md) before you run this or share it.** The host
> agent hands whoever connects real mouse and keyboard control of a real PC.

## How it works

```
 Browser viewer  <──WebSocket──>  Signaling server  <──WebSocket──>  Host agent
        │                                                                 │
        └──────────────── WebRTC (video + data channel) ─────────────────┘
                              (peer-to-peer once connected)
```

The signaling server only brokers the handshake. Once the WebRTC connection is
up, video (host → browser) and input (browser → host) flow peer-to-peer.

There are also two WebSocket streaming modes (GPU H.264 and JPEG) for networks
that block WebRTC. In those modes the frames travel **through the signaling
server**, so only run a server you control.

## Access control

One shared secret, `ACCESS_KEY`, gates everything:

* The host **refuses to start** without one at least 12 characters long.
* The room id the signaling server sees is `sha256(room + key)`, so the server
  never learns your room code and nobody can squat your room to collect
  keystrokes by pretending to be the host.
* The directory entry is `sha256(code + key)`, so guessing a room code does not
  resolve it to someone's tunnel URL.
* Before the host streams a frame or injects a keystroke it sends a random
  nonce and requires `HMAC-SHA256(key, nonce)` back. The key itself never
  crosses the wire, a captured answer can't be replayed, and five wrong answers
  drop the connection.

Send the key to the other person over a different channel than the room code,
and change it when they no longer need access.

## 1. Signaling server

Deploy on a VPS or free host (Render/Railway/Fly). It needs a public, ideally
`wss://` (TLS) URL — browsers block `ws://` from an `https://` page.

```bash
cd signaling-server
npm install
node server.js          # listens on $PORT or 8080
```

Or skip it entirely and use `start-selfhosted.bat`, which runs the broker on the
host PC behind a Cloudflare tunnel.

## 2. Host agent (the Windows PC you want to control)

Requires **Python 3.10+** from python.org, with "Add to PATH" ticked.

```bash
cd host-agent
pip install -r requirements.txt
copy config.example.bat config.bat     REM then edit it: ACCESS_KEY and ROOM
start-host.bat                         REM or start-selfhosted.bat
```

`config.bat` holds your secrets and is gitignored. To run `host.py` directly,
set the same variables in the environment:

| Variable | Meaning |
| --- | --- |
| `ACCESS_KEY` | **Required.** Shared secret the viewer must prove it knows. |
| `SIGNALING_URL` | Broker to connect to, e.g. `wss://your-server.example.com`. |
| `ROOM` | Room code the viewer types. |
| `MONITOR` | mss monitor index, default 1. |
| `FPS` | Capture frame rate, default 30. |
| `CAPTURE` | `mss` to force the fallback capture path instead of dxcam. |
| `DIRECTORY_URL` | Where to publish the tunnel URL. Empty means don't publish. |
| `TURN_URL` / `TURN_USER` / `TURN_PASS` | Your own TURN relay (see NAT note). |
| `MIC_OUT_DEVICE` | Output device the viewer's mic is played into. |

## 3. Browser viewer

Open `viewer/index.html` — host it anywhere static (the signaling server serves
it too). Fill in the host code, the access key, and if you are resolving a code,
the directory server. Then click **Connect**.

Everything except the access key can be preset in the URL:

```
index.html?server=wss://your-server.example.com&room=yourroom&directory=wss://your-server.example.com
```

The key is deliberately not accepted from the URL, and is never written to
localStorage — type it each session.

Click the video to capture the mouse (Pointer Lock); press **Esc** to release.

## Networking / NAT

ICE is **non-trickle**: both sides gather all candidates and embed them in the
SDP, so only offer/answer cross the signaling server.

Google STUN is configured by default and works for most home networks. If both
peers are behind **symmetric NAT** (some mobile/carrier networks), STUN fails
and you need a **TURN** relay. No relay credentials ship in this repo — bring
your own, e.g. a self-hosted [coturn](https://github.com/coturn/coturn):

* Host: set `TURN_URL` / `TURN_USER` / `TURN_PASS`.
* Viewer: put `urls|username|password` in the **TURN relay** box.

If neither works, the GPU H.264 and JPEG modes ride the WebSocket instead and
usually get through.

## Caveats

* **Windows display scaling:** input maps via normalised coordinates, so it's
  resolution-independent, but if absolute clicks land slightly off, set the
  display to 100% scaling.
* **Anti-cheat:** injected input (`SendInput`) may be flagged by kernel-level
  anti-cheat in some online games, and that can mean a ban. Fine for
  single-player and the desktop.
* **Audio:** with audio enabled the host streams its speaker output, and with
  mic enabled the viewer's microphone is played into the host's output device.
  Both are off by default.
