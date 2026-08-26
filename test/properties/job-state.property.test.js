'use strict';

/**
 * Property tests for job state round-trip via Redis.
 *
 * Property 5: Job state round-trip via Redis
 * - For any valid job state object, storing and retrieving produces a deeply equal object
 * - Generate job states with status in {queued, processing, completed, failed}, progress 0–100
 *
 * **Validates: Requirements 3.1, 3.2, 3.5**
 *
 * @module test/properties/job-state.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { createCache } = require('../../lib/cache');

// ─── Mock Redis ──────────────────────────────────────────────────────────────

/**
 * Creates a mock Redis client backed by an in-memory Map.
 * Mimics ioredis interface enough for cache utility operations.
 */
function createMockRedis() {
  const store = new Map();
  return {
    get: async (key) => store.get(key) ?? null,
    set: async (key, val, ..._args) => { store.set(key, val); return 'OK'; },
    del: async (key) => { store.delete(key); return 1; },
    keys: async () => [],
    pipeline: () => ({ del: () => {}, exec: async () => [] }),
    quit: async () => {},
    status: 'ready',
    _store: store,
  };
}

// ─── Arbitraries ─────────────────────────────────────────────────────────────

/**
 * Arbitrary for valid job state objects matching the schema:
 * { status, progress, recordId, sittingId, error }
 *
 * Uses .map() to produce plain objects (with Object.prototype) since
 * fc.record() creates null-prototype objects that differ from JSON.parse output.
 */
const jobStateArb = fc.record({
  status: fc.constantFrom('queued', 'processing', 'completed', 'failed'),
  progress: fc.integer({ min: 0, max: 100 }),
  recordId: fc.stringMatching(/^[1-9][0-9]{0,5}$/),
  sittingId: fc.stringMatching(/^[1-9][0-9]{0,5}$/),
  error: fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 100 })),
}).map((obj) => ({ ...obj }));

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 3.1, 3.2, 3.5**
 */
describe('Property 5: Job state round-trip via Redis', () => {
  it('any valid job state survives set→get via cache', async () => {
    await fc.assert(
      fc.asyncProperty(jobStateArb, async (jobState) => {
        const mockRedis = createMockRedis();
        const cache = createCache(mockRedis);
        const key = cache.key('jobs', jobState.recordId);

        await cache.set(key, jobState, 3600);
        const retrieved = await cache.get(key);

        assert.deepEqual(retrieved, jobState);
      }),
      { numRuns: 100 }
    );
  });

  it('job state with all status variants round-trips correctly', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom('queued', 'processing', 'completed', 'failed'),
        fc.integer({ min: 0, max: 100 }),
        fc.stringMatching(/^[1-9][0-9]{0,5}$/),
        fc.stringMatching(/^[1-9][0-9]{0,5}$/),
        fc.oneof(fc.constant(null), fc.string({ minLength: 1, maxLength: 50 })),
        async (status, progress, recordId, sittingId, error) => {
          const jobState = { status, progress, recordId, sittingId, error };
          const mockRedis = createMockRedis();
          const cache = createCache(mockRedis);
          const key = cache.key('jobs', recordId);

          await cache.set(key, jobState, 60);
          const retrieved = await cache.get(key);

          assert.deepEqual(retrieved, jobState);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('distinct job IDs produce independent cache entries', async () => {
    await fc.assert(
      fc.asyncProperty(
        jobStateArb,
        jobStateArb,
        fc.stringMatching(/^[1-9][0-9]{0,5}$/),
        fc.stringMatching(/^[1-9][0-9]{0,5}$/).filter((s) => s.length > 1),
        async (stateA, stateB, idA, idB) => {
          // Ensure distinct IDs
          fc.pre(idA !== idB);

          const mockRedis = createMockRedis();
          const cache = createCache(mockRedis);
          const keyA = cache.key('jobs', idA);
          const keyB = cache.key('jobs', idB);

          await cache.set(keyA, stateA, 3600);
          await cache.set(keyB, stateB, 3600);

          const retrievedA = await cache.get(keyA);
          const retrievedB = await cache.get(keyB);

          assert.deepEqual(retrievedA, stateA);
          assert.deepEqual(retrievedB, stateB);
        }
      ),
      { numRuns: 100 }
    );
  });
});
