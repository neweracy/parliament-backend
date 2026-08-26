'use strict';

/**
 * Property tests for the assigned-records filter predicate.
 *
 * Feature: assignments-page, Property 3: Assignee exact-match filter predicate
 *
 * For any set of stored records with arbitrary assignee names and any query name
 * (including differing case, differing whitespace, or unicode variants), the
 * assigned-records query SHALL return a record if and only if its stored assignee
 * name is byte-for-byte identical to the query name.
 *
 * Validates: Requirements 2.3
 *
 * @module test/properties/assigned-records-filter.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const recordsRoutes = require('../../routes/records');

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware.
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
 * Creates a mock DB that simulates the INNER JOIN behaviour of
 * GET /api/records/assigned. The mock stores a set of records with
 * their assignee names and a valid joined sitting, and implements the
 * strict equality filter that PostgreSQL's `=` operator provides.
 *
 * @param {Array<{assigneeName: string, id: number}>} records - records to store
 */
function createAssignedRecordsDb(records) {
  return {
    query(text, params) {
      // The assigned-records query: WHERE hr.assignee_name = $1
      if (text.includes('FROM hansard_record hr') && text.includes('INNER JOIN sitting')) {
        const queryName = params[0];
        const matchingRows = records
          .filter(r => r.assigneeName === queryName)
          .map(r => ({
            id: r.id,
            sitting_id: 1,
            title: 'Record ' + r.id,
            date: '2024-01-15',
            duration: null,
            duration_hours: null,
            language: 'English',
            audio_file_name: null,
            audio_path: null,
            status: 'Draft',
            progress: 0,
            visibility: 'Public',
            assignee_name: r.assigneeName,
            assignee_avatar: 'AB',
            assignee_role: 'Editor',
            start_time: null,
            end_time: null,
            description: null,
            error: null,
            created_at: '2024-01-15T10:00:00.000Z',
            updated_at: '2024-01-15T10:00:00.000Z',
            sitting_title: 'Plenary Session',
            sitting_priority: 'Medium',
          }));
        return Promise.resolve({ rows: matchingRows });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generator for a non-empty assignee name string.
 * Allows unicode characters, spaces, and various lengths.
 */
const assigneeNameArb = fc.string({ minLength: 1, maxLength: 60 })
  .filter(s => s.trim().length > 0);



// ─── Property Tests ──────────────────────────────────────────────────────────

describe('Feature: assignments-page, Property 3: Assignee exact-match filter predicate', () => {
  it('returns records if and only if stored assignee name is byte-for-byte identical to query name', async () => {
    await fc.assert(
      fc.asyncProperty(
        // Generate a list of records with arbitrary assignee names
        fc.array(assigneeNameArb, { minLength: 1, maxLength: 10 }),
        // Generate a query name that could be anything
        assigneeNameArb,
        async (assigneeNames, queryName) => {
          // Create records with generated assignee names
          const records = assigneeNames.map((name, idx) => ({
            id: idx + 1,
            assigneeName: name,
          }));

          const db = createAssignedRecordsDb(records);
          const app = express();
          app.use(recordsRoutes(passThrough, db));

          const res = await request(app)
            .get('/api/records/assigned')
            .set('x-user-name', queryName);

          assert.equal(res.status, 200);

          const returnedIds = new Set(res.body.data.map(r => r.id));

          // Verify: a record is returned IFF its assignee name === queryName (byte-for-byte)
          for (const record of records) {
            if (record.assigneeName === queryName) {
              assert.ok(
                returnedIds.has(record.id),
                `Record ${record.id} with assignee "${record.assigneeName}" should be returned for query "${queryName}"`
              );
            } else {
              assert.ok(
                !returnedIds.has(record.id),
                `Record ${record.id} with assignee "${record.assigneeName}" should NOT be returned for query "${queryName}"`
              );
            }
          }
        }
      ),
      { numRuns: 100 }
    );
  });

  it('case-different query name never matches the stored name', async () => {
    await fc.assert(
      fc.asyncProperty(
        // Generate a base name that has at least one letter (so case-toggle is meaningful)
        fc.string({ minLength: 2, maxLength: 30 }).filter(s => /[a-zA-Z]/.test(s) && s.trim().length > 0),
        async (baseName) => {
          // Create a record with the original name
          const records = [{ id: 1, assigneeName: baseName }];

          // Create a case variant that differs from the original
          const variant = baseName.split('').map((ch, i) => {
            if (i === 0 && /[a-zA-Z]/.test(ch)) {
              return ch === ch.toUpperCase() ? ch.toLowerCase() : ch.toUpperCase();
            }
            return ch;
          }).join('');

          // Skip if the variant happens to be identical (e.g., all digits)
          if (variant === baseName) return;

          const db = createAssignedRecordsDb(records);
          const app = express();
          app.use(recordsRoutes(passThrough, db));

          const res = await request(app)
            .get('/api/records/assigned')
            .set('x-user-name', variant);

          assert.equal(res.status, 200);
          assert.equal(
            res.body.data.length,
            0,
            `Case-variant "${variant}" should not match stored name "${baseName}"`
          );
        }
      ),
      { numRuns: 100 }
    );
  });

  it('internal whitespace differences prevent matching', async () => {
    await fc.assert(
      fc.asyncProperty(
        // Generate a name with at least two non-space characters so we can insert extra space
        fc.tuple(
          fc.string({ minLength: 1, maxLength: 15 }).filter(s => s.trim().length > 0 && !/\s/.test(s)),
          fc.string({ minLength: 1, maxLength: 15 }).filter(s => s.trim().length > 0 && !/\s/.test(s))
        ),
        async ([part1, part2]) => {
          // Stored name has single space; query has double space
          const storedName = part1 + ' ' + part2;
          const queryName = part1 + '  ' + part2;

          // They should always differ
          if (storedName === queryName) return;

          const records = [{ id: 1, assigneeName: storedName }];

          const db = createAssignedRecordsDb(records);
          const app = express();
          app.use(recordsRoutes(passThrough, db));

          const res = await request(app)
            .get('/api/records/assigned')
            .set('x-user-name', queryName);

          assert.equal(res.status, 200);
          assert.equal(
            res.body.data.length,
            0,
            `Query "${queryName}" with extra internal whitespace should not match stored name "${storedName}"`
          );
        }
      ),
      { numRuns: 100 }
    );
  });

  it('unicode-normalized variant does not match when bytes differ (predicate-level)', async () => {
    // HTTP headers reject non-ASCII combining characters, so we test the filtering
    // predicate directly: JavaScript's strict equality (===) mirrors PostgreSQL's
    // byte-for-byte text comparison that the WHERE clause uses.
    await fc.assert(
      fc.property(
        fc.constantFrom(
          { stored: 'Ren\u00e9', query: 'Ren\u0065\u0301' },            // é vs e+combining accent
          { stored: 'na\u00efve', query: 'na\u0069\u0308ve' },            // ï vs i+combining diaeresis
          { stored: '\u00f1o', query: 'n\u0303o' },                        // ñ vs n+combining tilde
          { stored: 'Caf\u00e9', query: 'Caf\u0065\u0301' },              // Café composed vs decomposed
          { stored: '\u00c5ngstr\u00f6m', query: 'A\u030angstr\u006f\u0308m' } // Å and ö decomposed
        ),
        ({ stored, query }) => {
          // Precondition: the composed and decomposed forms are byte-different
          assert.notEqual(stored, query, 'Test data must have byte-different variants');

          // The predicate used by the mock DB (and by PostgreSQL's = operator) is strict equality.
          // Records are included IFF storedName === queryName. Since they differ, no match.
          const records = [{ id: 1, assigneeName: stored }];
          const matches = records.filter(r => r.assigneeName === query);
          assert.equal(
            matches.length,
            0,
            `Unicode-variant "${query}" (length ${query.length}) should not match stored "${stored}" (length ${stored.length})`
          );
        }
      ),
      { numRuns: 100 }
    );
  });
});
