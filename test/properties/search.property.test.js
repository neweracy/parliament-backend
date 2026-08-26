'use strict';

/**
 * Property tests for the Search API limit enforcement.
 *
 * Property 16: Search limit enforcement
 * - response ≤ min(L, 50) results
 * - default 10 when no limit specified
 * - values >50 are clamped to 50
 *
 * Validates: Requirements 8.1
 *
 * @module test/properties/search.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const searchRoutes = require('../../routes/search');

// ─── Constants ───────────────────────────────────────────────────────────────

const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 50;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware for testing.
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
 * Creates a mock DB that returns empty results for suggestions queries.
 * (Search itself is proxied to the Postprocessing Service, so we mock fetch.)
 */
function createMockDb() {
  return {
    query() {
      return Promise.resolve({ rows: [] });
    },
  };
}

/**
 * Creates a test app that mounts the search routes and intercepts fetch calls
 * to the Postprocessing Service. The mock fetch returns N synthetic results
 * (up to the requested limit) so we can verify the Gateway's limit enforcement.
 *
 * @param {number} _corpusSize - Total available results the "backend" has (unused, fetch mock handles this)
 * @returns {{ app: import('express').Express, capturedLimits: number[] }}
 */
function _createTestApp(_corpusSize) {
  const capturedLimits = [];
  const db = createMockDb();
  const app = express();
  app.use(express.json());

  // We need to intercept the fetch to POSTPROCESS_URL/rag/search.
  // Since the route uses global fetch, we'll override it on the app level
  // by providing our own route handler that captures and validates.
  // Instead, we'll mock global.fetch for the test.

  app.use(searchRoutes(passThrough, db));
  return { app, capturedLimits };
}

/**
 * Creates a mock fetch that captures the limit sent to the RAG service
 * and returns a corresponding number of results.
 *
 * @param {number} corpusSize - Max results the "backend" will return
 * @returns {{ mockFetch: Function, getCapturedLimits: () => number[] }}
 */
function createMockFetch(corpusSize) {
  const capturedLimits = [];
  const originalFetch = global.fetch;

  function mockFetch(url, options) {
    if (url.includes('/rag/search')) {
      const body = JSON.parse(options.body);
      const requestedLimit = body.limit;
      capturedLimits.push(requestedLimit);

      // Simulate backend returning min(requestedLimit, corpusSize) results
      const resultCount = Math.min(requestedLimit, corpusSize);
      const results = Array.from({ length: resultCount }, (_, i) => ({
        chunkText: `Chunk ${i}`,
        relevanceScore: 1 - i * 0.01,
        transcriptId: i + 1,
        speaker: 'Speaker A',
        startS: i * 10,
        endS: (i + 1) * 10,
        matchedEntities: [],
      }));

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          results,
          totalMatched: corpusSize,
          latencyMs: 50,
        }),
      });
    }

    // Fall through to real fetch for other URLs
    return originalFetch(url, options);
  }

  return {
    mockFetch,
    getCapturedLimits: () => capturedLimits,
    restore: () => { global.fetch = originalFetch; },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 8.1**
 */
describe('Property 16: Search limit enforcement', () => {
  it('response contains at most min(L, 50) results when limit L is provided', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 200 }),  // limit value from client
        fc.integer({ min: 0, max: 100 }),   // corpus size
        async (limit, corpusSize) => {
          const { mockFetch, getCapturedLimits, restore } = createMockFetch(corpusSize);
          global.fetch = mockFetch;

          try {
            const db = createMockDb();
            const app = express();
            app.use(express.json());
            app.use(searchRoutes(passThrough, db));

            const res = await request(app)
              .post('/api/search')
              .send({ query: 'test query', limit });

            assert.equal(res.status, 200);

            const effectiveLimit = Math.min(limit, MAX_LIMIT);

            // Response must not exceed min(L, 50) results
            assert.ok(
              res.body.results.length <= effectiveLimit,
              `Expected at most ${effectiveLimit} results (min(${limit}, ${MAX_LIMIT})), got ${res.body.results.length}`
            );

            // The limit sent to the backend should be clamped to max 50
            const capturedLimits = getCapturedLimits();
            const sentLimit = capturedLimits[capturedLimits.length - 1];
            assert.ok(
              sentLimit <= MAX_LIMIT,
              `Limit sent to backend (${sentLimit}) should be ≤ ${MAX_LIMIT}`
            );
            assert.equal(
              sentLimit, effectiveLimit,
              `Limit sent to backend should be ${effectiveLimit}, got ${sentLimit}`
            );
          } finally {
            restore();
          }
        }
      ),
      { numRuns: 50 }
    );
  });

  it('defaults to 10 results when no limit is specified', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 10, max: 100 }),  // corpus with at least 10 results
        async (corpusSize) => {
          const { mockFetch, getCapturedLimits, restore } = createMockFetch(corpusSize);
          global.fetch = mockFetch;

          try {
            const db = createMockDb();
            const app = express();
            app.use(express.json());
            app.use(searchRoutes(passThrough, db));

            const res = await request(app)
              .post('/api/search')
              .send({ query: 'test query' });  // no limit field

            assert.equal(res.status, 200);

            // Should default to 10
            const capturedLimits = getCapturedLimits();
            const sentLimit = capturedLimits[capturedLimits.length - 1];
            assert.equal(
              sentLimit, DEFAULT_LIMIT,
              `Default limit sent to backend should be ${DEFAULT_LIMIT}, got ${sentLimit}`
            );

            assert.ok(
              res.body.results.length <= DEFAULT_LIMIT,
              `Expected at most ${DEFAULT_LIMIT} results by default, got ${res.body.results.length}`
            );
          } finally {
            restore();
          }
        }
      ),
      { numRuns: 20 }
    );
  });

  it('clamps limit values >50 to 50', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 51, max: 1000 }),  // limit > 50
        fc.integer({ min: 50, max: 200 }),   // large corpus
        async (limit, corpusSize) => {
          const { mockFetch, getCapturedLimits, restore } = createMockFetch(corpusSize);
          global.fetch = mockFetch;

          try {
            const db = createMockDb();
            const app = express();
            app.use(express.json());
            app.use(searchRoutes(passThrough, db));

            const res = await request(app)
              .post('/api/search')
              .send({ query: 'test query', limit });

            assert.equal(res.status, 200);

            // The limit sent to backend must be clamped to 50
            const capturedLimits = getCapturedLimits();
            const sentLimit = capturedLimits[capturedLimits.length - 1];
            assert.equal(
              sentLimit, MAX_LIMIT,
              `Limit >50 (${limit}) should be clamped to ${MAX_LIMIT}, got ${sentLimit}`
            );

            // Response results should not exceed 50
            assert.ok(
              res.body.results.length <= MAX_LIMIT,
              `Expected at most ${MAX_LIMIT} results, got ${res.body.results.length}`
            );
          } finally {
            restore();
          }
        }
      ),
      { numRuns: 30 }
    );
  });
});
