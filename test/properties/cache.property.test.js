'use strict';

/**
 * Property tests for the Cache utility module.
 *
 * Tests Properties 1–4 and 6 from the Redis Caching Layer design document.
 *
 * @module test/properties/cache.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { createCache } = require('../../lib/cache');

// ─── Mock Redis ──────────────────────────────────────────────────────────────

/**
 * Creates a mock Redis client backed by an in-memory Map.
 * Mimics the ioredis interface used by the cache utility.
 */
function createMockRedis() {
  const store = new Map();
  return {
    get: async (key) => store.get(key) ?? null,
    set: async (key, val, ..._args) => { store.set(key, val); return 'OK'; },
    del: async (key) => { store.delete(key); return 1; },
    keys: async (pattern) => {
      const prefix = pattern.replace(/\*$/, '');
      return [...store.keys()].filter(k => k.startsWith(prefix));
    },
    pipeline: () => {
      const ops = [];
      return { del: (key) => ops.push(key), exec: async () => ops.map(() => [null, 1]) };
    },
    quit: async () => {},
    status: 'ready',
    _store: store,
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * Property 1: Cache get/set round-trip
 *
 * For any JSON-serializable value, `set` then `get` returns deeply equal value.
 *
 * **Validates: Requirements 2.2, 2.3**
 */
describe('Property 1: Cache get/set round-trip', () => {
  it('any JSON-serializable value survives set→get round-trip', async () => {
    await fc.assert(
      fc.asyncProperty(fc.jsonValue(), async (value) => {
        const mockRedis = createMockRedis();
        const cache = createCache(mockRedis);
        const key = cache.key('test', 'roundtrip');

        await cache.set(key, value, 300);
        const retrieved = await cache.get(key);

        // JSON round-trip normalizes -0 to 0, so we compare against JSON-normalized value
        const expected = JSON.parse(JSON.stringify(value));
        assert.deepEqual(retrieved, expected);
      }),
      { numRuns: 100 }
    );
  });
});

/**
 * Property 2: Deterministic key generation
 *
 * For any object, `hashParams` produces the same hash regardless of key insertion order.
 *
 * **Validates: Requirements 2.7, 4.3**
 */
describe('Property 2: Deterministic key generation', () => {
  it('hashParams produces identical hash regardless of key insertion order', () => {
    fc.assert(
      fc.property(
        fc.dictionary(
          fc.string({ minLength: 1, maxLength: 10 }).filter(s => /^[a-zA-Z]/.test(s)),
          fc.oneof(fc.string(), fc.integer(), fc.boolean()),
          { minKeys: 2, maxKeys: 8 }
        ),
        (obj) => {
          const cache = createCache(null);

          // Original order
          const hash1 = cache.hashParams(obj);

          // Reversed key order
          const reversedKeys = Object.keys(obj).reverse();
          const reversed = {};
          for (const k of reversedKeys) {
            reversed[k] = obj[k];
          }
          const hash2 = cache.hashParams(reversed);

          // Shuffled key order
          const shuffledKeys = Object.keys(obj).sort(() => Math.random() - 0.5);
          const shuffled = {};
          for (const k of shuffledKeys) {
            shuffled[k] = obj[k];
          }
          const hash3 = cache.hashParams(shuffled);

          assert.equal(hash1, hash2, 'Hash should be same for reversed key order');
          assert.equal(hash1, hash3, 'Hash should be same for shuffled key order');
        }
      ),
      { numRuns: 100 }
    );
  });
});

/**
 * Property 3: Namespace key format invariant
 *
 * For any namespace and identifier strings, `key()` produces `parliament:<namespace>:<identifier>`.
 *
 * **Validates: Requirements 2.1**
 */
describe('Property 3: Namespace key format invariant', () => {
  it('key() produces parliament:<namespace>:<identifier> format', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 20 }).filter(s => !s.includes(':')),
        fc.string({ minLength: 1, maxLength: 30 }).filter(s => !s.includes(':')),
        (namespace, identifier) => {
          const cache = createCache(null);
          const result = cache.key(namespace, identifier);

          assert.equal(
            result,
            `parliament:${namespace}:${identifier}`,
            `Expected key to be parliament:${namespace}:${identifier}, got ${result}`
          );

          // Verify prefix
          assert.ok(result.startsWith('parliament:'));

          // Verify structure: exactly 3 parts separated by ':'
          const parts = result.split(':');
          assert.equal(parts[0], 'parliament');
          assert.equal(parts[1], namespace);
          assert.equal(parts[2], identifier);
        }
      ),
      { numRuns: 100 }
    );
  });
});

/**
 * Property 4: Graceful degradation preserves correctness
 *
 * For any cache operation with null client, returns default value without throwing.
 * - get → null
 * - set → false
 * - del → false
 * - invalidatePattern → 0
 *
 * **Validates: Requirements 1.4, 2.6, 9.2**
 */
describe('Property 4: Graceful degradation preserves correctness', () => {
  it('get returns null with null client for any key', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 50 }),
        async (key) => {
          const cache = createCache(null);
          const result = await cache.get(key);
          assert.equal(result, null);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('set returns false with null client for any value', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 50 }),
        fc.jsonValue(),
        fc.integer({ min: 1, max: 3600 }),
        async (key, value, ttl) => {
          const cache = createCache(null);
          const result = await cache.set(key, value, ttl);
          assert.equal(result, false);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('del returns false with null client for any key', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 50 }),
        async (key) => {
          const cache = createCache(null);
          const result = await cache.del(key);
          assert.equal(result, false);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('invalidatePattern returns 0 with null client for any pattern', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 50 }),
        async (pattern) => {
          const cache = createCache(null);
          const result = await cache.invalidatePattern(pattern);
          assert.equal(result, 0);
        }
      ),
      { numRuns: 100 }
    );
  });
});

/**
 * Property 6: Search cache key uniqueness
 *
 * For any two distinct parameter sets, `hashParams` produces different keys.
 * Tests with pairs of objects that differ in at least one field value.
 *
 * **Validates: Requirements 4.3**
 */
describe('Property 6: Search cache key uniqueness', () => {
  it('distinct search param objects produce different hashes', () => {
    // Generate pairs of search-like param objects that definitely differ
    // Use string-based dates to avoid Invalid Date issues with fc.date()
    const dateArb = fc.tuple(
      fc.integer({ min: 2000, max: 2030 }),
      fc.integer({ min: 1, max: 12 }),
      fc.integer({ min: 1, max: 28 })
    ).map(([y, m, d]) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`);

    const searchParamArb = fc.record({
      query: fc.string({ minLength: 1, maxLength: 30 }),
      entityFilter: fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 20 })),
      dateFrom: fc.oneof(fc.constant(null), dateArb),
      dateTo: fc.oneof(fc.constant(null), dateArb),
      speaker: fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 20 })),
      limit: fc.integer({ min: 1, max: 50 }),
    });

    fc.assert(
      fc.property(
        searchParamArb,
        searchParamArb,
        (params1, params2) => {
          const cache = createCache(null);

          // Normalize: remove null values for comparison (hashParams strips them)
          const norm1 = {};
          const norm2 = {};
          for (const k of Object.keys(params1)) {
            if (params1[k] !== null && params1[k] !== undefined) norm1[k] = params1[k];
          }
          for (const k of Object.keys(params2)) {
            if (params2[k] !== null && params2[k] !== undefined) norm2[k] = params2[k];
          }

          // Skip test if normalized objects are actually equal
          if (JSON.stringify(Object.entries(norm1).sort()) === JSON.stringify(Object.entries(norm2).sort())) {
            return; // not a counterexample — objects are the same after normalization
          }

          const hash1 = cache.hashParams(params1);
          const hash2 = cache.hashParams(params2);

          assert.notEqual(
            hash1, hash2,
            `Different params should produce different hashes.\n` +
            `  params1: ${JSON.stringify(params1)}\n` +
            `  params2: ${JSON.stringify(params2)}\n` +
            `  hash: ${hash1}`
          );
        }
      ),
      { numRuns: 200 }
    );
  });
});
