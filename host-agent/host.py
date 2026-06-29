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
import json
import math
import os
import threading
import time

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
from av import VideoFrame

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
SIGNALING_URL = os.environ.get("SIGNALING_URL", "ws://localhost:8080")
ROOM = os.environ.get("ROOM", "default")
MONITOR = int(os.environ.get("MONITOR", "1"))   # mss monitor index (1 = primary)
FPS = int(os.environ.get("FPS", "30"))

TURN_URL = os.environ.get("TURN_URL")           # e.g. turn:turn.example.com:3478
TURN_USER = os.environ.get("TURN_USER")
TURN_PASS = os.environ.get("TURN_PASS")

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


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def send_mouse_move_rel(dx: int, dy: int) -> None:
    extra = ctypes.c_ulong(0)
    mi = _MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, ctypes.pointer(extra))
    inp = _INPUT(0, _INPUTUNION(mi))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# ----------------------------------------------------------------------------
# Screen capture track
# ----------------------------------------------------------------------------
_thread_local = threading.local()


def _grab(monitor_index: int):
    # mss is not thread-safe across threads, so keep one instance per worker thread.
    if not hasattr(_thread_local, "sct"):
        _thread_local.sct = mss.mss()
    sct = _thread_local.sct
    mon = sct.monitors[monitor_index]
    shot = sct.grab(mon)
    # BGRA, shape (h, w, 4)
    return np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)


class ScreenTrack(VideoStreamTrack):
    """A WebRTC video track that yields frames grabbed from the screen."""

    def __init__(self, monitor_index: int = 1, fps: int = 30):
        super().__init__()
        self._monitor = monitor_index
        self._fps = fps
        self._start = None
        self._timestamp = 0
        # Single dedicated worker so screen grabs don't block the event loop.
        self._executor = None

    async def recv(self) -> VideoFrame:
        loop = asyncio.get_event_loop()

        # Pace frames to the target FPS.
        if self._start is None:
            self._start = time.time()
            self._timestamp = 0
        else:
            self._timestamp += int(VIDEO_CLOCK_RATE / self._fps)
            target = self._start + self._timestamp / VIDEO_CLOCK_RATE
            delay = target - time.time()
            if delay > 0:
                await asyncio.sleep(delay)

        arr = await loop.run_in_executor(None, _grab, self._monitor)
        frame = VideoFrame.from_ndarray(arr, format="rgb24")
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
            clicks = -int(math.copysign(max(1, abs(msg["dy"]) / 100), msg["dy"]))
            pyautogui.scroll(clicks, _pause=False)
        elif t == "keydown":
            k = code_to_key(msg.get("code", ""), msg.get("key", ""))
            if k:
                pyautogui.keyDown(k, _pause=False)
        elif t == "keyup":
            k = code_to_key(msg.get("code", ""), msg.get("key", ""))
            if k:
                pyautogui.keyUp(k, _pause=False)
    except Exception as e:
        print("input error:", e)


# ----------------------------------------------------------------------------
# WebRTC + signaling
# ----------------------------------------------------------------------------
def build_ice_servers():
    servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
    if TURN_URL:
        servers.append(RTCIceServer(urls=[TURN_URL], username=TURN_USER, credential=TURN_PASS))
    return servers


async def wait_ice_gathering_complete(pc: RTCPeerConnection) -> None:
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.get_event_loop().create_future()

    @pc.on("icegatheringstatechange")
    def _on_change():
        if pc.iceGatheringState == "complete" and not done.done():
            done.set_result(None)

    await done


async def run():
    print(f"Connecting to {SIGNALING_URL} (room={ROOM}) ...")
    async with websockets.connect(SIGNALING_URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "register", "role": "host", "room": ROOM}))
        print(f"Registered as host. Screen {SCREEN_W}x{SCREEN_H}, monitor {MONITOR}, {FPS} fps.")

        pc = None

        async def new_peer_connection():
            nonlocal pc
            if pc is not None:
                await pc.close()
            pc = RTCPeerConnection(RTCConfiguration(iceServers=build_ice_servers()))
            pc.addTrack(ScreenTrack(MONITOR, FPS))

            channel = pc.createDataChannel("input")

            @channel.on("message")
            def on_message(data):
                try:
                    handle_input(json.loads(data))
                except Exception as e:
                    print("bad input message:", e)

            @pc.on("connectionstatechange")
            async def on_state():
                print("connection state:", pc.connectionState)

            await pc.setLocalDescription(await pc.createOffer())
            await wait_ice_gathering_complete(pc)
            await ws.send(json.dumps({
                "type": "offer",
                "sdp": pc.localDescription.sdp,
            }))
            print("offer sent")

        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "viewer-joined":
                print("viewer joined -> creating offer")
                await new_peer_connection()

            elif mtype == "answer":
                if pc:
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=msg["sdp"], type="answer")
                    )
                    print("answer applied")

            elif mtype == "peer-left":
                print("viewer left")
                if pc:
                    await pc.close()
                    pc = None


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
