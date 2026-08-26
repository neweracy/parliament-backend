'use strict';

/**
 * Transcription route — Redis cache integration unit tests.
 *
 * Tests the transcription job state migration to Redis with in-memory Map
 * fallback. All tests mock Redis via a fake cache utility — no running Redis
 * instance required.
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 9.4
 *
 * @module test/routes/transcription-cache
 */

const { describe, it, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const transcriptionRoutes = require('../../routes/transcription');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Creates a mock cache utility that mimics lib/cache.js backed by an in-memory Map.
 * All operations succeed by default.
 */
function createMockCache() {
  const store = new Map();
  return {
    key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
    get: async (key) => {
      const raw = store.get(key);
      return raw ? JSON.parse(raw) : null;
    },
    set: async (key, value, _ttl) => {
      store.set(key, JSON.stringify(value));
      return true;
    },
    del: async (key) => {
      store.delete(key);
      return true;
    },
    invalidatePattern: async () => 0,
    hashParams: (_obj) => 'mockhash',
    _store: store,
  };
}

/**
 * Creates a mock cache that fails on all writes (simulates Redis unavailable).
 */
function createFailingCache() {
  return {
    key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
    get: async () => null,
    set: async () => false,
    del: async () => false,
    invalidatePattern: async () => 0,
    hashParams: (_obj) => 'mockhash',
  };
}

/**
 * Creates a mock db with configurable query responses.
 */
function createMockDb(responses = []) {
  let callIndex = 0;
  const calls = [];

  return {
    query(text, params) {
      calls.push({ text, params });
      const response = responses[callIndex] || { rows: [] };
      callIndex++;
      return Promise.resolve(response);
    },
    getCalls() { return calls; },
  };
}

/** No-op requireSession middleware that sets req.user for RBAC */
function passThrough(req, res, next) {
  req.user = { role: 'Admin', username: 'test-user' };
  next();
}

/** Builds an Express app with transcription routes, optionally passing cache */
function buildApp(db, cache) {
  const app = express();
  app.use(express.json());
  app.use(transcriptionRoutes(passThrough, db, cache));
  return app;
}

/** Sample record row with audio */
const SAMPLE_RECORD = {
  id: 10,
  sitting_id: 1,
  audio_path: '/audio/session.wav',
  status: 'Draft',
  progress: 0,
  error: null,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Transcription cache — job creation stores state in Redis', () => {
  afterEach(() => {
    const { jobs, jobRedisRefs } = require('../../routes/transcription');
    jobs.clear();
    jobRedisRefs.clear();
  });

  it('stores job state in Redis when cache is provided and working', async () => {
    // Track all cache.set calls to verify the initial write
    const setCalls = [];
    const store = new Map();
    const cache = {
      key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
      get: async (key) => {
        const raw = store.get(key);
        return raw ? JSON.parse(raw) : null;
      },
      set: async (key, value, ttl) => {
        setCalls.push({ key, value: JSON.parse(JSON.stringify(value)), ttl });
        store.set(key, JSON.stringify(value));
        return true;
      },
      del: async (key) => { store.delete(key); return true; },
      invalidatePattern: async () => 0,
      hashParams: () => 'mockhash',
    };
    const db = createMockDb([
      { rows: [SAMPLE_RECORD] }, // SELECT record
      { rows: [] },             // UPDATE status to Transcribing
    ]);
    const app = buildApp(db, cache);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    assert.equal(res.status, 202);
    const jobId = res.body.jobId;

    // Find the initial job creation cache.set call
    const jobKey = `parliament:jobs:${jobId}`;
    const initialSet = setCalls.find(c => c.key === jobKey && c.value.status === 'queued');
    assert.ok(initialSet, 'Initial job state should be stored in Redis with status "queued"');
    assert.equal(initialSet.value.progress, 0);
    assert.equal(initialSet.value.recordId, '10');
    assert.equal(initialSet.value.sittingId, '1');
    assert.equal(initialSet.value.error, null);
  });

  it('records jobId in jobRedisRefs when Redis write succeeds', async () => {
    const cache = createMockCache();
    const db = createMockDb([
      { rows: [SAMPLE_RECORD] },
      { rows: [] },
    ]);
    const app = buildApp(db, cache);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    const { jobRedisRefs } = require('../../routes/transcription');
    assert.equal(jobRedisRefs.get('10'), res.body.jobId);
  });

  it('does NOT store job in in-memory Map when Redis succeeds', async () => {
    const cache = createMockCache();
    const db = createMockDb([
      { rows: [SAMPLE_RECORD] },
      { rows: [] },
    ]);
    const app = buildApp(db, cache);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    const { jobs } = require('../../routes/transcription');
    assert.equal(jobs.has(res.body.jobId), false, 'Job should not be in Map when Redis succeeds');
  });
});

describe('Transcription cache — status poll retrieves from Redis', () => {
  afterEach(() => {
    const { jobs, jobRedisRefs } = require('../../routes/transcription');
    jobs.clear();
    jobRedisRefs.clear();
  });

  it('retrieves job state from Redis via jobRedisRefs lookup', async () => {
    const cache = createMockCache();
    const { jobRedisRefs } = require('../../routes/transcription');

    // Simulate a job stored in Redis
    const jobId = 'redis-job-1';
    const jobKey = cache.key('jobs', jobId);
    await cache.set(jobKey, {
      status: 'processing',
      progress: 60,
      recordId: '10',
      sittingId: '1',
      error: null,
    }, 3600);
    jobRedisRefs.set('10', jobId);

    const db = createMockDb([]);
    const app = buildApp(db, cache);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'processing');
    assert.equal(res.body.progress, 60);
  });

  it('falls back to DB when job is not in Redis or Map', async () => {
    const cache = createMockCache();
    const db = createMockDb([
      { rows: [{ status: 'Draft', progress: 100, error: null }] },
    ]);
    const app = buildApp(db, cache);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'completed');
    assert.equal(res.body.progress, 100);
  });
});

describe('Transcription cache — fallback to Map when Redis fails', () => {
  afterEach(() => {
    const { jobs, jobRedisRefs } = require('../../routes/transcription');
    jobs.clear();
    jobRedisRefs.clear();
  });

  it('falls back to in-memory Map when cache.set returns false', async () => {
    const cache = createFailingCache();
    const db = createMockDb([
      { rows: [SAMPLE_RECORD] },
      { rows: [] },
    ]);
    const app = buildApp(db, cache);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    assert.equal(res.status, 202);
    const jobId = res.body.jobId;

    // Verify job is in in-memory Map (fallback)
    const { jobs } = require('../../routes/transcription');
    const job = jobs.get(jobId);
    assert.ok(job, 'Job should be in Map when Redis fails');
    // Status may have progressed from 'queued' to 'processing' by the async runner
    assert.ok(['queued', 'processing', 'failed'].includes(job.status), 'Job should have a valid status');
    assert.equal(job.recordId, '10');
    assert.equal(job.sittingId, '1');
  });

  it('does NOT add to jobRedisRefs when Redis write fails', async () => {
    const cache = createFailingCache();
    const db = createMockDb([
      { rows: [SAMPLE_RECORD] },
      { rows: [] },
    ]);
    const app = buildApp(db, cache);

    await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    const { jobRedisRefs } = require('../../routes/transcription');
    assert.equal(jobRedisRefs.has('10'), false);
  });
});

describe('Transcription cache — TTL on terminal states', () => {
  afterEach(() => {
    const { jobs, jobRedisRefs } = require('../../routes/transcription');
    jobs.clear();
    jobRedisRefs.clear();
  });

  it('completeTranscription sets 60s TTL in Redis', async () => {
    const { completeTranscription, jobs } = require('../../routes/transcription');

    // Track TTL values passed to cache.set
    const setCalls = [];
    const cache = {
      key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
      get: async (_key) => null,
      set: async (key, value, ttl) => {
        setCalls.push({ key, value, ttl });
        return true;
      },
      del: async () => true,
      invalidatePattern: async () => 0,
      hashParams: () => 'mockhash',
    };

    jobs.set('complete-job', {
      status: 'processing',
      progress: 80,
      recordId: '20',
      sittingId: '2',
      error: null,
    });

    const db = createMockDb([
      { rows: [{ next_version: 1 }] }, // SELECT max version
      { rows: [{ id: 42 }] },          // INSERT transcript RETURNING id
      { rows: [] },                     // UPDATE record
    ]);

    await completeTranscription('complete-job', '20', {
      rawText: 'Raw',
      correctedText: 'Corrected',
      entities: [],
      wordTimings: [],
      durationS: null,
    }, db, cache);

    // Find the terminal state cache.set call (key should be parliament:jobs:complete-job)
    const terminalSetCall = setCalls.find(c => c.key === 'parliament:jobs:complete-job');
    assert.ok(terminalSetCall, 'Should call cache.set for terminal state');
    assert.equal(terminalSetCall.ttl, 60, 'TTL should be 60 seconds for completed jobs');
    assert.equal(terminalSetCall.value.status, 'completed');
    assert.equal(terminalSetCall.value.progress, 100);
  });

  it('failTranscription sets 60s TTL in Redis', async () => {
    const { failTranscription, jobs } = require('../../routes/transcription');

    const setCalls = [];
    const cache = {
      key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
      get: async () => null,
      set: async (key, value, ttl) => {
        setCalls.push({ key, value, ttl });
        return true;
      },
      del: async () => true,
      invalidatePattern: async () => 0,
      hashParams: () => 'mockhash',
    };

    jobs.set('fail-job', {
      status: 'processing',
      progress: 30,
      recordId: '30',
      sittingId: '3',
      error: null,
    });

    const db = createMockDb([
      { rows: [] }, // UPDATE record with error
    ]);

    await failTranscription('fail-job', '30', 'Timeout from ASR', db, cache);

    const terminalSetCall = setCalls.find(c => c.key === 'parliament:jobs:fail-job');
    assert.ok(terminalSetCall, 'Should call cache.set for terminal state');
    assert.equal(terminalSetCall.ttl, 60, 'TTL should be 60 seconds for failed jobs');
    assert.equal(terminalSetCall.value.status, 'failed');
    assert.equal(terminalSetCall.value.error, 'Timeout from ASR');
  });
});

describe('Transcription cache — backward compat (no cache)', () => {
  afterEach(() => {
    const { jobs, jobRedisRefs } = require('../../routes/transcription');
    jobs.clear();
    jobRedisRefs.clear();
  });

  it('stores jobs in Map when cache is null/undefined', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_RECORD] },
      { rows: [] },
    ]);
    // No cache parameter passed
    const app = buildApp(db, undefined);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    assert.equal(res.status, 202);

    const { jobs } = require('../../routes/transcription');
    const job = jobs.get(res.body.jobId);
    assert.ok(job, 'Job should be in Map when no cache is provided');
    // Status may have progressed from 'queued' by the async runner
    assert.ok(['queued', 'processing', 'failed'].includes(job.status), 'Job should have a valid status');
    assert.equal(job.recordId, '10');
    assert.equal(job.sittingId, '1');
  });

  it('status poll works from Map only when cache is null', async () => {
    const { jobs } = require('../../routes/transcription');
    jobs.set('map-job', {
      status: 'processing',
      progress: 55,
      recordId: '10',
      sittingId: '1',
      error: null,
    });

    const db = createMockDb([]);
    const app = buildApp(db, undefined);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'processing');
    assert.equal(res.body.progress, 55);
  });
});
