// Tiny WebRTC signaling server.
// Brokers the offer / answer / ICE handshake between a "host" (the Windows PC
// running the Python agent) and a "viewer" (a browser tab). Stateless beyond a
// small in-memory map of rooms; relay only, never inspects SDP.
//
// This server is deliberately dumb and trusts nobody. It does NOT authenticate
// anyone — the host does that itself, with a challenge the viewer can only
// answer using the shared access key. What the server sees is opaque: room ids
// and directory entries arrive already hashed with that key, so operating this
// server (or stumbling onto it) does not let you join a session, resolve a room
// code to someone's tunnel, or inject input.
//
// Note for anyone deploying it: in WebSocket streaming mode the video frames are
// relayed through this process, so whoever runs the server can see the streamed
// screen. Run your own; don't point at someone else's.
//
//   npm install
//   node server.js            (listens on $PORT or 8080)
//
// The WebSocket server is attached to a plain HTTP server so hosts like Render
// and Railway get a 200 on their health-check probe (a bare WS server only
// answers Upgrade requests and would be marked unhealthy and cycled).

const http = require("http");
const fs = require("fs");
const path = require("path");
const { WebSocketServer } = require("ws");

const PORT = process.env.PORT || 8080;
// Directory limits: entries expire, the map is capped so it can't be used to
// exhaust memory, and lookups are rate-limited so codes can't be enumerated.
const DIR_TTL_MS = Number(process.env.DIR_TTL_MS || 12 * 3600 * 1000);
const DIR_MAX_ENTRIES = Number(process.env.DIR_MAX_ENTRIES || 500);
const LOOKUPS_PER_MIN = Number(process.env.LOOKUPS_PER_MIN || 20);
const MAX_TEXT_BYTES = Number(process.env.MAX_TEXT_BYTES || 256 * 1024);

// The viewer is a sibling folder in the repo; serving it here means one URL
// does everything (open it in a browser; it also satisfies the health check).
const VIEWER = path.join(__dirname, "..", "viewer", "index.html");

const httpServer = http.createServer((req, res) => {
  fs.readFile(VIEWER, (err, data) => {
    if (err) {
      res.writeHead(200, { "Content-Type": "text/plain" });
      res.end("signaling server ok");
      return;
    }
    // Never cache the viewer, so a fresh visit always gets the newest version
    // (otherwise the browser serves a stale copy and you miss recent changes).
    res.writeHead(200, {
      "Content-Type": "text/html",
      "Cache-Control": "no-cache, no-store, must-revalidate",
      "Pragma": "no-cache",
      "Expires": "0",
    });
    res.end(data);
  });
});

const wss = new WebSocketServer({ server: httpServer });

// room id -> { host: ws|null, viewer: ws|null }
const rooms = new Map();
// Directory: an opaque entry id -> the host's current (changing) tunnel URL.
// Lets a viewer find the host by a memorable code even though the trycloudflare
// URL changes on every restart. The id is sha256(code + access key), computed by
// both ends, so this server never learns the code and a caller who guesses the
// code still cannot resolve the entry.
const directory = new Map();

function pruneDirectory() {
  const now = Date.now();
  for (const [k, v] of directory) if (now - v.ts > DIR_TTL_MS) directory.delete(k);
  // Still over the cap? Drop the oldest entries.
  if (directory.size > DIR_MAX_ENTRIES) {
    const oldest = [...directory.entries()].sort((a, b) => a[1].ts - b[1].ts);
    for (const [k] of oldest.slice(0, directory.size - DIR_MAX_ENTRIES)) directory.delete(k);
  }
}
setInterval(pruneDirectory, 60 * 1000).unref?.();

function send(ws, obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

wss.on("connection", (ws) => {
  ws.role = null;
  ws.room = null;
  ws.lookups = 0;
  // Reset the lookup budget once a minute for the life of the socket.
  const lookupReset = setInterval(() => { ws.lookups = 0; }, 60 * 1000);
  ws.on("close", () => clearInterval(lookupReset));

  ws.on("message", (data, isBinary) => {
    // Binary messages are video frames (WebSocket streaming mode) or other raw
    // payloads — relay them straight to the other peer without parsing.
    if (isBinary) {
      const room = rooms.get(ws.room);
      if (!room) return;
      const target = ws.role === "host" ? room.viewer : room.host;
      if (target && target.readyState === 1) target.send(data, { binary: true });
      return;
    }

    // Bound the work a single message can cause before parsing it.
    if (data.length > MAX_TEXT_BYTES) return;

    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (!msg || typeof msg !== "object") return;

    // Directory: host publishes its current tunnel URL under its opaque entry id.
    if (msg.type === "publish-url" && typeof msg.code === "string" && typeof msg.url === "string") {
      if (msg.code.length > 128 || msg.url.length > 512) return;
      pruneDirectory();
      if (directory.size >= DIR_MAX_ENTRIES && !directory.has(msg.code)) return;
      directory.set(msg.code.toLowerCase(), {
        url: msg.url,
        lan: typeof msg.lan === "string" ? msg.lan : null,
        ts: Date.now(),
      });
      // Log the entry id only — printing the URL would put a working way in to
      // someone's desktop in the server logs.
      console.log(`directory: entry ${msg.code.slice(0, 12)}… refreshed`);
      return;
    }
    // Viewer looks up an entry id and gets the current URL(s) back. Rate-limited,
    // because this is the one endpoint worth guessing at.
    if (msg.type === "lookup" && typeof msg.code === "string") {
      if (++ws.lookups > LOOKUPS_PER_MIN) return;
      const e = directory.get(msg.code.toLowerCase());
      const fresh = e && Date.now() - e.ts < DIR_TTL_MS;
      send(ws, { type: "url", code: msg.code, url: fresh ? e.url : null, lan: fresh ? e.lan : null });
      return;
    }

    // First message must register the peer's role + room.
    if (msg.type === "register") {
      const id = msg.room || "default";
      ws.room = id;
      ws.role = msg.role === "host" ? "host" : "viewer";

      const room = rooms.get(id) || { host: null, viewer: null };
      // Kick a stale peer of the same role if one is lingering.
      if (room[ws.role] && room[ws.role] !== ws) {
        send(room[ws.role], { type: "replaced" });
        try { room[ws.role].close(); } catch {}
      }
      room[ws.role] = ws;
      rooms.set(id, room);
      console.log(`[${String(id).slice(0, 12)}…] ${ws.role} registered`);

      // Let the host know a viewer is waiting so it can issue the access-key
      // challenge. This fires in both orderings (viewer first or host first) so
      // the handshake always starts exactly once.
      if (ws.role === "viewer" && room.host) send(room.host, { type: "viewer-joined" });
      if (ws.role === "host" && room.viewer) {
        send(room.viewer, { type: "host-ready" });
        send(ws, { type: "viewer-joined" });
      }
      return;
    }

    // Everything else (offer / answer / ice) is relayed to the other peer.
    const room = rooms.get(ws.room);
    if (!room) return;
    const target = ws.role === "host" ? room.viewer : room.host;
    send(target, msg);
  });

  ws.on("close", () => {
    const room = rooms.get(ws.room);
    if (!room) return;
    if (room[ws.role] === ws) room[ws.role] = null;
    const other = ws.role === "host" ? room.viewer : room.host;
    send(other, { type: "peer-left" });
    if (!room.host && !room.viewer) rooms.delete(ws.room);
    console.log(`[${String(ws.room).slice(0, 12)}…] ${ws.role} disconnected`);
  });
});

httpServer.listen(PORT, () => {
  console.log(`Signaling server listening on :${PORT} (HTTP health + WS)`);
});
