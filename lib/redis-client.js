/**
 * Redis connection singleton for the Gateway.
 *
 * Configured from REDIS_URL environment variable.
 * When REDIS_URL is not set, operates in disabled mode where all operations
 * return cache-miss results (null).
 *
 * @module lib/redis-client
 */

"use strict";

const Redis = require("ioredis");

/**
 * @typedef {'connected' | 'connecting' | 'disconnected' | 'disabled'} RedisState
 */

const REDIS_URL = process.env.REDIS_URL || "";

/** @type {import('ioredis') | null} */
let client = null;

if (!REDIS_URL) {
  console.warn(
    "[redis] REDIS_URL is not set. Redis caching is disabled — all cache " +
      "operations will return cache-miss results."
  );
} else {
  client = new Redis(REDIS_URL, {
    connectTimeout: 3000,
    commandTimeout: 1000,
    lazyConnect: false,
    retryStrategy(times) {
      // Exponential backoff: 100ms * 2^(attempt-1), capped at 5000ms
      const delay = Math.min(100 * Math.pow(2, times - 1), 5000);
      return delay;
    },
    maxRetriesPerRequest: null, // Unlimited retries (never reject commands due to reconnecting)
  });

  client.on("connect", () => {
    console.log("[redis] Connected to Redis");
  });

  client.on("error", (err) => {
    console.error("[redis] Connection error:", err.message);
  });

  client.on("close", () => {
    console.log("[redis] Connection closed");
  });

  client.on("reconnecting", (ms) => {
    console.log(`[redis] Reconnecting in ${ms}ms`);
  });
}

/**
 * Returns the shared ioredis client instance.
 * Returns null when REDIS_URL is not configured (disabled mode).
 * @returns {import('ioredis') | null}
 */
function getClient() {
  return client;
}

/**
 * Reports current connection health.
 * @returns {Promise<{ state: RedisState, latencyMs: number | null }>}
 */
async function healthCheck() {
  if (!client) {
    return { state: "disabled", latencyMs: null };
  }

  const status = client.status;

  if (status === "ready") {
    try {
      const start = Date.now();
      await client.ping();
      const latencyMs = Date.now() - start;
      return { state: "connected", latencyMs };
    } catch {
      return { state: "disconnected", latencyMs: null };
    }
  }

  if (status === "connecting" || status === "reconnecting") {
    return { state: "connecting", latencyMs: null };
  }

  return { state: "disconnected", latencyMs: null };
}

/**
 * Gracefully disconnects. Called on process shutdown.
 * @returns {Promise<void>}
 */
async function disconnect() {
  if (client) {
    await client.quit();
    console.log("[redis] Disconnected gracefully");
  }
}

module.exports = { getClient, healthCheck, disconnect };
