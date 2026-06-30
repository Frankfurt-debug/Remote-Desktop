// Tiny WebRTC signaling server.
// Brokers the offer / answer / ICE handshake between a "host" (the Windows PC
// running the Python agent) and a "viewer" (a browser tab). Stateless beyond a
// small in-memory map of rooms; relay only, never inspects SDP.
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

function send(ws, obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

wss.on("connection", (ws) => {
  ws.role = null;
  ws.room = null;

  ws.on("message", (data) => {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
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
      console.log(`[${id}] ${ws.role} registered`);

      // Let the host know a viewer is waiting so it can create the offer.
      if (ws.role === "viewer" && room.host) send(room.host, { type: "viewer-joined" });
      if (ws.role === "host" && room.viewer) send(room.viewer, { type: "host-ready" });
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
    console.log(`[${ws.room}] ${ws.role} disconnected`);
  });
});

httpServer.listen(PORT, () => {
  console.log(`Signaling server listening on :${PORT} (HTTP health + WS)`);
});
