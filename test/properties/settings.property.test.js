'use strict';

/**
 * Property tests for Settings validation.
 *
 * Property 21: Settings validation
 * - accept iff value ∈ allowed set for each field
 * - transcription_engine ∈ {'deepgram', 'khaya', 'hybrid'}
 * - default_language ∈ {'en', 'tw', 'ga', 'ee', 'ha'}
 * - auto_save_interval_s > 0
 *
 * Validates: Requirements 14.4
 *
 * @module test/properties/settings.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const settingsRoutes = require('../../routes/settings');

// ─── Constants ───────────────────────────────────────────────────────────────

const VALID_ENGINES = ['deepgram', 'khaya', 'hybrid'];
const VALID_LANGUAGES = ['en', 'tw', 'ga', 'ee', 'ha'];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware for testing.
 */
function passThrough(req, res, next) { next(); }

/**
 * Creates a mock DB that simulates the app_settings singleton row.
 * Tracks the last "updated" state so we can verify PATCH behaviour.
 */
function createMockDb() {
  const currentSettings = {
    id: 1,
    transcription_engine: 'deepgram',
    default_language: 'en',
    custom_dictionary: '[]',
    auto_save_interval_s: 30,
    updated_at: new Date().toISOString(),
  };

  return {
    query(text, params) {
      // GET settings
      if (text.includes('SELECT') && text.includes('app_settings') && text.includes('WHERE id = 1')) {
        return Promise.resolve({ rows: [{ ...currentSettings }] });
      }

      // UPDATE settings
      if (text.includes('UPDATE app_settings')) {
        // Parse SET clauses from the text to determine which fields are being updated
        // The params array contains values in order
        let paramIdx = 0;

        if (text.includes('transcription_engine')) {
          currentSettings.transcription_engine = params[paramIdx++];
        }
        if (text.includes('default_language')) {
          currentSettings.default_language = params[paramIdx++];
        }
        if (text.includes('custom_dictionary')) {
          currentSettings.custom_dictionary = params[paramIdx++];
          paramIdx++;
        }
        if (text.includes('auto_save_interval_s')) {
          currentSettings.auto_save_interval_s = params[paramIdx++];
        }

        currentSettings.updated_at = new Date().toISOString();
        return Promise.resolve({ rows: [{ ...currentSettings }] });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

/**
 * Creates an Express app with the settings routes mounted.
 */
function createTestApp() {
  const db = createMockDb();
  const app = express();
  app.use(express.json());
  app.use(settingsRoutes(passThrough, db));
  return app;
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 14.4**
 */
describe('Property 21: Settings validation', () => {
  it('accepts transcriptionEngine iff value ∈ {deepgram, khaya, hybrid}', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 30 }),
        async (engine) => {
          const app = createTestApp();

          const res = await request(app)
            .patch('/api/settings')
            .send({ transcriptionEngine: engine });

          const isValid = VALID_ENGINES.includes(engine);

          if (isValid) {
            assert.equal(
              res.status, 200,
              `Valid engine '${engine}' should be accepted (got ${res.status})`
            );
          } else {
            assert.equal(
              res.status, 422,
              `Invalid engine '${engine}' should be rejected with 422 (got ${res.status})`
            );
            assert.equal(res.body.error.code, 'VALIDATION_ERROR');
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  it('accepts all known valid transcription engines', async () => {
    for (const engine of VALID_ENGINES) {
      const app = createTestApp();

      const res = await request(app)
        .patch('/api/settings')
        .send({ transcriptionEngine: engine });

      assert.equal(
        res.status, 200,
        `Known valid engine '${engine}' should be accepted (got ${res.status})`
      );
    }
  });

  it('accepts defaultLanguage iff value ∈ {en, tw, ga, ee, ha}', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 10 }),
        async (lang) => {
          const app = createTestApp();

          const res = await request(app)
            .patch('/api/settings')
            .send({ defaultLanguage: lang });

          const isValid = VALID_LANGUAGES.includes(lang);

          if (isValid) {
            assert.equal(
              res.status, 200,
              `Valid language '${lang}' should be accepted (got ${res.status})`
            );
          } else {
            assert.equal(
              res.status, 422,
              `Invalid language '${lang}' should be rejected with 422 (got ${res.status})`
            );
            assert.equal(res.body.error.code, 'VALIDATION_ERROR');
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  it('accepts all known valid languages', async () => {
    for (const lang of VALID_LANGUAGES) {
      const app = createTestApp();

      const res = await request(app)
        .patch('/api/settings')
        .send({ defaultLanguage: lang });

      assert.equal(
        res.status, 200,
        `Known valid language '${lang}' should be accepted (got ${res.status})`
      );
    }
  });

  it('accepts autoSaveIntervalS iff value > 0', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.oneof(
          fc.double({ min: -1000, max: 1000, noNaN: true, noDefaultInfinity: true }),
          fc.integer({ min: -100, max: 100 })
        ),
        async (interval) => {
          const app = createTestApp();

          const res = await request(app)
            .patch('/api/settings')
            .send({ autoSaveIntervalS: interval });

          const isValid = typeof interval === 'number' && interval > 0;

          if (isValid) {
            assert.equal(
              res.status, 200,
              `Valid interval ${interval} should be accepted (got ${res.status})`
            );
          } else {
            assert.equal(
              res.status, 422,
              `Invalid interval ${interval} should be rejected with 422 (got ${res.status})`
            );
            assert.equal(res.body.error.code, 'VALIDATION_ERROR');
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});
