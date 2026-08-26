'use strict';

/**
 * Audio route unit tests.
 *
 * Tests MIME validation, file size enforcement, upload success, and GET streaming.
 * Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5
 *
 * @module test/routes/audio
 */

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');
const path = require('path');
const fs = require('fs');
const os = require('os');

const audioRoutes = require('../../routes/audio');

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
 * Builds an Express app with the audio routes using the given db mock.
 * Sets AUDIO_STORAGE_PATH to a temp dir to avoid polluting the project.
 */
function buildApp(db, storagePath) {
  process.env.AUDIO_STORAGE_PATH = storagePath;
  const app = express();
  app.use(audioRoutes(passThrough, db));
  return app;
}

describe('POST /api/sittings/:sittingId/records/:recordId/audio', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'audio-test-'));
  });

  afterEach(() => {
    // Clean up temp directory
    fs.rmSync(tmpDir, { recursive: true, force: true });
    delete process.env.AUDIO_STORAGE_PATH;
  });

  it('accepts a valid audio/mpeg upload and returns 200', async () => {
    const db = createMockDb([
      { rows: [{ id: 1 }] },  // record exists check
      { rows: [] },           // UPDATE result
    ]);
    const app = buildApp(db, tmpDir);

    // Create a small fake audio buffer
    const fakeAudio = Buffer.alloc(1024, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeAudio, { filename: 'test.mp3', contentType: 'audio/mpeg' });

    assert.equal(res.status, 200);
    assert.equal(res.body.fileName, 'test.mp3');
    assert.equal(res.body.size, 1024);
  });

  it('accepts audio/wav uploads', async () => {
    const db = createMockDb([
      { rows: [{ id: 1 }] },
      { rows: [] },
    ]);
    const app = buildApp(db, tmpDir);

    const fakeAudio = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeAudio, { filename: 'test.wav', contentType: 'audio/wav' });

    assert.equal(res.status, 200);
  });

  it('accepts audio/ogg uploads', async () => {
    const db = createMockDb([
      { rows: [{ id: 1 }] },
      { rows: [] },
    ]);
    const app = buildApp(db, tmpDir);

    const fakeAudio = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeAudio, { filename: 'test.ogg', contentType: 'audio/ogg' });

    assert.equal(res.status, 200);
  });

  it('accepts audio/webm uploads', async () => {
    const db = createMockDb([
      { rows: [{ id: 1 }] },
      { rows: [] },
    ]);
    const app = buildApp(db, tmpDir);

    const fakeAudio = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeAudio, { filename: 'test.webm', contentType: 'audio/webm' });

    assert.equal(res.status, 200);
  });

  it('accepts audio/mp4 uploads', async () => {
    const db = createMockDb([
      { rows: [{ id: 1 }] },
      { rows: [] },
    ]);
    const app = buildApp(db, tmpDir);

    const fakeAudio = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeAudio, { filename: 'test.m4a', contentType: 'audio/mp4' });

    assert.equal(res.status, 200);
  });

  it('rejects non-audio MIME types with 415 and UNSUPPORTED_MEDIA code', async () => {
    const db = createMockDb([]);
    const app = buildApp(db, tmpDir);

    const fakeFile = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeFile, { filename: 'test.txt', contentType: 'text/plain' });

    assert.equal(res.status, 415);
    assert.equal(res.body.error.code, 'UNSUPPORTED_MEDIA');
  });

  it('rejects application/pdf with 415', async () => {
    const db = createMockDb([]);
    const app = buildApp(db, tmpDir);

    const fakeFile = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeFile, { filename: 'doc.pdf', contentType: 'application/pdf' });

    assert.equal(res.status, 415);
    assert.equal(res.body.error.code, 'UNSUPPORTED_MEDIA');
  });

  it('rejects video/mp4 with 415', async () => {
    const db = createMockDb([]);
    const app = buildApp(db, tmpDir);

    const fakeFile = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', fakeFile, { filename: 'video.mp4', contentType: 'video/mp4' });

    assert.equal(res.status, 415);
    assert.equal(res.body.error.code, 'UNSUPPORTED_MEDIA');
  });

  it('returns 404 when record does not exist', async () => {
    const db = createMockDb([
      { rows: [] },  // record not found
    ]);
    const app = buildApp(db, tmpDir);

    const fakeAudio = Buffer.alloc(512, 0);

    const res = await request(app)
      .post('/api/sittings/1/records/999/audio')
      .attach('file', fakeAudio, { filename: 'test.mp3', contentType: 'audio/mpeg' });

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'RECORD_NOT_FOUND');
  });

  it('updates the record with audio_file_name and audio_path', async () => {
    const db = createMockDb([
      { rows: [{ id: 5 }] },  // record exists
      { rows: [] },           // UPDATE
    ]);
    const app = buildApp(db, tmpDir);

    const fakeAudio = Buffer.alloc(1024, 0);

    await request(app)
      .post('/api/sittings/2/records/5/audio')
      .attach('file', fakeAudio, { filename: 'session.mp3', contentType: 'audio/mpeg' });

    const updateCall = db.getCalls()[1];
    assert.ok(updateCall.text.includes('audio_file_name'));
    assert.ok(updateCall.text.includes('audio_path'));
    assert.equal(updateCall.params[0], 'session.mp3');
    // The path should be within the configured storage directory
    assert.ok(updateCall.params[1].startsWith(tmpDir));
    assert.equal(updateCall.params[2], '5');
  });
});

describe('GET /api/sittings/:sittingId/records/:recordId/audio', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'audio-test-'));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    delete process.env.AUDIO_STORAGE_PATH;
  });

  it('streams the audio file back with correct Content-Type', async () => {
    // Create a fake audio file on disk
    const filePath = path.join(tmpDir, 'test.mp3');
    const content = Buffer.alloc(256, 0xAB);
    fs.writeFileSync(filePath, content);

    const db = createMockDb([
      { rows: [{ audio_path: filePath, audio_file_name: 'test.mp3' }] },
    ]);
    const app = buildApp(db, tmpDir);

    const res = await request(app)
      .get('/api/sittings/1/records/1/audio');

    assert.equal(res.status, 200);
    assert.equal(res.headers['content-type'], 'audio/mpeg');
    assert.equal(parseInt(res.headers['content-length'], 10), 256);
    assert.equal(res.body.length, 256);
  });

  it('streams .wav files with audio/wav content type', async () => {
    const filePath = path.join(tmpDir, 'test.wav');
    fs.writeFileSync(filePath, Buffer.alloc(128, 0));

    const db = createMockDb([
      { rows: [{ audio_path: filePath, audio_file_name: 'test.wav' }] },
    ]);
    const app = buildApp(db, tmpDir);

    const res = await request(app)
      .get('/api/sittings/1/records/1/audio');

    assert.equal(res.status, 200);
    assert.equal(res.headers['content-type'], 'audio/wav');
  });

  it('returns 404 when record does not exist', async () => {
    const db = createMockDb([
      { rows: [] },
    ]);
    const app = buildApp(db, tmpDir);

    const res = await request(app)
      .get('/api/sittings/1/records/999/audio');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'RECORD_NOT_FOUND');
  });

  it('returns 404 when record has no audio_path', async () => {
    const db = createMockDb([
      { rows: [{ audio_path: null, audio_file_name: null }] },
    ]);
    const app = buildApp(db, tmpDir);

    const res = await request(app)
      .get('/api/sittings/1/records/1/audio');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'AUDIO_NOT_FOUND');
  });

  it('returns 404 when audio file is missing from disk', async () => {
    const db = createMockDb([
      { rows: [{ audio_path: '/nonexistent/file.mp3', audio_file_name: 'file.mp3' }] },
    ]);
    const app = buildApp(db, tmpDir);

    const res = await request(app)
      .get('/api/sittings/1/records/1/audio');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'AUDIO_NOT_FOUND');
  });

  it('includes Content-Disposition header with filename', async () => {
    const filePath = path.join(tmpDir, 'session-audio.mp3');
    fs.writeFileSync(filePath, Buffer.alloc(64, 0));

    const db = createMockDb([
      { rows: [{ audio_path: filePath, audio_file_name: 'session-audio.mp3' }] },
    ]);
    const app = buildApp(db, tmpDir);

    const res = await request(app)
      .get('/api/sittings/1/records/1/audio');

    assert.equal(res.status, 200);
    assert.ok(res.headers['content-disposition'].includes('session-audio.mp3'));
  });
});

describe('Authentication requirement for audio routes', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'audio-test-'));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    delete process.env.AUDIO_STORAGE_PATH;
  });

  it('returns 401 when requireSession rejects on POST', async () => {
    function rejectAuth(_req, res, _next) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    const db = createMockDb([]);
    process.env.AUDIO_STORAGE_PATH = tmpDir;
    const app = express();
    app.use(audioRoutes(rejectAuth, db));

    const res = await request(app)
      .post('/api/sittings/1/records/1/audio')
      .attach('file', Buffer.alloc(512), { filename: 'test.mp3', contentType: 'audio/mpeg' });

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'MISSING_TOKEN');
  });

  it('returns 401 when requireSession rejects on GET', async () => {
    function rejectAuth(_req, res, _next) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    const db = createMockDb([]);
    process.env.AUDIO_STORAGE_PATH = tmpDir;
    const app = express();
    app.use(audioRoutes(rejectAuth, db));

    const res = await request(app)
      .get('/api/sittings/1/records/1/audio');

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'MISSING_TOKEN');
  });
});
