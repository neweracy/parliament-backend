/**
 * Cache utility layer for Redis operations.
 *
 * Provides get/set/del/invalidate helpers with namespace-prefixed keys,
 * JSON serialization, TTL support, and deterministic key generation.
 * All operations gracefully degrade when the Redis client is null or disconnected.
 *
 * @module lib/cache
 */

"use strict";

const crypto = require("crypto");

/**
 * @typedef {Object} CacheUtil
 * @property {function(string, ...string[]): string} key - Build namespaced key
 * @property {function(string): Promise<any|null>} get - Get and deserialize
 * @property {function(string, any, number): Promise<boolean>} set - Serialize and store with TTL
 * @property {function(string): Promise<boolean>} del - Delete single key
 * @property {function(string): Promise<number>} invalidatePattern - Delete keys by pattern
 * @property {function(Object): string} hashParams - Deterministic hash of query params
 */

/**
 * Creates a cache utility instance bound to the Redis client.
 * @param {import('ioredis') | null} client - Redis client or null (disabled)
 * @returns {CacheUtil}
 */
function createCache(client) {
  /**
   * Build a namespaced cache key.
   * Format: parliament:<namespace>:<parts joined by ':'>
   * @param {string} namespace
   * @param {...string} parts
   * @returns {string}
   */
  function key(namespace, ...parts) {
    return `parliament:${namespace}:${parts.join(":")}`;
  }

  /**
   * Check whether the client is usable (non-null and in a ready state).
   * @returns {boolean}
   */
  function isReady() {
    if (!client) return false;
    return client.status === "ready";
  }

  /**
   * Get and deserialize a cached value.
   * Returns null on miss, parse error, or when client is unavailable.
   * Corrupted keys (invalid JSON) are deleted asynchronously.
   * @param {string} cacheKey
   * @returns {Promise<any|null>}
   */
  async function get(cacheKey) {
    if (!isReady()) return null;

    try {
      const raw = await client.get(cacheKey);
      if (raw === null) return null;

      try {
        return JSON.parse(raw);
      } catch {
        // Corrupted data — delete the key asynchronously
        client.del(cacheKey).catch(() => {});
        return null;
      }
    } catch {
      return null;
    }
  }

  /**
   * Serialize and store a value with TTL.
   * @param {string} cacheKey
   * @param {any} value - Must be JSON-serializable
   * @param {number} ttlSeconds - Time-to-live in seconds
   * @returns {Promise<boolean>} true if stored successfully
   */
  async function set(cacheKey, value, ttlSeconds) {
    if (!isReady()) return false;

    try {
      const serialized = JSON.stringify(value);
      await client.set(cacheKey, serialized, "EX", ttlSeconds);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Delete a single cache key.
   * @param {string} cacheKey
   * @returns {Promise<boolean>} true if deleted successfully
   */
  async function del(cacheKey) {
    if (!isReady()) return false;

    try {
      await client.del(cacheKey);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Delete all keys matching a pattern using KEYS + pipeline DEL.
   * Safe for low-cardinality patterns only.
   * @param {string} pattern - Glob-style pattern (e.g., "parliament:search:*")
   * @returns {Promise<number>} Number of keys deleted
   */
  async function invalidatePattern(pattern) {
    if (!isReady()) return 0;

    try {
      const keys = await client.keys(pattern);
      if (keys.length === 0) return 0;

      const pipeline = client.pipeline();
      for (const k of keys) {
        pipeline.del(k);
      }
      await pipeline.exec();
      return keys.length;
    } catch {
      return 0;
    }
  }

  /**
   * Generate a deterministic hash from query parameters.
   * 1. Remove undefined/null values
   * 2. Sort keys alphabetically
   * 3. JSON-stringify the sorted entries
   * 4. SHA-256 hash, take first 16 hex characters
   * @param {Object} obj - Query parameters object
   * @returns {string} 16-character hex hash
   */
  function hashParams(obj) {
    const filtered = {};
    const sortedKeys = Object.keys(obj).sort();

    for (const k of sortedKeys) {
      if (obj[k] !== null && obj[k] !== undefined) {
        filtered[k] = obj[k];
      }
    }

    const serialized = JSON.stringify(
      Object.entries(filtered).sort(([a], [b]) => a.localeCompare(b))
    );
    const hash = crypto.createHash("sha256").update(serialized).digest("hex");
    return hash.substring(0, 16);
  }

  return {
    key,
    get,
    set,
    del,
    invalidatePattern,
    hashParams,
  };
}

module.exports = { createCache };
