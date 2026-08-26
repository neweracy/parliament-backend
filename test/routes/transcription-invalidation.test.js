'use strict';

/**
 * Transcription route — cache invalidation and health check unit tests.
 *
 * Tests that cache invalidation fires on successful ingest, that invalidation
 * failures do not block the ingest response, and that the health endpoint
 * includes a `redis` field with the correct state.
 *
 * All tests mock Redis — no running Redis instance required.
 *
 * Validates: Requirements 8.1, 8.2, 8.4, 1.5, 9.4
 *
 * @module test/routes/transcription-invalidation
 */

const { describe, it, afterEach } = require('node:test');
const assert = require('node:assert/strict');

const { completeTranscription, failTranscription, jobs } = require('../../routes/transcription');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Creates a mock cache that tracks del calls.
 */
function createMockCache() {
  const delCalls = [];
  return {
    key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
    get: async () => null,
    set: async () => true,
    del: async (key) => {
      delCalls.push(key);
      return true;
    },
    invalidatePattern: async () => 0,
    hashParams: () => 'mockhash',
    _delCalls: delCalls,
  };
}

/**
 * Creates a mock cache where del() rejects (simulates Redis failure on invalidation).
 */
function createFailingDelCache() {
  const delCalls = [];
  return {
    key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
    get: async () => null,
    set: async () => true,
    del: async (key) => {
      delCalls.push(key);
      throw new Error('Redis connection lost');
    },
    invalidatePattern: async () => 0,
    hashParams: () => 'mockhash',
    _delCalls: delCalls,
  };
}

/**
 * Creates a mock db that handles completeTranscription's queries.
 */
function createMockDb() {
  return {
    query: async (text) => {
      if (text.includes('MAX(version)')) return { rows: [{ next_version: 1 }] };
      if (text.includes('INSERT INTO transcript')) return { rows: [{ id: 42 }] };
      return { rows: [] };
    },
  };
}

/**
 * Creates a mock db that handles failTranscription's queries.
 */
function createFailMockDb() {
  return {
    query: async () => ({ rows: [] }),
  };
}

/**
 * Minimal transcription result for completeTranscription.
 */
const RESULT = {
  rawText: 'The speaker rose.',
  correctedText: 'The speaker rose.',
  entities: [],
  wordTimings: [],
  durationS: null,
};

// ---------------------------------------------------------------------------
// Tests — Cache invalidation on successful ingest
// ---------------------------------------------------------------------------

describe('Transcription invalidation — fires on successful ingest', () => {
  afterEach(() => {
    jobs.clear();
  });

  it('invalidates suggestions and dashboard caches after completeTranscription', async () => {
    const cache = createMockCache();
    const db = createMockDb();

    // Set up a job in the Map (simulates a job that was being processed)
    jobs.set('inv-job-1', {
      status: 'processing',
      progress: 80,
      recordId: '1',
      sittingId: '1',
      error: null,
    });

    await completeTranscription('inv-job-1', '1', RESULT, db, cache);

    // Allow fire-and-forget promises to settle
    await new Promise((resolve) => setTimeout(resolve, 50));

    assert.ok(
      cache._delCalls.includes('parliament:suggestions:current'),
      'Should invalidate suggestions cache'
    );
    assert.ok(
      cache._delCalls.includes('parliament:dashboard:stats'),
      'Should invalidate dashboard stats cache'
    );
  });

  it('does NOT invalidate search cache keys (TTL-only per Req 8.3)', async () => {
    const cache = createMockCache();
    const db = createMockDb();

    jobs.set('inv-job-2', {
      status: 'processing',
      progress: 80,
      recordId: '2',
      sittingId: '1',
      error: null,
    });

    await completeTranscription('inv-job-2', '2', RESULT, db, cache);

    await new Promise((resolve) => setTimeout(resolve, 50));

    const searchDels = cache._delCalls.filter((k) => k.includes('search'));
    assert.equal(searchDels.length, 0, 'Should NOT invalidate any search cache keys');
  });
});

// ---------------------------------------------------------------------------
// Tests — Invalidation does NOT fire on failure
// ---------------------------------------------------------------------------

describe('Transcription invalidation — does NOT fire on failure', () => {
  afterEach(() => {
    jobs.clear();
  });

  it('does not call cache.del for suggestions/dashboard when failTranscription is called', async () => {
    const cache = createMockCache();
    const db = createFailMockDb();

    jobs.set('fail-inv-job', {
      status: 'processing',
      progress: 30,
      recordId: '5',
      sittingId: '1',
      error: null,
    });

    await failTranscription('fail-inv-job', '5', 'ASR timeout', db, cache);

    await new Promise((resolve) => setTimeout(resolve, 50));

    const suggestionsInv = cache._delCalls.filter((k) => k.includes('suggestions'));
    const dashboardInv = cache._delCalls.filter((k) => k.includes('dashboard'));

    assert.equal(suggestionsInv.length, 0, 'Should NOT invalidate suggestions on failure');
    assert.equal(dashboardInv.length, 0, 'Should NOT invalidate dashboard on failure');
  });
});

// ---------------------------------------------------------------------------
// Tests — Invalidation failure does not block ingest
// ---------------------------------------------------------------------------

describe('Transcription invalidation — failure does not block ingest', () => {
  afterEach(() => {
    jobs.clear();
  });

  it('completeTranscription succeeds even when cache.del throws', async () => {
    const cache = createFailingDelCache();
    const db = createMockDb();

    jobs.set('inv-fail-job', {
      status: 'processing',
      progress: 90,
      recordId: '3',
      sittingId: '1',
      error: null,
    });

    // completeTranscription should NOT throw even though del() throws
    await assert.doesNotReject(
      async () => completeTranscription('inv-fail-job', '3', RESULT, db, cache),
      'Invalidation failure should not block completeTranscription'
    );

    // Verify the job was still marked as completed
    const job = jobs.get('inv-fail-job');
    assert.equal(job.status, 'completed');
    assert.equal(job.progress, 100);
  });

  it('the ingest result is unaffected by del failure', async () => {
    const cache = createFailingDelCache();
    const db = createMockDb();

    jobs.set('inv-fail-job-2', {
      status: 'processing',
      progress: 70,
      recordId: '4',
      sittingId: '1',
      error: null,
    });

    await completeTranscription('inv-fail-job-2', '4', RESULT, db, cache);

    // The function should have completed all DB operations (INSERT + UPDATE)
    // despite cache invalidation failing. Verify via job state.
    const job = jobs.get('inv-fail-job-2');
    assert.equal(job.status, 'completed');
    assert.equal(job.error, null);
  });
});

// ---------------------------------------------------------------------------
// Tests — Health check includes redis field
// ---------------------------------------------------------------------------

describe('Health check — redis field', () => {
  it('healthCheck() returns "disabled" state when client is null', async () => {
    // Directly test the healthCheck function from redis-client
    // Since REDIS_URL is not set in test environment, it should be disabled
    const { healthCheck } = require('../../lib/redis-client');
    const result = await healthCheck();

    assert.ok(result, 'healthCheck should return an object');
    assert.ok(
      ['connected', 'connecting', 'disconnected', 'disabled'].includes(result.state),
      `State should be one of the valid values, got: ${result.state}`
    );
  });

  it('healthCheck() result has expected shape with state and latencyMs', async () => {
    const { healthCheck } = require('../../lib/redis-client');
    const result = await healthCheck();

    assert.ok('state' in result, 'Should have a state field');
    assert.ok('latencyMs' in result, 'Should have a latencyMs field');
    assert.ok(
      typeof result.state === 'string',
      'state should be a string'
    );
    assert.ok(
      result.latencyMs === null || typeof result.latencyMs === 'number',
      'latencyMs should be null or a number'
    );
  });

  it('healthCheck() returns "disabled" and null latency when no REDIS_URL', async () => {
    // In test environment, REDIS_URL is not configured
    const { healthCheck } = require('../../lib/redis-client');
    const result = await healthCheck();

    // Without REDIS_URL, the client is null and state should be 'disabled'
    assert.equal(result.state, 'disabled');
    assert.equal(result.latencyMs, null);
  });
});
