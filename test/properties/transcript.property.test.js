'use strict';

/**
 * Property tests for the Transcript API.
 *
 * Property 8: Transcript persistence round-trip
 * Property 9: Transcript versioning preservation
 * Property 19: Transcript text validation
 *
 * Uses fast-check to generate arbitrary transcript data,
 * then exercises the actual route handler via supertest + express with a mock db.
 *
 * Validates: Requirements 5.4, 6.1, 6.4, 12.3
 *
 * @module test/properties/transcript.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const transcriptRoutes = require('../../routes/transcript');

// ─── Constants ───────────────────────────────────────────────────────────────

const MAX_TEXT_BYTES = 1_048_576; // 1 MB
const SITTING_ID = '1';
const RECORD_ID = '42';

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generator for a non-empty corrected text string (valid transcript text).
 * Constrained to reasonable size to keep tests fast.
 */
const validTextArb = fc.string({ minLength: 1, maxLength: 500 })
  .filter(s => s.trim().length > 0);

/**
 * Generator for a valid raw text string.
 */
const rawTextArb = fc.string({ minLength: 1, maxLength: 500 })
  .filter(s => s.trim().length > 0);

/**
 * Generator for entities (array of entity objects).
 * Uses .map to produce plain objects (avoids null-prototype comparison issues).
 */
const entityArb = fc.record({
  name: fc.string({ minLength: 1, maxLength: 50 }).filter(s => s.trim().length > 0),
  kind: fc.constantFrom('person', 'place', 'organization', 'date', 'law'),
  count: fc.integer({ min: 1, max: 20 }),
}).map(r => ({ name: r.name, kind: r.kind, count: r.count }));

const entitiesArb = fc.array(entityArb, { minLength: 0, maxLength: 5 });

/**
 * Generator for word timings (array of word timing objects).
 * Uses .map to produce plain objects (avoids null-prototype comparison issues).
 */
const wordTimingArb = fc.record({
  word: fc.string({ minLength: 1, maxLength: 30 }).filter(s => s.trim().length > 0),
  start: fc.float({ min: 0, max: 3600, noNaN: true }),
  end: fc.float({ min: 0, max: 3600, noNaN: true }),
  speaker: fc.option(fc.string({ minLength: 1, maxLength: 30 }), { nil: null }),
}).map(r => ({ word: r.word, start: r.start, end: r.end, speaker: r.speaker }));

const wordTimingsArb = fc.array(wordTimingArb, { minLength: 0, maxLength: 10 });

/**
 * Generator for a full transcript row as stored in DB.
 */
const transcriptRowArb = fc.record({
  corrected_text: validTextArb,
  raw_text: rawTextArb,
  entities: entitiesArb,
  word_timings: wordTimingsArb,
  version: fc.constant(1),
  correlation_id: fc.option(fc.string({ minLength: 5, maxLength: 20 }), { nil: null }),
  provider: fc.option(fc.constantFrom('deepgram', 'khaya', 'hybrid'), { nil: null }),
  duration_s: fc.option(fc.float({ min: 0, max: 7200, noNaN: true }), { nil: null }),
});

/**
 * Generator for N edit texts (for versioning test).
 * N is between 2 and 10.
 */
const editTextsArb = fc.array(
  fc.string({ minLength: 1, maxLength: 200 }).filter(s => s.trim().length > 0),
  { minLength: 2, maxLength: 10 }
);

/**
 * Generator for arbitrary text strings (for validation test).
 * Includes empty, whitespace-only, valid, and oversized.
 */
const arbitraryTextArb = fc.oneof(
  // Empty or whitespace-only strings
  fc.constantFrom('', ' ', '  ', '\t', '\n', '  \n\t  '),
  // Valid non-empty strings (under 1 MB)
  fc.string({ minLength: 1, maxLength: 500 }).filter(s => s.trim().length > 0),
  // Strings that are only whitespace of random length
  fc.integer({ min: 1, max: 20 }).map(n => ' '.repeat(n)),
);

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware.
 */
function passThrough(req, res, next) { next(); }

/**
 * Creates a mock DB seeded with a known transcript row.
 * GET returns the seeded data. Used for Property 8 (round-trip).
 */
function createSeededDb(transcriptRow) {
  return {
    query(text, _params) {
      // SELECT for GET - return the seeded transcript
      if (text.includes('SELECT') && text.includes('FROM transcript') && text.includes('ORDER BY version DESC')) {
        return Promise.resolve({
          rows: [{
            corrected_text: transcriptRow.corrected_text,
            raw_text: transcriptRow.raw_text,
            entities: transcriptRow.entities,
            word_timings: transcriptRow.word_timings,
            version: transcriptRow.version,
          }],
        });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

/**
 * Creates a mock DB that tracks transcript versions for sequential PATCHes.
 * Starts with an initial version 1 row, and each PATCH inserts a new version.
 * Used for Property 9 (versioning preservation).
 */
function createVersioningDb(initialText) {
  const versions = [{
    version: 1,
    corrected_text: initialText,
    raw_text: initialText,
    entities: [],
    word_timings: [],
    correlation_id: 'test-correlation',
    provider: 'deepgram',
    duration_s: 120.5,
  }];

  return {
    versions,
    query(text, params) {
      // SELECT latest version (for PATCH handler to get current version)
      if (text.includes('SELECT') && text.includes('ORDER BY version DESC') && text.includes('LIMIT 1')) {
        const latest = versions[versions.length - 1];
        return Promise.resolve({ rows: [latest] });
      }

      // INSERT new version (from PATCH handler)
      if (text.includes('INSERT INTO transcript')) {
        const newVersion = {
          version: params[1], // version param
          corrected_text: params[2], // text param
          raw_text: params[3],
          entities: JSON.parse(params[4] || '[]'),
          word_timings: JSON.parse(params[5] || '[]'),
          correlation_id: params[6],
          provider: params[7],
          duration_s: params[8],
        };
        versions.push(newVersion);
        return Promise.resolve({ rows: [{ id: versions.length }] });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

/**
 * Creates a mock DB for validation tests.
 * Has an existing transcript (version 1) so PATCH doesn't 404.
 */
function createValidationDb() {
  let insertCount = 0;
  return {
    query(text, _params) {
      // SELECT latest version for PATCH
      if (text.includes('SELECT') && text.includes('ORDER BY version DESC') && text.includes('LIMIT 1')) {
        return Promise.resolve({
          rows: [{
            version: 1,
            raw_text: 'Some raw text',
            entities: [],
            word_timings: [],
            correlation_id: null,
            provider: 'deepgram',
            duration_s: 60.0,
          }],
        });
      }

      // INSERT new version
      if (text.includes('INSERT INTO transcript')) {
        insertCount++;
        return Promise.resolve({ rows: [{ id: insertCount }] });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 5.4, 6.1**
 */
describe('Property 8: Transcript persistence round-trip', () => {
  it('seeded transcript data retrieved via GET yields identical fields', async () => {
    await fc.assert(
      fc.asyncProperty(transcriptRowArb, async (row) => {
        const db = createSeededDb(row);
        const app = express();
        app.use(express.json());
        app.use(transcriptRoutes(passThrough, db));

        const res = await request(app)
          .get(`/api/sittings/${SITTING_ID}/records/${RECORD_ID}/transcript`);

        assert.equal(res.status, 200, `Expected 200, got ${res.status}: ${JSON.stringify(res.body)}`);

        // Assert all fields match the seeded data
        assert.equal(
          res.body.correctedText, row.corrected_text,
          'correctedText should match persisted corrected_text'
        );
        assert.equal(
          res.body.rawText, row.raw_text,
          'rawText should match persisted raw_text'
        );
        assert.deepEqual(
          res.body.entities, row.entities,
          'entities should match persisted entities'
        );
        assert.deepEqual(
          res.body.wordTimings, row.word_timings,
          'wordTimings should match persisted word_timings'
        );
        assert.equal(
          res.body.version, row.version,
          'version should match persisted version'
        );
      }),
      { numRuns: 50 }
    );
  });
});

/**
 * **Validates: Requirements 6.4**
 */
describe('Property 9: Transcript versioning preservation', () => {
  it('N edits produce N+1 version rows with monotonically increasing version numbers', async () => {
    await fc.assert(
      fc.asyncProperty(editTextsArb, async (editTexts) => {
        const initialText = 'Initial transcript content for testing';
        const db = createVersioningDb(initialText);
        const app = express();
        app.use(express.json());
        app.use(transcriptRoutes(passThrough, db));

        // Apply N sequential PATCH edits
        for (const text of editTexts) {
          const res = await request(app)
            .patch(`/api/sittings/${SITTING_ID}/records/${RECORD_ID}/transcript`)
            .send({ text });

          assert.equal(res.status, 200, `Expected 200, got ${res.status}: ${JSON.stringify(res.body)}`);
        }

        // N edits + 1 initial version = N+1 total versions
        const N = editTexts.length;
        assert.equal(
          db.versions.length, N + 1,
          `Expected ${N + 1} versions (1 initial + ${N} edits), got ${db.versions.length}`
        );

        // Version numbers should be monotonically increasing: 1, 2, 3, ..., N+1
        for (let i = 0; i < db.versions.length; i++) {
          assert.equal(
            db.versions[i].version, i + 1,
            `Version at index ${i} should be ${i + 1}, got ${db.versions[i].version}`
          );
        }

        // Each successive version number is strictly greater than the previous
        for (let i = 1; i < db.versions.length; i++) {
          assert.ok(
            db.versions[i].version > db.versions[i - 1].version,
            `Version ${db.versions[i].version} should be > ${db.versions[i - 1].version}`
          );
        }
      }),
      { numRuns: 30 }
    );
  });
});

/**
 * **Validates: Requirements 12.3**
 */
describe('Property 19: Transcript text validation', () => {
  it('accepts iff trim().length > 0 AND byteLength <= 1 MB', async () => {
    await fc.assert(
      fc.asyncProperty(arbitraryTextArb, async (text) => {
        const db = createValidationDb();
        const app = express();
        app.use(express.json());
        app.use(transcriptRoutes(passThrough, db));

        const res = await request(app)
          .patch(`/api/sittings/${SITTING_ID}/records/${RECORD_ID}/transcript`)
          .send({ text });

        const trimmedLength = text.trim().length;
        const byteLength = Buffer.byteLength(text, 'utf8');

        if (trimmedLength === 0) {
          // Empty after trim → 422 EMPTY_TRANSCRIPT
          assert.equal(
            res.status, 422,
            `Empty/whitespace text "${text}" should be rejected with 422, got ${res.status}`
          );
          assert.equal(
            res.body.error.code, 'EMPTY_TRANSCRIPT',
            `Expected EMPTY_TRANSCRIPT code, got ${res.body.error.code}`
          );
        } else if (byteLength > MAX_TEXT_BYTES) {
          // Over 1 MB → 422 TRANSCRIPT_TOO_LARGE
          assert.equal(
            res.status, 422,
            `Oversized text (${byteLength} bytes) should be rejected with 422, got ${res.status}`
          );
          assert.equal(
            res.body.error.code, 'TRANSCRIPT_TOO_LARGE',
            `Expected TRANSCRIPT_TOO_LARGE code, got ${res.body.error.code}`
          );
        } else {
          // Valid text → 200
          assert.equal(
            res.status, 200,
            `Valid text (trimmed length=${trimmedLength}, bytes=${byteLength}) should be accepted with 200, got ${res.status}: ${JSON.stringify(res.body)}`
          );
        }
      }),
      { numRuns: 100 }
    );
  });

  it('rejects text exceeding 1 MB byte length', async () => {
    await fc.assert(
      fc.asyncProperty(
        // Generate text that is over 1 MB using multi-byte characters
        fc.integer({ min: 1, max: 10 }).map(extra => {
          // Create a string just over 1 MB. Use ASCII to keep it simple.
          return 'A'.repeat(MAX_TEXT_BYTES + extra);
        }),
        async (text) => {
          const db = createValidationDb();
          const app = express();
          app.use(express.json({ limit: '2mb' }));
          app.use(transcriptRoutes(passThrough, db));

          const res = await request(app)
            .patch(`/api/sittings/${SITTING_ID}/records/${RECORD_ID}/transcript`)
            .send({ text });

          assert.equal(
            res.status, 422,
            `Text of ${Buffer.byteLength(text, 'utf8')} bytes should be rejected with 422, got ${res.status}`
          );
          assert.equal(
            res.body.error.code, 'TRANSCRIPT_TOO_LARGE',
            `Expected TRANSCRIPT_TOO_LARGE code, got ${res.body.error.code}`
          );
        }
      ),
      { numRuns: 5 }
    );
  });
});
