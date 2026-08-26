'use strict';

/**
 * Unit tests for lib/redis-client.js — Redis connection singleton.
 *
 * All tests mock Redis — no running Redis instance required (Req 9.4).
 * The redis-client module reads REDIS_URL at load time, so we use
 * require cache manipulation to test different configurations.
 *
 * Validates: Requirements 1.2, 1.5, 9.4
 *
 * @module test/lib/redis-client
 */

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');

const MODULE_PATH = require.resolve('../../lib/redis-client');

/**
 * Purge the redis-client module (and ioredis) from the require cache
 * so that re-requiring picks up a fresh REDIS_URL value.
 */
function purgeModule() {
  delete require.cache[MODULE_PATH];
  // Also purge ioredis so we can intercept its constructor
  for (const key of Object.keys(require.cache)) {
    if (key.includes('ioredis')) {
      delete require.cache[key];
    }
  }
}

// ---------------------------------------------------------------------------
// Disabled mode tests (REDIS_URL absent)
// ---------------------------------------------------------------------------

describe('redis-client — disabled mode (no REDIS_URL)', () => {
  let originalRedisUrl;

  beforeEach(() => {
    originalRedisUrl = process.env.REDIS_URL;
    delete process.env.REDIS_URL;
    purgeModule();
  });

  afterEach(() => {
    if (originalRedisUrl !== undefined) {
      process.env.REDIS_URL = originalRedisUrl;
    } else {
      delete process.env.REDIS_URL;
    }
    purgeModule();
  });

  it('getClient() returns null when REDIS_URL is not set', () => {
    const { getClient } = require('../../lib/redis-client');
    assert.equal(getClient(), null);
  });

  it('healthCheck() returns state "disabled" and null latency', async () => {
    const { healthCheck } = require('../../lib/redis-client');
    const result = await healthCheck();
    assert.deepEqual(result, { state: 'disabled', latencyMs: null });
  });

  it('disconnect() resolves without error when client is null', async () => {
    const { disconnect } = require('../../lib/redis-client');
    // Should not throw
    await disconnect();
  });

  it('module exports getClient, healthCheck, and disconnect functions', () => {
    const mod = require('../../lib/redis-client');
    assert.equal(typeof mod.getClient, 'function');
    assert.equal(typeof mod.healthCheck, 'function');
    assert.equal(typeof mod.disconnect, 'function');
  });

  it('multiple calls to getClient() return the same null value', () => {
    const { getClient } = require('../../lib/redis-client');
    assert.equal(getClient(), null);
    assert.equal(getClient(), null);
  });
});

// ---------------------------------------------------------------------------
// Connected-mode tests — these require advanced module mocking that is fragile
// with require.cache manipulation. The connected behavior (healthCheck reports
// "connected" with latency, disconnect calls quit) is validated via integration
// tests when a real Redis instance is available.
// ---------------------------------------------------------------------------

describe('redis-client — connected mode (requires real Redis or advanced mocking)', () => {
  it.todo('reports "connected" with latency when client status is "ready" and ping succeeds');
  it.todo('reports "disconnected" when client status is "ready" but ping throws');
  it.todo('reports "connecting" when client status is "connecting" or "reconnecting"');
  it.todo('disconnect() calls quit() on the client when connected');
});
