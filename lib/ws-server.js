/**
 * WebSocket Server — real-time push notifications for DB changes.
 *
 * Broadcasts structured events to connected clients when sittings,
 * records, or transcripts are created, updated, or deleted.
 *
 * Event format (JSON):
 * {
 *   "type": "sitting:created" | "sitting:updated" | "record:created" | "record:updated" |
 *           "transcript:created" | "transcript:updated" | "ingestion:complete",
 *   "payload": { ... entity-specific data ... },
 *   "timestamp": "2026-08-12T15:30:00.000Z"
 * }
 *
 * Clients can subscribe to specific event types by sending:
 * { "action": "subscribe", "types": ["sitting:*", "record:*"] }
 *
 * By default, new connections receive ALL event types.
 *
 * @module lib/ws-server
 */

"use strict";

const { WebSocketServer } = require("ws");

/**
 * @typedef {Object} WSClient
 * @property {import('ws').WebSocket} ws - The WebSocket connection
 * @property {Set<string>|null} subscriptions - Event type filters (null = all)
 * @property {string} id - Unique client identifier
 */

/** @type {Set<WSClient>} */
const clients = new Set();

/** @type {WebSocketServer|null} */
let wss = null;

let clientIdCounter = 0;

/**
 * Initialise the WebSocket server on the given HTTP server.
 *
 * @param {import('http').Server} httpServer - The existing Express HTTP server
 * @param {Object} [options]
 * @param {number} [options.heartbeatIntervalMs=30000] - Ping interval for liveness
 * @returns {WebSocketServer}
 */
function initWebSocket(httpServer, options = {}) {
  const heartbeatMs = options.heartbeatIntervalMs || 30000;

  wss = new WebSocketServer({
    server: httpServer,
    path: "/ws",
  });

  wss.on("connection", (ws, req) => {
    const clientId = `ws_${++clientIdCounter}`;
    const client = { ws, subscriptions: null, id: clientId };
    clients.add(client);

    const ip = req.headers["x-forwarded-for"] || req.socket.remoteAddress;
    console.log(`[ws] Client connected: ${clientId} from ${ip} (total: ${clients.size})`);

    // Mark alive for heartbeat
    ws.isAlive = true;
    ws.on("pong", () => { ws.isAlive = true; });

    // Handle incoming messages (subscription filters)
    ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.action === "subscribe" && Array.isArray(msg.types)) {
          client.subscriptions = new Set(msg.types);
          ws.send(JSON.stringify({
            type: "system:subscribed",
            payload: { types: msg.types },
            timestamp: new Date().toISOString(),
          }));
        }
      } catch {
        // Ignore malformed messages
      }
    });

    ws.on("close", () => {
      clients.delete(client);
      console.log(`[ws] Client disconnected: ${clientId} (total: ${clients.size})`);
    });

    ws.on("error", (err) => {
      console.error(`[ws] Client error (${clientId}):`, err.message);
      clients.delete(client);
    });

    // Send welcome message
    ws.send(JSON.stringify({
      type: "system:connected",
      payload: { clientId, message: "Connected to Hansard live updates" },
      timestamp: new Date().toISOString(),
    }));
  });

  // Heartbeat — detect dead connections
  const heartbeat = setInterval(() => {
    for (const client of clients) {
      if (!client.ws.isAlive) {
        client.ws.terminate();
        clients.delete(client);
        continue;
      }
      client.ws.isAlive = false;
      client.ws.ping();
    }
  }, heartbeatMs);

  // Don't keep the process alive just for the heartbeat
  if (typeof heartbeat.unref === "function") heartbeat.unref();

  wss.on("close", () => {
    clearInterval(heartbeat);
  });

  console.log(`[ws] WebSocket server ready on /ws (heartbeat: ${heartbeatMs}ms)`);
  return wss;
}

/**
 * Check whether a client's subscription filter matches an event type.
 *
 * @param {Set<string>|null} subscriptions - Client's filter set
 * @param {string} eventType - The event type to check
 * @returns {boolean}
 */
function matchesSubscription(subscriptions, eventType) {
  if (subscriptions === null) return true; // No filter = receive all

  for (const pattern of subscriptions) {
    if (pattern === eventType) return true;
    // Wildcard support: "sitting:*" matches "sitting:created", "sitting:updated"
    if (pattern.endsWith(":*")) {
      const prefix = pattern.slice(0, -1); // "sitting:"
      if (eventType.startsWith(prefix)) return true;
    }
  }
  return false;
}

/**
 * Broadcast an event to all connected (and subscribed) clients.
 *
 * @param {string} type - Event type (e.g., "sitting:created")
 * @param {Object} payload - Event payload data
 */
function broadcast(type, payload) {
  if (clients.size === 0) return;

  const message = JSON.stringify({
    type,
    payload,
    timestamp: new Date().toISOString(),
  });

  let sent = 0;
  for (const client of clients) {
    if (client.ws.readyState === client.ws.OPEN && matchesSubscription(client.subscriptions, type)) {
      client.ws.send(message);
      sent++;
    }
  }

  if (sent > 0) {
    console.log(`[ws] Broadcast ${type} to ${sent}/${clients.size} clients`);
  }
}

/**
 * Get the current number of connected clients.
 * @returns {number}
 */
function getClientCount() {
  return clients.size;
}

module.exports = {
  initWebSocket,
  broadcast,
  getClientCount,
};
