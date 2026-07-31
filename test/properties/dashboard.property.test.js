'use strict';

/**
 * Property tests for Dashboard statistics consistency.
 *
 * Property 20: Dashboard statistics consistency
 * - totalSittings == count(sittings)
 * - totalRecords == count(records)
 * - sum(recordsByStatus values) == totalRecords
 * - totalTranscriptionHours == sum(record.duration_hours)
 *
 * Validates: Requirements 13.1
 *
 * @module test/properties/dashboard.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const dashboardRoutes = require('../../routes/dashboard');

// ─── Constants ───────────────────────────────────────────────────────────────

const VALID_STATUSES = ['Transcribing', 'Draft', 'Editing', 'Under Review', 'Certified', 'Published'];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware for testing.
 */
function passThrough(req, res, next) { next(); }

/**
 * Generates a list of mock sittings.
 */
const sittingArb = fc.record({
  id: fc.nat(),
});

/**
 * Generates a mock record with a status and duration_hours.
 */
const recordArb = fc.record({
  id: fc.nat(),
  status: fc.constantFrom(...VALID_STATUSES),
  duration_hours: fc.oneof(
    fc.constant(null),
    fc.double({ min: 0, max: 100, noNaN: true, noDefaultInfinity: true })
  ),
  created_at: fc.date({ min: new Date(Date.now() - 12 * 7 * 24 * 60 * 60 * 1000), max: new Date() }),
});

/**
 * Creates a mock DB that returns deterministic results based on input arrays.
 *
 * @param {Array} sittings - Array of sitting objects
 * @param {Array} records - Array of record objects with status and duration_hours
 * @returns {Object} mock DB with query function
 */
function createMockDb(sittings, records) {
  return {
    query(text) {
      // Match query pattern to return appropriate mock data
      if (text.includes('COUNT(*)') && text.includes('FROM sitting')) {
        return Promise.resolve({ rows: [{ total: String(sittings.length) }] });
      }

      if (text.includes('COUNT(*)') && text.includes('FROM hansard_record') && !text.includes('GROUP BY')) {
        return Promise.resolve({ rows: [{ total: String(records.length) }] });
      }

      if (text.includes('GROUP BY status')) {
        const statusCounts = {};
        for (const r of records) {
          statusCounts[r.status] = (statusCounts[r.status] || 0) + 1;
        }
        const rows = Object.entries(statusCounts).map(([status, count]) => ({
          status,
          count: String(count),
        }));
        return Promise.resolve({ rows });
      }

      if (text.includes('SUM(duration_hours)')) {
        const total = records.reduce((sum, r) => sum + (r.duration_hours || 0), 0);
        return Promise.resolve({ rows: [{ total: String(total) }] });
      }

      if (text.includes("date_trunc('week'")) {
        // Group records by week
        const weekMap = {};
        const twelveWeeksAgo = Date.now() - 12 * 7 * 24 * 60 * 60 * 1000;
        for (const r of records) {
          const ts = r.created_at.getTime();
          if (ts >= twelveWeeksAgo) {
            // Truncate to week start (Monday)
            const d = new Date(ts);
            const day = d.getDay();
            const diff = d.getDate() - day + (day === 0 ? -6 : 1);
            const weekStart = new Date(d.setDate(diff));
            weekStart.setHours(0, 0, 0, 0);
            const key = weekStart.toISOString();
            weekMap[key] = (weekMap[key] || 0) + 1;
          }
        }
        const rows = Object.entries(weekMap)
          .map(([week, count]) => ({ week: new Date(week), count: String(count) }))
          .sort((a, b) => a.week - b.week);
        return Promise.resolve({ rows });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 13.1**
 */
describe('Property 20: Dashboard statistics consistency', () => {
  it('totalSittings equals count of sittings in database', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(sittingArb, { minLength: 0, maxLength: 50 }),
        fc.array(recordArb, { minLength: 0, maxLength: 50 }),
        async (sittings, records) => {
          const db = createMockDb(sittings, records);
          const app = express();
          app.use(dashboardRoutes(passThrough, db));

          const res = await request(app).get('/api/dashboard/stats');

          assert.equal(res.status, 200);
          assert.equal(
            res.body.totalSittings, sittings.length,
            `totalSittings (${res.body.totalSittings}) should equal count(sittings) (${sittings.length})`
          );
        }
      ),
      { numRuns: 50 }
    );
  });

  it('totalRecords equals count of records in database', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(sittingArb, { minLength: 0, maxLength: 50 }),
        fc.array(recordArb, { minLength: 0, maxLength: 50 }),
        async (sittings, records) => {
          const db = createMockDb(sittings, records);
          const app = express();
          app.use(dashboardRoutes(passThrough, db));

          const res = await request(app).get('/api/dashboard/stats');

          assert.equal(res.status, 200);
          assert.equal(
            res.body.totalRecords, records.length,
            `totalRecords (${res.body.totalRecords}) should equal count(records) (${records.length})`
          );
        }
      ),
      { numRuns: 50 }
    );
  });

  it('sum of recordsByStatus values equals totalRecords', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(sittingArb, { minLength: 0, maxLength: 20 }),
        fc.array(recordArb, { minLength: 0, maxLength: 50 }),
        async (sittings, records) => {
          const db = createMockDb(sittings, records);
          const app = express();
          app.use(dashboardRoutes(passThrough, db));

          const res = await request(app).get('/api/dashboard/stats');

          assert.equal(res.status, 200);

          const statusSum = Object.values(res.body.recordsByStatus)
            .reduce((sum, count) => sum + count, 0);

          assert.equal(
            statusSum, res.body.totalRecords,
            `sum(recordsByStatus) (${statusSum}) should equal totalRecords (${res.body.totalRecords})`
          );
        }
      ),
      { numRuns: 50 }
    );
  });

  it('totalTranscriptionHours equals sum of record.duration_hours', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(sittingArb, { minLength: 0, maxLength: 10 }),
        fc.array(recordArb, { minLength: 0, maxLength: 50 }),
        async (sittings, records) => {
          const db = createMockDb(sittings, records);
          const app = express();
          app.use(dashboardRoutes(passThrough, db));

          const res = await request(app).get('/api/dashboard/stats');

          assert.equal(res.status, 200);

          const expectedHours = records.reduce((sum, r) => sum + (r.duration_hours || 0), 0);

          // Use approximate equality due to floating point
          const diff = Math.abs(res.body.totalTranscriptionHours - expectedHours);
          assert.ok(
            diff < 0.0001,
            `totalTranscriptionHours (${res.body.totalTranscriptionHours}) should approximately equal sum(duration_hours) (${expectedHours}), diff=${diff}`
          );
        }
      ),
      { numRuns: 50 }
    );
  });
});
