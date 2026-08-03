'use strict';

/**
 * Property tests for the assigned records formatting.
 *
 * Feature: assignments-page, Property 4: Assigned-record formatting completeness
 *
 * For any joined (hansard_record, sitting) row pair, the formatted response item
 * SHALL have a non-empty string sittingTitle equal to the sitting's title and a
 * sittingPriority equal to the sitting's priority, which SHALL always be one of
 * High, Medium, or Low.
 *
 * **Validates: Requirements 2.4**
 *
 * @module test/properties/assigned-records-format.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const recordsRoutes = require('../../routes/records');

// ─── Constants ───────────────────────────────────────────────────────────────

const VALID_PRIORITIES = ['High', 'Medium', 'Low'];
const USER_NAME = 'Test User';

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generator for a non-empty sitting title string.
 */
const sittingTitleArb = fc.string({ minLength: 1, maxLength: 200 }).filter(s => s.trim().length > 0);

/**
 * Generator for a valid sitting priority.
 */
const sittingPriorityArb = fc.constantFrom(...VALID_PRIORITIES);

/**
 * Generator for a complete joined row (hansard_record + sitting fields)
 * as would be returned from the INNER JOIN query.
 */
const joinedRowArb = fc.record({
  // hansard_record fields
  id: fc.integer({ min: 1, max: 100000 }),
  sitting_id: fc.integer({ min: 1, max: 100000 }),
  title: fc.string({ minLength: 1, maxLength: 100 }).filter(s => s.trim().length > 0),
  date: fc.constant('2024-03-15'),
  duration: fc.constant(null),
  duration_hours: fc.constant(null),
  language: fc.constantFrom('English', 'Twi', 'Ga'),
  audio_file_name: fc.constant(null),
  audio_path: fc.constant(null),
  status: fc.constantFrom('Draft', 'Editing', 'Under Review', 'Transcribing'),
  progress: fc.integer({ min: 0, max: 100 }),
  visibility: fc.constantFrom('Public', 'Internal', 'Restricted'),
  assignee_name: fc.constant(USER_NAME),
  assignee_avatar: fc.constant('TU'),
  assignee_role: fc.constant('Editor'),
  start_time: fc.constant(null),
  end_time: fc.constant(null),
  description: fc.constant(null),
  error: fc.constant(null),
  created_at: fc.constant('2024-03-15T10:00:00.000Z'),
  updated_at: fc.constant('2024-03-15T10:00:00.000Z'),
  // Joined sitting fields
  sitting_title: sittingTitleArb,
  sitting_priority: sittingPriorityArb,
});

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware.
 */
function passThrough(req, res, next) { next(); }

/**
 * Creates a mock DB that returns the provided joined rows for the assigned records query.
 */
function createAssignedRecordsDb(rows) {
  return {
    query(text, params) {
      // The INNER JOIN query for assigned records
      if (text.includes('FROM hansard_record hr') && text.includes('INNER JOIN sitting')) {
        return Promise.resolve({ rows });
      }
      return Promise.resolve({ rows: [] });
    },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * Feature: assignments-page, Property 4: Assigned-record formatting completeness
 *
 * **Validates: Requirements 2.4**
 */
describe('Property 4: Assigned-record formatting completeness', () => {
  it('formatted response item has non-empty sittingTitle equal to input and sittingPriority is one of High/Medium/Low', async () => {
    await fc.assert(
      fc.asyncProperty(joinedRowArb, async (row) => {
        const db = createAssignedRecordsDb([row]);
        const app = express();
        app.use(recordsRoutes(passThrough, db));

        const res = await request(app)
          .get('/api/records/assigned')
          .set('x-user-name', USER_NAME);

        assert.equal(res.status, 200, `Expected 200, got ${res.status}: ${JSON.stringify(res.body)}`);

        const { data } = res.body;
        assert.ok(Array.isArray(data), 'Response data must be an array');
        assert.equal(data.length, 1, 'Should have exactly one record');

        const item = data[0];

        // sittingTitle must be a non-empty string equal to the input sitting_title
        assert.equal(typeof item.sittingTitle, 'string', 'sittingTitle must be a string');
        assert.ok(item.sittingTitle.length > 0, 'sittingTitle must be non-empty');
        assert.equal(item.sittingTitle, row.sitting_title, 'sittingTitle must equal the sitting title from the joined row');

        // sittingPriority must be one of High, Medium, or Low
        assert.ok(
          VALID_PRIORITIES.includes(item.sittingPriority),
          `sittingPriority must be one of ${VALID_PRIORITIES.join(', ')}, got: ${item.sittingPriority}`
        );
        assert.equal(item.sittingPriority, row.sitting_priority, 'sittingPriority must equal the sitting priority from the joined row');
      }),
      { numRuns: 100 }
    );
  });
});
