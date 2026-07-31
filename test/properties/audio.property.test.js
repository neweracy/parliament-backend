'use strict';

/**
 * Property tests for audio MIME type validation.
 *
 * Property 7: MIME type validation correctness — accept iff
 * MIME ∈ {audio/mpeg, audio/wav, audio/ogg, audio/webm, audio/mp4}
 *
 * Uses fast-check to generate arbitrary MIME type strings and verifies
 * the audio upload endpoint returns 200 for allowed types and 415 for all others.
 *
 * Validates: Requirements 4.3
 *
 * @module test/properties/audio.property
 */

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');
const path = require('path');
const fs = require('fs');
const os = require('os');

const audioRoutes = require('../../routes/audio');

// ─── Constants ───────────────────────────────────────────────────────────────

const ALLOWED_MIME_TYPES = [
  'audio/mpeg',
  'audio/wav',
  'audio/ogg',
  'audio/webm',
  'audio/mp4',
];

const COMMON_INVALID_TYPES = [
  'text/plain',
  'text/html',
  'application/json',
  'application/pdf',
  'application/octet-stream',
  'video/mp4',
  'video/webm',
  'image/png',
  'image/jpeg',
  'audio/flac',
  'audio/aac',
  'audio/x-wav',
  'multipart/form-data',
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware.
 */
function passThrough(_req, _res, next) { next(); }

/**
 * Creates a mock db that returns a valid record for the existence check
 * and accepts the UPDATE.
 */
function createMockDb() {
  return {
    query(_text, _params) {
      return Promise.resolve({ rows: [{ id: 1 }] });
    },
  };
}

/**
 * Builds an Express app with the audio routes using a temp storage dir.
 */
function buildApp(tmpDir) {
  process.env.AUDIO_STORAGE_PATH = tmpDir;
  const db = createMockDb();
  const app = express();
  app.use(audioRoutes(passThrough, db));
  return app;
}

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generator for valid audio MIME types (from the allowed set).
 */
const validMimeArb = fc.constantFrom(...ALLOWED_MIME_TYPES);

/**
 * Generator for invalid MIME types: mix of common invalid types and arbitrary strings.
 * Filters out any string that happens to be in the allowed set.
 */
const invalidMimeArb = fc.oneof(
  fc.constantFrom(...COMMON_INVALID_TYPES),
  fc.string({ minLength: 1, maxLength: 50 }).filter(s => !ALLOWED_MIME_TYPES.includes(s))
);

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * Validates: Requirements 4.3
 */
describe('Property 7: MIME type validation correctness', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'audio-prop-test-'));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    delete process.env.AUDIO_STORAGE_PATH;
  });

  it('accepts uploads with allowed MIME types (returns 200)', async () => {
    await fc.assert(
      fc.asyncProperty(validMimeArb, async (mimeType) => {
        const app = buildApp(tmpDir);
        const fakeAudio = Buffer.alloc(512, 0);

        const res = await request(app)
          .post('/api/sittings/1/records/1/audio')
          .attach('file', fakeAudio, { filename: 'test.bin', contentType: mimeType });

        assert.equal(
          res.status, 200,
          `Expected 200 for allowed MIME type "${mimeType}", got ${res.status}`
        );
      }),
      { numRuns: 20 }
    );
  });

  it('rejects uploads with disallowed MIME types (returns 415)', async () => {
    await fc.assert(
      fc.asyncProperty(invalidMimeArb, async (mimeType) => {
        const app = buildApp(tmpDir);
        const fakeFile = Buffer.alloc(512, 0);

        const res = await request(app)
          .post('/api/sittings/1/records/1/audio')
          .attach('file', fakeFile, { filename: 'test.bin', contentType: mimeType });

        assert.equal(
          res.status, 415,
          `Expected 415 for disallowed MIME type "${mimeType}", got ${res.status}`
        );
      }),
      { numRuns: 50 }
    );
  });

  it('MIME filter is both sound and complete: accept iff MIME ∈ allowed set', async () => {
    const allMimeArb = fc.oneof(
      validMimeArb,
      invalidMimeArb
    );

    await fc.assert(
      fc.asyncProperty(allMimeArb, async (mimeType) => {
        const app = buildApp(tmpDir);
        const fakeFile = Buffer.alloc(512, 0);

        const res = await request(app)
          .post('/api/sittings/1/records/1/audio')
          .attach('file', fakeFile, { filename: 'test.bin', contentType: mimeType });

        const isAllowed = ALLOWED_MIME_TYPES.includes(mimeType);

        if (isAllowed) {
          assert.equal(
            res.status, 200,
            `MIME "${mimeType}" is in the allowed set but got status ${res.status}`
          );
        } else {
          assert.equal(
            res.status, 415,
            `MIME "${mimeType}" is NOT in the allowed set but got status ${res.status} (expected 415)`
          );
        }
      }),
      { numRuns: 80 }
    );
  });
});
