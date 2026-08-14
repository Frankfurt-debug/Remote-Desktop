# Remote Desktop

Control your Windows PC from any browser — phone, laptop, Chromebook, anything.
GPU-encoded video, mouse and keyboard, two-way audio. No account, no port
forwarding, no server to deploy.

**You run one file on your PC. It prints a username and four words. You type
those into a browser anywhere. That's it.**

```
   PC (host)                                  any browser
   ┌──────────────────────┐                   ┌──────────────────────┐
   │ screen  → NVENC H.264│ ── Cloudflare ──► │ WebCodecs → canvas   │
   │ speakers→ Opus       │     tunnel        │ speakers             │
   │ input   ← SendInput  │ ◄──────────────── │ mouse / keys / mic   │
   └──────────────────────┘                   └──────────────────────┘
```

---

## Setup (5 minutes, once)

**1. Install Python 3.10+** — [python.org](https://www.python.org/downloads/).
   Tick **“Add python.exe to PATH”** during setup.

**2. Install cloudflared** (this is what lets you connect from outside your
   home network, without touching your router):

   ```
   winget install Cloudflare.cloudflared
   ```

**3. Download this repo** (green **Code** button → **Download ZIP**) and unzip it.

**4. Open `start.bat` and set your username:**

   ```bat
   set "USERNAME_RD=admin"        <- change "admin" to something only you know
   ```

## Using it

**On the PC:** double-click **`start.bat`**. It prints your login:

```
============================================================
  REMOTE DESKTOP IS READY - use these to log in from the client
============================================================
   Username:  admin
   Password:  students adopted tests kits

   Or open directly: https://students-adopted-tests-kits.trycloudflare.com
   Same WiFi: http://192.168.1.9:8080
============================================================
```

**On the other device:** open that `https://…trycloudflare.com` link, type the
username and the four words, press **Connect**.

Leave the host window open. Closing it stops sharing.

> The four words change every time you restart the host — that is the point.
> They are the address *and* the password, so a fresh set means a fresh
> connection that nobody else has ever seen.

---

## How the login works

The Cloudflare tunnel hands out a random address like
`students-adopted-tests-kits.trycloudflare.com`. Those four words *are* the
password: the viewer turns `students adopted tests kits` back into the address
and connects. So:

- **The four words say where** — unguessable, and different every run.
- **The username says who** — checked by the host; a wrong one is rejected
  before anything is streamed.

Anyone who knows both can control the PC, so treat the pair like a password.
See [Security](#security) for the full picture.

---

## Options

Everything below is optional.

### Modes (pick in Settings)

| Mode | Use it when | Notes |
|---|---|---|
| **GPU H.264** *(default)* | almost always | NVIDIA NVENC encodes on the GPU, so your CPU stays free for games. Only sends what changed. Works on restrictive networks. |
| **WebRTC** | same WiFi, or you want the lowest possible latency | Peer-to-peer. ~6 ms on a LAN. Blocked on some managed/school networks. |
| **JPEG** | nothing else works | No GPU or WebCodecs needed. Heaviest option. |

### Gaming

- **Game input mode** — sends hardware scancodes instead of virtual keys, which
  is what games using DirectInput/raw input actually read. Turn this on if a
  game ignores your keyboard (Half-Life 2, most Source games).
- **Capture mouse on click** — pointer lock, for mouselook. Raw relative motion,
  so the camera keeps turning past the screen edge.
- **`\` (backslash) sends Escape** while the mouse is captured — the browser
  keeps the real Esc key for releasing pointer lock, so games can never see it.
- **Exclusive fullscreen cannot be captured** by anything (OBS included). Set the
  game to **Borderless / Windowed Fullscreen** and it works.

### Audio

- **Enable audio** — hear the PC. Opus, with a self-correcting buffer so it does
  not drift seconds behind like most remote desktops.
- **Enable microphone** — talk to the PC, so voice chat in games works. The host
  plays your voice out of its speakers, so set the game's microphone to
  **Stereo Mix**.
- For clean mic with **no echo and no game audio leaking in**, install
  [VB-CABLE](https://vb-audio.com/Cable/) (free, needs a reboot), then uncomment
  this line in `start.bat`:

  ```bat
  set "MIC_OUT_DEVICE=CABLE Input"
  ```

  and set the game's microphone to **CABLE Output**.

### Quality

**Auto-adjust quality** (on by default) watches the real round-trip time and, if
frames start queueing, drops the frame rate and then the resolution until it
clears — then puts them back. The stats overlay shows **Buildup**, which is how
far behind you are right now: green is fine, red means it is backing up.

### Settings in `start.bat`

| Variable | Default | Meaning |
|---|---|---|
| `USERNAME_RD` | `admin` | Your login name |
| `PORT` | `8080` | Local port to serve on |
| `TUNNEL` | `1` | `0` = same-WiFi only, no internet access |
| `MIC_OUT_DEVICE` | default speakers | Where incoming mic audio is played |
| `MONITOR` | `1` | Which monitor to capture |
| `CAPTURE` | `auto` | `mss` forces the old BitBlt capture |

---

## Security

Be realistic about what this is: **anyone with the username and the four words
gets full control of your PC.**

What protects you:

- The address is random and unguessable, and **changes every restart**.
- The username is a second secret, checked before any screen data is sent.
- The tunnel is HTTPS end-to-end; nothing is exposed on your router.
- The password is never saved to disk in the browser.

What does **not** protect you:

- There is no rate limiting, lockout, or 2FA.
- Anyone you send the four words to keeps access until you restart the host.

Sensible use: pick a username nobody would guess, don't paste the four words
anywhere public, and **close the host window when you're done** — that
immediately invalidates the address.

---

## Troubleshooting

**“Cannot reach host”** — the host isn't running, the four words are mistyped, or
this network blocks it. Re-check the host window. The words are case-insensitive
and you can type them with spaces or hyphens.

**“Wrong username or password”** — the four words changed. Restarting the host
always makes a new set; read the current ones from the host window.

**Black screen in a game** — it's in exclusive fullscreen. Switch it to
Borderless/Windowed Fullscreen.

**Keyboard does nothing in a game** — turn on **Game input mode**.

**It's laggy** — lower **Quality (resolution)** first, then frame rate. Watch
**Buildup** in the stats overlay; if it's red, the network is the limit, not the PC.

**No audio** — check **Enable audio** is ticked and that Windows is actually
playing sound out of the default output device.

**Use it on the same WiFi** — put the host's `IP:port` (shown as “Same WiFi” in
the host window) in the password box instead of the four words. Much faster.

---

## Requirements

- **Host:** Windows 10/11, Python 3.10+. NVIDIA GPU for GPU H.264 mode
  (it falls back to JPEG without one).
- **Client:** any modern browser. GPU H.264 mode needs WebCodecs — Chrome/Edge 94+.

## What's in here

```
start.bat            run this on the PC
host/host.py         capture, encode, input injection, the server itself
host/requirements.txt
viewer/index.html    the entire client, one file
```

No build step, no framework, no database, no cloud account.
