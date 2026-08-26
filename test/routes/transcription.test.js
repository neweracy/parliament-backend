'use strict';

/**
 * Transcription route unit tests.
 *
 * Uses a mock db to test route logic without a real PostgreSQL connection.
 * Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
 *
 * @module test/routes/transcription
 */

const { describe, it, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const transcriptionRoutes = require('../../routes/transcription');

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

/**
 * A no-op requireSession middleware that sets req.user with Admin permissions.
 */
function passThrough(req, res, next) {
  req.user = {
    userId: 'test-user',
    email: 'admin@test.com',
    name: 'Test Admin',
    role: 'Admin',
    permissions: [
      'system_config', 'view_records', 'manage_users',
      'create_sitting', 'edit_record', 'export_hansard',
      'upload_audio',
    ],
  };
  next();
}

/**
 * Builds an Express app with the transcription routes using the given db mock.
 */
function buildApp(db) {
  const app = express();
  app.use(express.json());
  app.use(transcriptionRoutes(passThrough, db));
  return app;
}

/** A sample record row with audio path set */
const SAMPLE_RECORD_ROW = {
  id: 10,
  sitting_id: 1,
  audio_path: '/audio/morning.wav',
  status: 'Draft',
  progress: 0,
  error: null,
};

/** A sample record row without audio */
const RECORD_NO_AUDIO = {
  id: 11,
  sitting_id: 1,
  audio_path: null,
  status: 'Draft',
  progress: 0,
  error: null,
};

describe('POST /api/sittings/:sittingId/records/:recordId/transcribe', () => {
  afterEach(() => {
    // Clear the in-memory jobs map between tests
    const { jobs } = require('../../routes/transcription');
    jobs.clear();
  });

  it('returns 202 with a jobId when record exists and has audio', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_RECORD_ROW] }, // SELECT record
      { rows: [SAMPLE_RECORD_ROW] }, // UPDATE status to Transcribing
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    assert.equal(res.status, 202);
    assert.ok(res.body.jobId);
    assert.equal(typeof res.body.jobId, 'string');
    // UUID format check
    assert.match(res.body.jobId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  });

  it('sets record status to Transcribing in DB', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_RECORD_ROW] }, // SELECT record
      { rows: [SAMPLE_RECORD_ROW] }, // UPDATE status
    ]);
    const app = buildApp(db);

    await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    const calls = db.getCalls();
    const updateCall = calls[1];
    assert.ok(updateCall.text.includes("status = 'Transcribing'"));
    assert.ok(updateCall.text.includes("progress = 0"));
  });

  it('returns 404 when record does not exist', async () => {
    const db = createMockDb([
      { rows: [] }, // SELECT record returns nothing
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/sittings/1/records/999/transcribe');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'RECORD_NOT_FOUND');
  });

  it('returns 422 when record has no audio file', async () => {
    const db = createMockDb([
      { rows: [RECORD_NO_AUDIO] }, // SELECT record without audio_path
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/sittings/1/records/11/transcribe');

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'NO_AUDIO');
  });

  it('stores job state in the in-memory map', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_RECORD_ROW] },
      { rows: [SAMPLE_RECORD_ROW] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    const { jobs } = require('../../routes/transcription');
    const job = jobs.get(res.body.jobId);
    assert.ok(job);
    assert.equal(job.recordId, '10');
    assert.equal(job.sittingId, '1');
  });
});

describe('GET /api/sittings/:sittingId/records/:recordId/transcription-status', () => {
  afterEach(() => {
    const { jobs } = require('../../routes/transcription');
    jobs.clear();
  });

  it('returns status from in-memory job when active', async () => {
    const { jobs } = require('../../routes/transcription');
    jobs.set('test-job', {
      status: 'processing',
      progress: 45,
      recordId: '10',
      sittingId: '1',
      error: null,
    });

    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'processing');
    assert.equal(res.body.progress, 45);
    assert.equal(res.body.error, undefined);
  });

  it('includes error field when job has failed', async () => {
    const { jobs } = require('../../routes/transcription');
    jobs.set('test-job', {
      status: 'failed',
      progress: 0,
      recordId: '10',
      sittingId: '1',
      error: 'ASR provider timeout',
    });

    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'failed');
    assert.equal(res.body.error, 'ASR provider timeout');
  });

  it('falls back to DB status when no in-memory job exists', async () => {
    const db = createMockDb([
      { rows: [{ status: 'Draft', progress: 100, error: null }] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'completed');
    assert.equal(res.body.progress, 100);
  });

  it('returns "processing" when DB status is Transcribing', async () => {
    const db = createMockDb([
      { rows: [{ status: 'Transcribing', progress: 30, error: null }] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'processing');
    assert.equal(res.body.progress, 30);
  });

  it('returns "failed" when DB record has an error', async () => {
    const db = createMockDb([
      { rows: [{ status: 'Draft', progress: 0, error: 'Connection lost' }] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/sittings/1/records/10/transcription-status');

    assert.equal(res.status, 200);
    assert.equal(res.body.status, 'failed');
    assert.equal(res.body.error, 'Connection lost');
  });

  it('returns 404 when record does not exist', async () => {
    const db = createMockDb([
      { rows: [] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/sittings/1/records/999/transcription-status');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'RECORD_NOT_FOUND');
  });
});

describe('completeTranscription helper', () => {
  it('inserts transcript and updates record status to Draft', async () => {
    const { completeTranscription, jobs } = require('../../routes/transcription');

    jobs.set('job-1', {
      status: 'processing',
      progress: 50,
      recordId: '10',
      sittingId: '1',
      error: null,
    });

    const db = createMockDb([
      { rows: [{ next_version: 1 }] }, // SELECT max version
      { rows: [{ id: 1 }] },           // INSERT transcript RETURNING id
      { rows: [] },                     // UPDATE record
    ]);

    await completeTranscription('job-1', '10', {
      rawText: 'Raw text from ASR',
      correctedText: 'Corrected text',
      entities: [{ name: 'Ghana', kind: 'location' }],
      wordTimings: [{ word: 'Corrected', start: 0, end: 0.5 }],
      correlationId: 'corr-123',
      provider: 'deepgram',
      durationS: 120.5,
    }, db);

    const calls = db.getCalls();

    // Verify INSERT transcript
    const insertCall = calls[1];
    assert.ok(insertCall.text.includes('INSERT INTO transcript'));
    assert.equal(insertCall.params[0], '10');    // record_id
    assert.equal(insertCall.params[1], 1);       // version
    assert.equal(insertCall.params[2], 'corr-123'); // correlation_id
    assert.equal(insertCall.params[3], 'deepgram'); // provider
    assert.equal(insertCall.params[4], 120.5);   // duration_s
    assert.equal(insertCall.params[5], 'Raw text from ASR');
    assert.equal(insertCall.params[6], 'Corrected text');

    // Verify UPDATE record to Draft
    const updateCall = calls[2];
    assert.ok(updateCall.text.includes("status = 'Draft'"));
    assert.ok(updateCall.text.includes("progress = 100"));

    // Verify in-memory job updated
    const job = jobs.get('job-1');
    assert.equal(job.status, 'completed');
    assert.equal(job.progress, 100);

    jobs.clear();
  });

  it('writes the real audio duration onto the record when ASR reports one', async () => {
    const { completeTranscription, jobs } = require('../../routes/transcription');

    jobs.set('job-dur', {
      status: 'processing',
      progress: 50,
      recordId: '10',
      sittingId: '1',
      error: null,
    });

    const db = createMockDb([
      { rows: [{ next_version: 1 }] },
      { rows: [{ id: 1 }] },
      { rows: [] },
    ]);

    // 625 seconds = 00:10:25, matching the real bug report of a ~10 minute
    // recording that kept showing "0m" on the registry/sitting cards.
    await completeTranscription('job-dur', '10', {
      rawText: 'Raw text',
      correctedText: 'Corrected text',
      entities: [],
      wordTimings: [],
      durationS: 625,
    }, db);

    const updateCall = db.getCalls()[2];
    assert.ok(updateCall.text.includes('duration = $2'));
    assert.ok(updateCall.text.includes('duration_hours = $3'));
    assert.deepEqual(updateCall.params, ['10', '00:10:25', 625 / 3600]);

    jobs.clear();
  });

  it('leaves duration columns untouched when ASR reports no duration', async () => {
    const { completeTranscription, jobs } = require('../../routes/transcription');

    jobs.set('job-nodur', {
      status: 'processing',
      progress: 50,
      recordId: '10',
      sittingId: '1',
      error: null,
    });

    const db = createMockDb([
      { rows: [{ next_version: 1 }] },
      { rows: [{ id: 1 }] },
      { rows: [] },
    ]);

    await completeTranscription('job-nodur', '10', {
      rawText: 'Raw text',
      correctedText: 'Corrected text',
      entities: [],
      wordTimings: [],
      durationS: null,
    }, db);

    const updateCall = db.getCalls()[2];
    assert.ok(!updateCall.text.includes('duration ='));
    assert.deepEqual(updateCall.params, ['10']);

    jobs.clear();
  });
});

describe('formatDurationHMS', () => {
  const { formatDurationHMS } = require('../../routes/transcription');

  it('formats seconds as zero-padded HH:MM:SS', () => {
    assert.equal(formatDurationHMS(625), '00:10:25');
    assert.equal(formatDurationHMS(3661), '01:01:01');
    assert.equal(formatDurationHMS(59), '00:00:59');
    assert.equal(formatDurationHMS(0), '00:00:00');
  });

  it('rounds fractional seconds', () => {
    assert.equal(formatDurationHMS(90.6), '00:01:31');
  });
});

describe('buildDurationFields', () => {
  const { buildDurationFields } = require('../../routes/transcription');

  it('returns a SET fragment and params for a positive duration', () => {
    const fields = buildDurationFields(625);
    assert.equal(fields.setClause, ', duration = $2, duration_hours = $3');
    assert.deepEqual(fields.params, ['00:10:25', 625 / 3600]);
    assert.deepEqual(fields.broadcastFields, {
      duration: '00:10:25',
      durationHours: 625 / 3600,
    });
  });

  it('returns an empty fragment for null, undefined, zero, or negative durations', () => {
    for (const value of [null, undefined, 0, -5, NaN]) {
      const fields = buildDurationFields(value);
      assert.equal(fields.setClause, '');
      assert.deepEqual(fields.params, []);
      assert.deepEqual(fields.broadcastFields, {});
    }
  });

  it('offsets placeholder numbers by the given paramOffset', () => {
    const fields = buildDurationFields(120, 3);
    assert.equal(fields.setClause, ', duration = $4, duration_hours = $5');
  });
});

describe('failTranscription helper', () => {
  it('sets error on record and returns to Draft status', async () => {
    const { failTranscription, jobs } = require('../../routes/transcription');

    jobs.set('job-2', {
      status: 'processing',
      progress: 50,
      recordId: '10',
      sittingId: '1',
      error: null,
    });

    const db = createMockDb([
      { rows: [] }, // UPDATE record with error
    ]);

    await failTranscription('job-2', '10', 'ASR provider timeout', db);

    const calls = db.getCalls();
    const updateCall = calls[0];
    assert.ok(updateCall.text.includes("status = 'Draft'"));
    assert.ok(updateCall.text.includes("error = $1"));
    assert.equal(updateCall.params[0], 'ASR provider timeout');
    assert.equal(updateCall.params[1], '10');

    // Verify in-memory job updated
    const job = jobs.get('job-2');
    assert.equal(job.status, 'failed');
    assert.equal(job.error, 'ASR provider timeout');

    jobs.clear();
  });
});

describe('Authentication requirement', () => {
  it('returns 401 when requireSession rejects', async () => {
    function rejectAuth(req, res) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    const db = createMockDb([]);
    const app = express();
    app.use(express.json());
    app.use(transcriptionRoutes(rejectAuth, db));

    const res = await request(app)
      .post('/api/sittings/1/records/10/transcribe');

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'MISSING_TOKEN');
  });
});
