'use strict';

/**
 * Unit tests for lib/cache.js — createCache utility layer.
 *
 * All tests use a mock Redis (in-memory Map) — no running Redis instance required.
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.6, 2.7, 9.4
 *
 * @module test/lib/cache
 */

const { describe, it, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { createCache } = require('../../lib/cache');

/**
 * Create a mock Redis client backed by an in-memory Map.
 * Mimics the ioredis interface used by cache.js.
 */
function createMockRedis() {
  const store = new Map();
  return {
    get: async (key) => store.get(key) ?? null,
    set: async (key, val, ..._args) => { store.set(key, val); return 'OK'; },
    del: async (key) => { store.delete(key); return 1; },
    keys: async (pattern) => {
      // Simple glob match: only handles trailing '*'
      const prefix = pattern.replace(/\*$/, '');
      return [...store.keys()].filter(k => k.startsWith(prefix));
    },
    pipeline: () => {
      const ops = [];
      return {
        del: (key) => ops.push(key),
        exec: async () => {
          for (const key of ops) store.delete(key);
          return ops.map(() => [null, 1]);
        },
      };
    },
    quit: async () => {},
    status: 'ready',
    _store: store,
  };
}

// ---------------------------------------------------------------------------
// key() — Namespace key generation
// ---------------------------------------------------------------------------

describe('cache.key()', () => {
  it('produces parliament:<namespace>:<id> for single part', () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);
    const result = cache.key('search', 'abc');
    assert.equal(result, 'parliament:search:abc');
  });

  it('produces parliament:<namespace>:<id1>:<id2> for multiple parts', () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);
    const result = cache.key('jobs', 'abc', 'def');
    assert.equal(result, 'parliament:jobs:abc:def');
  });

  it('produces parliament:<namespace>: when no parts given', () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);
    const result = cache.key('test');
    assert.equal(result, 'parliament:test:');
  });
});

// ---------------------------------------------------------------------------
// get/set round-trip
// ---------------------------------------------------------------------------

describe('cache get/set round-trip', () => {
  let mockRedis;
  let cache;

  beforeEach(() => {
    mockRedis = createMockRedis();
    cache = createCache(mockRedis);
  });

  it('set then get returns the same value (object)', async () => {
    const key = 'parliament:test:roundtrip';
    const value = { foo: 'bar', count: 42, nested: { x: [1, 2, 3] } };

    const stored = await cache.set(key, value, 300);
    assert.equal(stored, true);

    const retrieved = await cache.get(key);
    assert.deepEqual(retrieved, value);
  });

  it('set then get returns the same value (array)', async () => {
    const key = 'parliament:test:array';
    const value = [1, 'two', { three: 3 }];

    await cache.set(key, value, 60);
    const retrieved = await cache.get(key);
    assert.deepEqual(retrieved, value);
  });

  it('set then get returns the same value (string)', async () => {
    const key = 'parliament:test:string';
    const value = 'hello world';

    await cache.set(key, value, 60);
    const retrieved = await cache.get(key);
    assert.equal(retrieved, value);
  });

  it('set then get returns the same value (number)', async () => {
    const key = 'parliament:test:number';
    const value = 3.14159;

    await cache.set(key, value, 60);
    const retrieved = await cache.get(key);
    assert.equal(retrieved, value);
  });

  it('set then get returns the same value (boolean)', async () => {
    const key = 'parliament:test:bool';

    await cache.set(key, true, 60);
    const retrieved = await cache.get(key);
    assert.equal(retrieved, true);
  });

  it('set then get returns the same value (null)', async () => {
    const key = 'parliament:test:null';

    await cache.set(key, null, 60);
    // null serialized is "null", which parses back to null
    const retrieved = await cache.get(key);
    assert.equal(retrieved, null);
  });

  it('get on non-existent key returns null', async () => {
    const result = await cache.get('parliament:missing:key');
    assert.equal(result, null);
  });
});

// ---------------------------------------------------------------------------
// hashParams — Deterministic key generation
// ---------------------------------------------------------------------------

describe('cache.hashParams()', () => {
  let cache;

  beforeEach(() => {
    const mockRedis = createMockRedis();
    cache = createCache(mockRedis);
  });

  it('produces same hash regardless of key insertion order', () => {
    const hash1 = cache.hashParams({ b: 2, a: 1 });
    const hash2 = cache.hashParams({ a: 1, b: 2 });
    assert.equal(hash1, hash2);
  });

  it('produces same hash for complex objects with shuffled keys', () => {
    const hash1 = cache.hashParams({ z: 'last', a: 'first', m: 'middle' });
    const hash2 = cache.hashParams({ a: 'first', z: 'last', m: 'middle' });
    const hash3 = cache.hashParams({ m: 'middle', z: 'last', a: 'first' });
    assert.equal(hash1, hash2);
    assert.equal(hash2, hash3);
  });

  it('filters out null values', () => {
    const hash1 = cache.hashParams({ a: 1, b: null });
    const hash2 = cache.hashParams({ a: 1 });
    assert.equal(hash1, hash2);
  });

  it('filters out undefined values', () => {
    const hash1 = cache.hashParams({ a: 1, b: undefined });
    const hash2 = cache.hashParams({ a: 1 });
    assert.equal(hash1, hash2);
  });

  it('filters out both null and undefined values', () => {
    const hash1 = cache.hashParams({ a: 1, b: null, c: undefined, d: 'keep' });
    const hash2 = cache.hashParams({ d: 'keep', a: 1 });
    assert.equal(hash1, hash2);
  });

  it('returns a 16-character hex string', () => {
    const hash = cache.hashParams({ query: 'test', limit: 10 });
    assert.equal(hash.length, 16);
    assert.match(hash, /^[0-9a-f]{16}$/);
  });

  it('produces different hashes for different values', () => {
    const hash1 = cache.hashParams({ query: 'foo' });
    const hash2 = cache.hashParams({ query: 'bar' });
    assert.notEqual(hash1, hash2);
  });
});

// ---------------------------------------------------------------------------
// Graceful degradation — null client
// ---------------------------------------------------------------------------

describe('cache graceful degradation (null client)', () => {
  let cache;

  beforeEach(() => {
    cache = createCache(null);
  });

  it('get returns null without throwing', async () => {
    const result = await cache.get('parliament:test:any');
    assert.equal(result, null);
  });

  it('set returns false without throwing', async () => {
    const result = await cache.set('parliament:test:any', { data: 1 }, 300);
    assert.equal(result, false);
  });

  it('del returns false without throwing', async () => {
    const result = await cache.del('parliament:test:any');
    assert.equal(result, false);
  });

  it('invalidatePattern returns 0 without throwing', async () => {
    const result = await cache.invalidatePattern('parliament:test:*');
    assert.equal(result, 0);
  });

  it('key() still works without a client', () => {
    const result = cache.key('search', 'abc');
    assert.equal(result, 'parliament:search:abc');
  });

  it('hashParams() still works without a client', () => {
    const hash = cache.hashParams({ a: 1, b: 2 });
    assert.equal(hash.length, 16);
    assert.match(hash, /^[0-9a-f]{16}$/);
  });
});

// ---------------------------------------------------------------------------
// Graceful degradation — disconnected client (status !== 'ready')
// ---------------------------------------------------------------------------

describe('cache graceful degradation (disconnected client)', () => {
  it('get returns null when client status is not ready', async () => {
    const mockRedis = createMockRedis();
    mockRedis.status = 'connecting';
    const cache = createCache(mockRedis);

    const result = await cache.get('parliament:test:any');
    assert.equal(result, null);
  });

  it('set returns false when client status is not ready', async () => {
    const mockRedis = createMockRedis();
    mockRedis.status = 'end';
    const cache = createCache(mockRedis);

    const result = await cache.set('parliament:test:any', 'val', 60);
    assert.equal(result, false);
  });
});

// ---------------------------------------------------------------------------
// Corrupted JSON handling
// ---------------------------------------------------------------------------

describe('cache corrupted JSON handling', () => {
  it('get returns null for corrupted JSON and triggers delete', async () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);

    // Directly inject corrupted data into the store
    mockRedis._store.set('parliament:test:corrupt', '{invalid json!!!');

    const result = await cache.get('parliament:test:corrupt');
    assert.equal(result, null);

    // Allow the async delete to fire (it's fire-and-forget with .catch)
    await new Promise(resolve => setTimeout(resolve, 10));

    // Verify the corrupted key was deleted
    const raw = await mockRedis.get('parliament:test:corrupt');
    assert.equal(raw, null);
  });

  it('get returns null for non-JSON string without throwing', async () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);

    mockRedis._store.set('parliament:test:bad', 'not json at all');

    const result = await cache.get('parliament:test:bad');
    assert.equal(result, null);
  });
});

// ---------------------------------------------------------------------------
// del() and invalidatePattern()
// ---------------------------------------------------------------------------

describe('cache.del()', () => {
  it('deletes an existing key and returns true', async () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);

    await cache.set('parliament:test:todel', 'value', 60);
    const deleted = await cache.del('parliament:test:todel');
    assert.equal(deleted, true);

    const result = await cache.get('parliament:test:todel');
    assert.equal(result, null);
  });
});

describe('cache.invalidatePattern()', () => {
  it('deletes all keys matching the pattern and returns count', async () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);

    await cache.set('parliament:search:a', 'val1', 60);
    await cache.set('parliament:search:b', 'val2', 60);
    await cache.set('parliament:other:c', 'val3', 60);

    const count = await cache.invalidatePattern('parliament:search:*');
    assert.equal(count, 2);

    // Verify they were deleted
    assert.equal(await cache.get('parliament:search:a'), null);
    assert.equal(await cache.get('parliament:search:b'), null);

    // Other namespace untouched
    assert.equal(await cache.get('parliament:other:c'), 'val3');
  });

  it('returns 0 when no keys match the pattern', async () => {
    const mockRedis = createMockRedis();
    const cache = createCache(mockRedis);

    const count = await cache.invalidatePattern('parliament:empty:*');
    assert.equal(count, 0);
  });
});
