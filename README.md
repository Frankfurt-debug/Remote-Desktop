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

## Setup

The simplest way to run this: everything lives on the PC being controlled, and
the other person just opens a link. No server to deploy, no accounts.

You need to agree on one thing beforehand: an **access key**, a long shared
passphrase. Anyone who has it gets full mouse and keyboard control of the host
PC, so pick something long, and send it in a different message from the link.

### On the PC you want to control

Do this once:

1. Install **Python** from [python.org](https://www.python.org/downloads/). On
   the first screen, tick **"Add python.exe to PATH"** before clicking install.
2. Install **Node.js** from [nodejs.org](https://nodejs.org) (the LTS button).
3. Install **cloudflared**. Open Command Prompt and run:
   ```
   winget install --id Cloudflare.cloudflared
   ```
   If `winget` isn't available, download it from
   [Cloudflare's releases page](https://github.com/cloudflare/cloudflared/releases)
   and put `cloudflared.exe` somewhere on your PATH.
4. Download this repository (green **Code** button → **Download ZIP**) and
   unzip it somewhere permanent, like your Documents folder.
5. Open Command Prompt in the `host-agent` folder and run:
   ```
   pip install -r requirements.txt
   ```
6. In that same folder, copy `config.example.bat` to `config.bat`, open it in
   Notepad, and set two things:
   ```
   set "ACCESS_KEY=the-long-passphrase-you-both-agreed-on"
   set "ROOM=any-name-for-this-pc"
   ```
   Save it. `config.bat` is gitignored, so it never gets committed.

Then, every time you want to share the screen:

7. Double-click **`start-selfhosted.bat`**. Two windows open. Leave both open.
8. After a few seconds it prints a link:
   ```
   https://something-random-here.trycloudflare.com/?room=your-room-name
   ```
   Send that link to the other person. Send them the access key **separately**.
9. Closing the windows stops the sharing. The link changes every time you
   restart, so you send a new one each session.

### On the device connecting to it

1. Open the link in Chrome or Edge. On a phone or iPad it works too, but games
   need a real keyboard.
2. Type the **access key** into the Access key box.
3. Click **Connect**. The screen appears in a few seconds.
4. Click the screen to capture the mouse for games. Press **Esc** to let go and
   bring the settings panel back.

If it doesn't connect, open the panel, tick **Show connection log**, and try
again. The log names the step that failed. **Wrong access key** means the two
of you have different keys; **host not found** means the sharing PC isn't
running or its link has changed.

### Things worth knowing before you start

* The person with the access key can do anything you could do sitting at that
  PC. There is no restricted mode and no log of what they did.
* On the same WiFi, tick **Same WiFi as host** for a much faster connection.
* If the picture is choppy, lower **Frame rate** or **Quality (resolution)** in
  the panel. Both can be changed while connected.
* Injected keyboard and mouse input can trip anti-cheat in online games, and
  that can mean a ban. Fine for single-player and normal desktop use.
* Audio and microphone are off by default; tick them in the panel if you want
  them.

For a server you deploy once instead of a link that changes every session, see
the numbered sections below.

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

## Advanced: a permanent server

### 1. Signaling server

Deploy on a VPS or free host (Render/Railway/Fly). It needs a public, ideally
`wss://` (TLS) URL — browsers block `ws://` from an `https://` page.

```bash
cd signaling-server
npm install
node server.js          # listens on $PORT or 8080
```

Or skip it entirely and use `start-selfhosted.bat`, which runs the broker on the
host PC behind a Cloudflare tunnel — that is the [Setup](#setup) path above.

### 2. Host agent (the Windows PC you want to control)

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

### 3. Browser viewer

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
