"""
Host agent for the browser remote desktop.

Runs on the Windows PC you want to control. Captures the screen with `mss`,
streams it to the browser over a WebRTC video track (aiortc), and injects the
mouse/keyboard events the browser sends back over a data channel.

Flow:
  1. Connect to the signaling server, register as "host".
  2. When a viewer joins, build the peer connection: add the screen video track
     and an "input" data channel, create the offer, send it.
  3. Receive the answer, connection negotiates, video flows host -> browser and
     input flows browser -> host.

Config is via environment variables (see the constants below) or just edit them.

  pip install -r requirements.txt
  set SIGNALING_URL=wss://your-server.example.com
  python host.py

Notes:
  * ICE is non-trickle: aiortc gathers all candidates and embeds them in the
    SDP, and the browser does the same, so only offer/answer are exchanged.
  * STUN (Google) is enough for most home networks. If both peers are behind
    symmetric NAT you must supply a TURN server (set TURN_URL/TURN_USER/TURN_PASS).
"""

import asyncio
import ctypes
import fractions
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

import mss
import numpy as np
import pyautogui
import websockets
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.sdp import candidate_from_sdp
import av
from av import VideoFrame

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
SIGNALING_URL = os.environ.get("SIGNALING_URL", "ws://localhost:8080")
ROOM = os.environ.get("ROOM", "default")
MONITOR = int(os.environ.get("MONITOR", "1"))   # mss monitor index (1 = primary)
FPS = int(os.environ.get("FPS", "30"))
# Cap the WebRTC send bitrate. Over a bandwidth-limited relay an uncapped
# encoder overruns the link, frames queue, and latency grows unbounded (mouse
# lags ~1s). ~2.5 Mbps keeps it responsive; lower it if the relay is slow.
MAX_BITRATE_KBPS = int(os.environ.get("MAX_BITRATE_KBPS", "2500"))

TURN_URL = os.environ.get("TURN_URL")           # e.g. turn:turn.example.com:3478
TURN_USER = os.environ.get("TURN_USER")
TURN_PASS = os.environ.get("TURN_PASS")

# Make the process DPI-aware so screen capture, cursor position and pyautogui
# all agree on physical pixels (otherwise the drawn cursor and clicks drift on
# displays with scaling above 100%). Must run before reading the screen size.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

# pyautogui: no delay between calls, and don't abort when the cursor hits a corner.
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False

SCREEN_W, SCREEN_H = pyautogui.size()
VIDEO_CLOCK_RATE = 90000

# ----------------------------------------------------------------------------
# Raw relative mouse movement via Win32 SendInput.
# pyautogui.move() clamps at the screen edges, which breaks FPS look-around in
# pointer lock. SendInput with MOUSEEVENTF_MOVE feeds raw relative deltas that
# games read directly, so the camera keeps turning past the edge.
# ----------------------------------------------------------------------------
MOUSEEVENTF_MOVE = 0x0001
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
PUL = ctypes.POINTER(ctypes.c_ulong)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def send_mouse_move_rel(dx: int, dy: int) -> None:
    extra = ctypes.c_ulong(0)
    mi = _MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, ctypes.pointer(extra))
    inp = _INPUT(0, _INPUTUNION(mi=mi))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# Hardware scancodes (Set 1) keyed by browser KeyboardEvent.code. Games that use
# DirectInput / raw input read scancodes, not the virtual keys pyautogui sends —
# so this "game input" path is what makes movement work in HL2, etc.
# Value is (scancode, is_extended).
SCANCODES = {
    "Escape": (0x01, False), "Digit1": (0x02, False), "Digit2": (0x03, False),
    "Digit3": (0x04, False), "Digit4": (0x05, False), "Digit5": (0x06, False),
    "Digit6": (0x07, False), "Digit7": (0x08, False), "Digit8": (0x09, False),
    "Digit9": (0x0A, False), "Digit0": (0x0B, False), "Minus": (0x0C, False),
    "Equal": (0x0D, False), "Backspace": (0x0E, False), "Tab": (0x0F, False),
    "KeyQ": (0x10, False), "KeyW": (0x11, False), "KeyE": (0x12, False),
    "KeyR": (0x13, False), "KeyT": (0x14, False), "KeyY": (0x15, False),
    "KeyU": (0x16, False), "KeyI": (0x17, False), "KeyO": (0x18, False),
    "KeyP": (0x19, False), "BracketLeft": (0x1A, False), "BracketRight": (0x1B, False),
    "Enter": (0x1C, False), "ControlLeft": (0x1D, False), "KeyA": (0x1E, False),
    "KeyS": (0x1F, False), "KeyD": (0x20, False), "KeyF": (0x21, False),
    "KeyG": (0x22, False), "KeyH": (0x23, False), "KeyJ": (0x24, False),
    "KeyK": (0x25, False), "KeyL": (0x26, False), "Semicolon": (0x27, False),
    "Quote": (0x28, False), "Backquote": (0x29, False), "ShiftLeft": (0x2A, False),
    "Backslash": (0x2B, False), "KeyZ": (0x2C, False), "KeyX": (0x2D, False),
    "KeyC": (0x2E, False), "KeyV": (0x2F, False), "KeyB": (0x30, False),
    "KeyN": (0x31, False), "KeyM": (0x32, False), "Comma": (0x33, False),
    "Period": (0x34, False), "Slash": (0x35, False), "ShiftRight": (0x36, False),
    "AltLeft": (0x38, False), "Space": (0x39, False), "CapsLock": (0x3A, False),
    "F1": (0x3B, False), "F2": (0x3C, False), "F3": (0x3D, False), "F4": (0x3E, False),
    "F5": (0x3F, False), "F6": (0x40, False), "F7": (0x41, False), "F8": (0x42, False),
    "F9": (0x43, False), "F10": (0x44, False), "F11": (0x57, False), "F12": (0x58, False),
    # Extended keys (need the extended flag).
    "ControlRight": (0x1D, True), "AltRight": (0x38, True), "MetaLeft": (0x5B, True),
    "MetaRight": (0x5C, True), "ArrowUp": (0x48, True), "ArrowLeft": (0x4B, True),
    "ArrowRight": (0x4D, True), "ArrowDown": (0x50, True), "Home": (0x47, True),
    "End": (0x4F, True), "PageUp": (0x49, True), "PageDown": (0x51, True),
    "Insert": (0x52, True), "Delete": (0x53, True), "NumpadEnter": (0x1C, True),
}


def send_key_scancode(scancode: int, down: bool, extended: bool = False) -> None:
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    extra = ctypes.c_ulong(0)
    ki = _KEYBDINPUT(0, scancode, flags, 0, ctypes.pointer(extra))
    inp = _INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=ki))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# One wheel "notch" is WHEEL_DELTA (120). The browser reports ~100 px per notch,
# so scaling by ~1.2 makes a notch in the browser equal a real notch on the host.
def send_mouse_wheel(amount: int) -> None:
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(amount), 0)   # MOUSEEVENTF_WHEEL


# ----------------------------------------------------------------------------
# Cursor overlay
# mss captures the framebuffer, which does NOT include the mouse cursor (it's a
# hardware overlay). So we query the real OS cursor position and paint a simple
# arrow onto each frame, otherwise the remote viewer sees no pointer at all.
# ----------------------------------------------------------------------------
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor_pos():
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# A classic arrow shape. 'X' = black outline, '.' = white fill, ' ' = transparent.
# The hotspot (the actual click point) is the top-left tip at (0, 0).
_ARROW = [
    "X          ",
    "XX         ",
    "X.X        ",
    "X..X       ",
    "X...X      ",
    "X....X     ",
    "X.....X    ",
    "X......X   ",
    "X.......X  ",
    "X........X ",
    "X.....XXXXX",
    "X..X..X    ",
    "X.X X..X   ",
    "XX  X..X   ",
    "X    X..X  ",
    "     X..X  ",
    "      XX   ",
]
# Pre-split into black and white pixel offsets for a fast blit.
_CURSOR_BLACK = [(y, x) for y, row in enumerate(_ARROW) for x, c in enumerate(row) if c == "X"]
_CURSOR_WHITE = [(y, x) for y, row in enumerate(_ARROW) for x, c in enumerate(row) if c == "."]


def draw_cursor(arr, mon_left: int, mon_top: int) -> None:
    cx, cy = get_cursor_pos()
    rx, ry = cx - mon_left, cy - mon_top
    h, w, _ = arr.shape
    if not (0 <= rx < w and 0 <= ry < h):
        return
    for px, val in ((_CURSOR_BLACK, 0), (_CURSOR_WHITE, 255)):
        for dy, dx in px:
            y, x = ry + dy, rx + dx
            if 0 <= y < h and 0 <= x < w:
                arr[y, x, 0] = val
                arr[y, x, 1] = val
                arr[y, x, 2] = val


# ----------------------------------------------------------------------------
# Screen capture track
# ----------------------------------------------------------------------------
_thread_local = threading.local()


def _grab(monitor_index: int):
    # mss is not thread-safe across threads, so keep one instance per worker thread.
    if not hasattr(_thread_local, "sct"):
        _thread_local.sct = mss.mss()
    try:
        sct = _thread_local.sct
        mon = sct.monitors[monitor_index]
        shot = sct.grab(mon)
    except Exception:
        # A game switching display mode / resolution can invalidate the capture
        # handle. Re-create mss (which re-reads the monitor geometry) and retry
        # once so a single bad grab doesn't kill the stream.
        _thread_local.sct = mss.mss()
        sct = _thread_local.sct
        mon = sct.monitors[monitor_index]
        shot = sct.grab(mon)
    # shot.rgb is read-only; .copy() makes it writable so we can paint the cursor.
    arr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3).copy()
    return arr, mon["left"], mon["top"]


class ScreenTrack(VideoStreamTrack):
    """A WebRTC video track that yields frames grabbed from the screen.

    fps, scale and show_cursor are public and can be changed live (the viewer
    sends a {"type": "config", ...} message over the data channel)."""

    def __init__(self, monitor_index: int = 1, fps: int = 30):
        super().__init__()
        self._monitor = monitor_index
        self.fps = fps
        self.scale = 1.0          # 0.25 .. 1.0 — downscale for less bandwidth
        self.show_cursor = True
        self._start = None
        self._timestamp = 0
        self._last_arr = None     # last good frame, reused if a grab fails
        # One dedicated capture thread so a single mss instance is reused and
        # screen grabs never block the asyncio event loop.
        self._executor = ThreadPoolExecutor(max_workers=1)

    def stop(self):
        # Called by aiortc when the peer connection closes — shut the capture
        # thread down so reconnects don't leak threads/mss instances.
        super().stop()
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    async def recv(self) -> VideoFrame:
        loop = asyncio.get_event_loop()
        fps = max(1, min(60, int(self.fps)))

        # Pace frames to the target FPS.
        if self._start is None:
            self._start = time.time()
            self._timestamp = 0
        else:
            self._timestamp += int(VIDEO_CLOCK_RATE / fps)
            target = self._start + self._timestamp / VIDEO_CLOCK_RATE
            delay = target - time.time()
            if delay > 0:
                await asyncio.sleep(delay)

        try:
            arr, mon_left, mon_top = await loop.run_in_executor(self._executor, _grab, self._monitor)
            if self.show_cursor:
                draw_cursor(arr, mon_left, mon_top)
            self._last_arr = arr
        except Exception as e:
            # Never let a capture error kill the track (which freezes the stream
            # and drops the connection). Reuse the last good frame, or black.
            print("capture error (reusing last frame):", e)
            arr = self._last_arr
            if arr is None:
                arr = np.zeros((SCREEN_H, SCREEN_W, 3), dtype=np.uint8)

        frame = VideoFrame.from_ndarray(arr, format="rgb24")
        if self.scale < 0.999:
            h, w, _ = arr.shape
            nw = max(2, int(w * self.scale)) & ~1   # even dims for YUV420
            nh = max(2, int(h * self.scale)) & ~1
            frame = frame.reformat(width=nw, height=nh)

        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, VIDEO_CLOCK_RATE)
        return frame


# ----------------------------------------------------------------------------
# Input injection
# ----------------------------------------------------------------------------
BUTTONS = {0: "left", 1: "middle", 2: "right"}

# Map browser KeyboardEvent.code (physical key, layout independent — good for WASD)
# to pyautogui key names.
KEY_MAP = {
    "Space": "space", "Enter": "enter", "Escape": "esc", "Tab": "tab",
    "Backspace": "backspace", "Delete": "delete", "Insert": "insert",
    "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "ArrowLeft": "left", "ArrowRight": "right", "ArrowUp": "up", "ArrowDown": "down",
    "ShiftLeft": "shiftleft", "ShiftRight": "shiftright",
    "ControlLeft": "ctrlleft", "ControlRight": "ctrlright",
    "AltLeft": "altleft", "AltRight": "altright",
    "MetaLeft": "winleft", "MetaRight": "winright",
    "CapsLock": "capslock", "NumLock": "numlock",
    "Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]",
    "Semicolon": ";", "Quote": "'", "Backquote": "`", "Comma": ",",
    "Period": ".", "Slash": "/", "Backslash": "\\",
}
for _i in range(1, 13):
    KEY_MAP[f"F{_i}"] = f"f{_i}"


def code_to_key(code: str, key: str):
    if code in KEY_MAP:
        return KEY_MAP[code]
    if code.startswith("Key") and len(code) == 4:        # KeyA -> 'a'
        return code[3].lower()
    if code.startswith("Digit") and len(code) == 6:      # Digit1 -> '1'
        return code[5]
    if code.startswith("Numpad") and code[6:].isdigit(): # Numpad1 -> '1'
        return code[6:]
    # Fallback: a single printable character from event.key.
    if key and len(key) == 1:
        return key.lower()
    return None


# When True, keyboard input is sent as hardware scancodes (works in games that
# ignore pyautogui's virtual keys). Toggled by the viewer's "Game input" setting.
GAME_INPUT = False


def _send_key(code: str, key: str, down: bool, force_scancode: bool = False) -> None:
    if (GAME_INPUT or force_scancode) and code in SCANCODES:
        sc, ext = SCANCODES[code]
        send_key_scancode(sc, down, ext)
        return
    k = code_to_key(code, key)
    if k:
        (pyautogui.keyDown if down else pyautogui.keyUp)(k, _pause=False)


def handle_input(msg: dict) -> None:
    t = msg.get("type")
    try:
        if t == "mousemoverel":
            send_mouse_move_rel(int(msg["dx"]), int(msg["dy"]))
        elif t == "mousemove":
            # Normalised (0..1) absolute position -> logical screen coords.
            x = max(0, min(SCREEN_W - 1, int(msg["x"] * SCREEN_W)))
            y = max(0, min(SCREEN_H - 1, int(msg["y"] * SCREEN_H)))
            pyautogui.moveTo(x, y, _pause=False)
        elif t == "mousedown":
            pyautogui.mouseDown(button=BUTTONS.get(msg.get("button", 0), "left"), _pause=False)
        elif t == "mouseup":
            pyautogui.mouseUp(button=BUTTONS.get(msg.get("button", 0), "left"), _pause=False)
        elif t == "wheel":
            # Browser deltaY is positive when scrolling down; wheel is positive up.
            send_mouse_wheel(-int(round(msg["dy"] * 1.2)))
        elif t == "keydown":
            _send_key(msg.get("code", ""), msg.get("key", ""), True, msg.get("sc", False))
        elif t == "keyup":
            _send_key(msg.get("code", ""), msg.get("key", ""), False, msg.get("sc", False))
    except Exception as e:
        print("input error:", e)


# ----------------------------------------------------------------------------
# WebRTC + signaling
# ----------------------------------------------------------------------------
def build_ice_servers():
    servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
    if TURN_URL:
        # User-supplied TURN overrides the default.
        servers.append(RTCIceServer(urls=[TURN_URL], username=TURN_USER, credential=TURN_PASS))
    else:
        # Metered TURN relay so the host allocates a relay candidate the viewer
        # can reach. Use plain UDP only — the host is on an unfiltered network, and
        # aioice's TURN channel-binding is most reliable over UDP (the TCP/TLS
        # transports were throwing 401s on channel-bind and breaking the relay).
        servers.append(RTCIceServer(
            urls=["turn:global.relay.metered.ca:80"],
            username="36292581ea281c3ca146486f",
            credential="VOadz1IOJqzHUZRa",
        ))
    return servers


def cap_video_bitrate(sdp: str, kbps: int) -> str:
    """Add a bandwidth line to the video section. aiortc honours the bandwidth in
    the remote description as its encoder cap, which stops the send queue from
    growing (the cause of latency creeping up over a slow relay)."""
    out, in_video = [], False
    for line in sdp.replace("\r\n", "\n").split("\n"):
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        out.append(line)
        if in_video and line.startswith("c="):
            out.append(f"b=AS:{kbps}")
            in_video = False
    return "\r\n".join(out)


def h264_codec_string(annexb: bytes) -> str:
    """Extract the WebCodecs codec string (avc1.PPCCLL) from the SPS in an
    Annex-B H.264 keyframe, so the browser's decoder can be configured."""
    i, n = 0, len(annexb)
    while i < n - 5:
        if annexb[i] == 0 and annexb[i + 1] == 0 and annexb[i + 2] == 1:
            if (annexb[i + 3] & 0x1F) == 7:                 # SPS NAL
                p, c, l = annexb[i + 4], annexb[i + 5], annexb[i + 6]
                return "avc1.%02X%02X%02X" % (p, c, l)
            i += 3
        else:
            i += 1
    return "avc1.42E01F"


def make_nvenc(w: int, h: int, fps: int, bitrate: int):
    """Create a low-latency H.264 NVENC encoder for the given size/fps/bitrate."""
    enc = av.codec.CodecContext.create("h264_nvenc", "w")
    enc.width, enc.height = w, h
    enc.pix_fmt = "yuv420p"
    enc.framerate = fractions.Fraction(fps, 1)
    enc.time_base = fractions.Fraction(1, fps)
    enc.bit_rate = bitrate
    # WebSocket is reliable (TCP), so we don't need periodic keyframes — they're
    # big and cause recurring latency spikes. Use a huge GOP (only 1 keyframe at
    # start) and request more on-demand if the decoder ever needs to resync.
    enc.gop_size = 100000
    enc.options = {"preset": "p1", "tune": "ull", "zerolatency": "1", "delay": "0", "rc": "cbr"}
    return enc


async def wait_ice_gathering_complete(pc: RTCPeerConnection) -> None:
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.get_event_loop().create_future()

    @pc.on("icegatheringstatechange")
    def _on_change():
        if pc.iceGatheringState == "complete" and not done.done():
            done.set_result(None)

    await done


async def connect_once():
    global GAME_INPUT
    async with websockets.connect(SIGNALING_URL, max_size=None, open_timeout=90) as ws:
        await ws.send(json.dumps({"type": "register", "role": "host", "room": ROOM}))
        print(f"Registered as host. Screen {SCREEN_W}x{SCREEN_H}, monitor {MONITOR}, {FPS} fps.")

        pc = None

        # --- WebSocket video streaming (fallback for networks that block WebRTC) ---
        # Instead of WebRTC/DTLS, capture -> JPEG -> send as binary over the same
        # signaling socket the viewer already uses. Slower/heavier than WebRTC but
        # rides the one channel a strict filter reliably allows.
        stream_state = {"run": False, "fps": 12, "scale": 0.5, "quality": 55, "cursor": True, "codec": "jpeg"}
        stream_task = None

        async def nvenc_stream():
            # GPU (NVENC) H.264 over the WebSocket: hardware-encoded, only sends
            # what changed between frames. Each binary message = 1 byte keyframe
            # flag + the H.264 access unit. A "codec-info" JSON precedes the video.
            loop = asyncio.get_event_loop()
            print("NVENC H.264 stream: started")
            enc = None
            cur = (0, 0, 0)         # (w, h, fps) the encoder was built for
            need_codec_info = False
            pts = 0
            frames = 0
            while stream_state["run"]:
                t0 = time.time()
                try:
                    fps = max(1, min(60, int(stream_state["fps"])))
                    scale = float(stream_state["scale"])
                    w = max(2, int(SCREEN_W * scale)) & ~1
                    h = max(2, int(SCREEN_H * scale)) & ~1
                    if stream_state.get("force_key"):
                        cur = (0, 0, 0)         # rebuild encoder -> emits a keyframe
                        stream_state["force_key"] = False
                    if (w, h, fps) != cur:
                        if enc is not None:
                            for _p in enc.encode(None):
                                pass
                        # ~0.1 bit/pixel/frame is a reasonable H.264 target.
                        bitrate = max(800_000, min(12_000_000, int(w * h * fps * 0.1)))
                        enc = make_nvenc(w, h, fps, bitrate)
                        cur = (w, h, fps)
                        need_codec_info = True
                        print(f"NVENC: {w}x{h}@{fps} {bitrate // 1000}kbps")

                    arr, ml, mt = await loop.run_in_executor(None, _grab, MONITOR)
                    if stream_state["cursor"]:
                        draw_cursor(arr, ml, mt)
                    frame = VideoFrame.from_ndarray(arr, format="rgb24")
                    frame = frame.reformat(width=w, height=h, format="yuv420p")
                    frame.pts = pts
                    frame.time_base = fractions.Fraction(1, fps)
                    pts += 1
                    for pkt in enc.encode(frame):
                        data = bytes(pkt)
                        if need_codec_info and pkt.is_keyframe:
                            cs = h264_codec_string(data)
                            await ws.send(json.dumps({"type": "codec-info", "w": w, "h": h, "codec": cs}))
                            need_codec_info = False
                        await ws.send((b"\x01" if pkt.is_keyframe else b"\x00") + data)
                        frames += 1
                except Exception as e:
                    print("nvenc error:", e)
                    await asyncio.sleep(0.3)
                    cur = (0, 0, 0)   # rebuild encoder after an error
                interval = 1.0 / max(1, int(stream_state["fps"]))
                dt = time.time() - t0
                if dt < interval:
                    await asyncio.sleep(interval - dt)
            print(f"NVENC H.264 stream: stopped ({frames} packets)")

        async def ws_stream():
            loop = asyncio.get_event_loop()
            last = None
            print("WS video stream: started")
            frames = 0
            while stream_state["run"]:
                t0 = time.time()
                try:
                    arr, ml, mt = await loop.run_in_executor(None, _grab, MONITOR)
                    if stream_state["cursor"]:
                        draw_cursor(arr, ml, mt)
                    img = Image.fromarray(arr)
                    scale = float(stream_state["scale"])
                    if scale < 0.999:
                        img = img.resize((max(2, int(img.width * scale)),
                                          max(2, int(img.height * scale))))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=int(stream_state["quality"]))
                    data = buf.getvalue()
                    if data != last:            # skip unchanged frames to save bandwidth
                        await ws.send(data)
                        last = data
                        frames += 1
                except Exception as e:
                    print("stream error:", e)
                    await asyncio.sleep(0.3)
                interval = 1.0 / max(1, int(stream_state["fps"]))
                dt = time.time() - t0
                if dt < interval:
                    await asyncio.sleep(interval - dt)
            print(f"WS video stream: stopped ({frames} frames sent)")

        async def new_peer_connection():
            nonlocal pc
            # Give a WebSocket-mode viewer a moment to send "start-stream" first;
            # if it does, skip WebRTC entirely.
            await asyncio.sleep(0.4)
            if stream_state["run"]:
                return
            if pc is not None:
                await pc.close()
            pc = RTCPeerConnection(RTCConfiguration(iceServers=build_ice_servers()))
            track = ScreenTrack(MONITOR, FPS)
            pc.addTrack(track)

            channel = pc.createDataChannel("input")

            @channel.on("message")
            def on_message(data):
                try:
                    msg = json.loads(data)
                except Exception as e:
                    print("bad message:", e)
                    return
                # Viewer settings (FPS / quality / cursor) arrive as "config";
                # everything else is mouse/keyboard input.
                if msg.get("type") == "config":
                    global GAME_INPUT
                    if "fps" in msg:
                        track.fps = int(msg["fps"])
                    if "scale" in msg:
                        track.scale = float(msg["scale"])
                    if "cursor" in msg:
                        track.show_cursor = bool(msg["cursor"])
                    if "gameinput" in msg:
                        GAME_INPUT = bool(msg["gameinput"])
                    print(f"config: fps={track.fps} scale={track.scale} cursor={track.show_cursor} game={GAME_INPUT}")
                else:
                    handle_input(msg)

            @pc.on("connectionstatechange")
            async def on_state():
                print("connection state:", pc.connectionState)

            await pc.setLocalDescription(await pc.createOffer())
            await wait_ice_gathering_complete(pc)

            # Print which candidate types the host gathered. For a relay-only
            # viewer to connect, this MUST include "relay" — otherwise the host
            # can't allocate on the TURN server (and forced-relay will fail).
            cand_types = [
                line.split("typ ", 1)[1].split()[0]
                for line in pc.localDescription.sdp.splitlines()
                if "candidate:" in line and "typ " in line
            ]
            print("host ICE candidates:", cand_types or ["(none)"])
            if "relay" not in cand_types:
                print("  ⚠ NO relay candidate — TURN allocation failed; forced-relay won't connect.")

            await ws.send(json.dumps({
                "type": "offer",
                "sdp": pc.localDescription.sdp,
            }))
            print("offer sent")

        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "viewer-joined":
                print("viewer joined")
                # Run concurrently so a slow WebRTC ICE gather can't block the
                # loop from handling a WebSocket-mode viewer's start-stream.
                asyncio.create_task(new_peer_connection())

            elif mtype == "answer":
                if pc:
                    sdp = cap_video_bitrate(msg["sdp"], MAX_BITRATE_KBPS)
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=sdp, type="answer")
                    )
                    print(f"answer applied (bitrate cap {MAX_BITRATE_KBPS} kbps)")

            elif mtype == "ice":
                # Trickled ICE candidate from the viewer.
                cand_info = msg.get("candidate")
                if pc and cand_info and cand_info.get("candidate"):
                    try:
                        sdp = cand_info["candidate"]
                        if sdp.startswith("candidate:"):
                            sdp = sdp[len("candidate:"):]
                        cand = candidate_from_sdp(sdp)
                        cand.sdpMid = cand_info.get("sdpMid")
                        cand.sdpMLineIndex = cand_info.get("sdpMLineIndex")
                        await pc.addIceCandidate(cand)
                    except Exception as e:
                        print("ice add error:", e)

            elif mtype == "start-stream":
                # Viewer wants WebSocket video (no WebRTC). Drop any WebRTC peer
                # so we don't capture the screen twice.
                if pc:
                    await pc.close()
                    pc = None
                for k in ("fps", "scale", "quality", "cursor", "codec"):
                    if k in msg:
                        stream_state[k] = msg[k]
                if "gameinput" in msg:
                    GAME_INPUT = bool(msg["gameinput"])
                stream_state["run"] = True
                codec = stream_state.get("codec", "jpeg")
                print(f"start-stream: codec={codec} fps={stream_state['fps']} scale={stream_state['scale']}")
                if stream_task is None or stream_task.done():
                    stream_task = asyncio.create_task(nvenc_stream() if codec == "h264" else ws_stream())

            elif mtype == "stop-stream":
                stream_state["run"] = False

            elif mtype == "ping":
                # Latency probe: echo the viewer's timestamp straight back.
                await ws.send(json.dumps({"type": "pong", "t": msg.get("t")}))

            elif mtype == "request-keyframe":
                stream_state["force_key"] = True    # decoder needs to resync

            elif mtype in ("mousemove", "mousemoverel", "mousedown", "mouseup",
                           "wheel", "keydown", "keyup"):
                # Input in WebSocket-streaming mode arrives here (no data channel).
                handle_input(msg)

            elif mtype == "config":
                for k in ("fps", "scale", "quality", "cursor"):
                    if k in msg:
                        stream_state[k] = msg[k]
                if "gameinput" in msg:
                    GAME_INPUT = bool(msg["gameinput"])

            elif mtype == "leave":
                # Explicit "I'm leaving" from the viewer (button / page close).
                # Always tear down cleanly so the next connection starts fresh.
                print("viewer left (explicit)")
                stream_state["run"] = False
                if pc:
                    await pc.close()
                    pc = None

            elif mtype == "peer-left":
                # WS-video rides this socket, so it can't survive the viewer
                # leaving — stop streaming (it restarts when they reconnect).
                stream_state["run"] = False
                # For WebRTC, the media is independent of signaling, so a dropped
                # socket must NOT tear a connected stream down (flaky/filtered
                # networks would kill a working stream). Only clean up if it
                # never actually connected.
                if pc and pc.connectionState == "connected":
                    print("viewer signaling dropped — media still connected, keeping it alive")
                else:
                    print("viewer left")
                    if pc:
                        await pc.close()
                        pc = None


async def run():
    # Reconnect loop: Render's free tier sleeps when idle and returns 404 until
    # it wakes (~50s), so the first connection often fails. Keep retrying; this
    # also recovers automatically if the signaling server restarts.
    print(f"Connecting to {SIGNALING_URL} (room={ROOM}) ...")
    print("(If the signaling server is asleep, the first attempt can take up to a minute.)")
    while True:
        try:
            await connect_once()
            print("signaling connection closed; reconnecting in 3s...")
        except (OSError, websockets.exceptions.WebSocketException) as e:
            print(f"could not reach signaling server ({e}); retrying in 5s...")
            await asyncio.sleep(5)
            continue
        await asyncio.sleep(3)


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
