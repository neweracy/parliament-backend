'use strict';

/**
 * Property tests for the Records API.
 *
 * Property 5: Create record round-trip
 * Property 6: Partial update field isolation
 *
 * Uses fast-check to generate arbitrary record objects and field subsets,
 * then exercises the actual route handler via supertest + express with a mock db.
 *
 * Validates: Requirements 3.1, 3.3, 2.5
 *
 * @module test/properties/records.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const recordsRoutes = require('../../routes/records');

// ─── Constants ───────────────────────────────────────────────────────────────

const VALID_LANGUAGES = ['English', 'Twi', 'Ga', 'Ewe', 'Hausa'];
const VALID_VISIBILITIES = ['Public', 'Internal', 'Restricted'];
// Exclude 'Published' from status options to avoid transcript validation check
const VALID_STATUSES_FOR_PATCH = ['Transcribing', 'Draft', 'Editing', 'Under Review', 'Certified'];

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generator for an ISO date string in YYYY-MM-DD format within a realistic range.
 */
const isoDateArb = fc.integer({ min: 0, max: 3650 }).map(dayOffset => {
  const base = new Date('2020-01-01');
  base.setDate(base.getDate() + dayOffset);
  return base.toISOString().slice(0, 10);
});

/**
 * Generator for a time string in HH:MM format.
 */
const timeArb = fc.tuple(
  fc.integer({ min: 0, max: 23 }),
  fc.integer({ min: 0, max: 59 })
).map(([h, m]) => `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);

/**
 * Generator for a valid CreateRecordDTO body.
 */
const createRecordDtoArb = fc.record({
  title: fc.string({ minLength: 1, maxLength: 80 }).filter(s => s.trim().length > 0),
  date: isoDateArb,
  language: fc.option(fc.constantFrom(...VALID_LANGUAGES), { nil: undefined }),
  startTime: fc.option(timeArb, { nil: undefined }),
  endTime: fc.option(timeArb, { nil: undefined }),
  description: fc.option(fc.string({ minLength: 1, maxLength: 200 }).filter(s => s.trim().length > 0), { nil: undefined }),
  visibility: fc.option(fc.constantFrom(...VALID_VISIBILITIES), { nil: undefined }),
});

/**
 * Generator for a non-empty strict subset of mutable record fields.
 * Each field, if included, gets a fresh random value.
 */
const patchFieldsArb = fc.record({
  title: fc.option(fc.string({ minLength: 1, maxLength: 80 }).filter(s => s.trim().length > 0), { nil: undefined }),
  status: fc.option(fc.constantFrom(...VALID_STATUSES_FOR_PATCH), { nil: undefined }),
  assigneeName: fc.option(fc.string({ minLength: 1, maxLength: 60 }).filter(s => s.trim().length > 0), { nil: undefined }),
  assigneeAvatar: fc.option(fc.string({ minLength: 1, maxLength: 200 }).filter(s => s.trim().length > 0), { nil: undefined }),
  assigneeRole: fc.option(fc.string({ minLength: 1, maxLength: 60 }).filter(s => s.trim().length > 0), { nil: undefined }),
  visibility: fc.option(fc.constantFrom(...VALID_VISIBILITIES), { nil: undefined }),
}).filter(patch => {
  // Ensure at least one field is present
  return Object.values(patch).some(v => v !== undefined);
});

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

const SITTING_ID = '1';

/**
 * Creates a mock DB for the create record round-trip test.
 * - Sitting check returns a valid sitting
 * - INSERT...RETURNING * simulates inserting and returning a record row
 * - SELECT by id returns the stored row
 */
function createRecordRoundTripDb() {
  let storedRow = null;
  let nextId = 1;

  return {
    query(text, params) {
      // Check sitting exists
      if (text.includes('FROM sitting WHERE id')) {
        return Promise.resolve({ rows: [{ id: SITTING_ID }] });
      }

      // INSERT INTO hansard_record ... RETURNING *
      if (text.includes('INSERT INTO hansard_record')) {
        const now = new Date().toISOString();
        storedRow = {
          id: nextId++,
          sitting_id: params[0],
          title: params[1],
          date: params[2],
          language: params[3],
          start_time: params[4],
          end_time: params[5],
          description: params[6],
          visibility: params[7],
          duration: null,
          duration_hours: null,
          audio_file_name: null,
          audio_path: null,
          status: 'Draft',
          progress: 0,
          assignee_name: null,
          assignee_avatar: null,
          assignee_role: null,
          error: null,
          created_at: now,
          updated_at: now,
        };
        return Promise.resolve({ rows: [storedRow] });
      }

      // SELECT * FROM hansard_record WHERE id = $1 AND sitting_id = $2
      if (text.includes('FROM hansard_record WHERE id')) {
        if (storedRow && String(storedRow.id) === String(params[0]) && String(storedRow.sitting_id) === String(params[1])) {
          return Promise.resolve({ rows: [storedRow] });
        }
        return Promise.resolve({ rows: [] });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

/**
 * Creates a mock DB for the partial update field isolation test.
 * Starts with a known record, supports PATCH via UPDATE...RETURNING *.
 */
function createPatchDb(initialRecord) {
  const record = { ...initialRecord };

  return {
    query(text, params) {
      // Check sitting exists
      if (text.includes('FROM sitting WHERE id')) {
        return Promise.resolve({ rows: [{ id: SITTING_ID }] });
      }

      // Transcript check for Published status - not needed since we exclude Published
      if (text.includes('FROM transcript WHERE record_id')) {
        return Promise.resolve({ rows: [] });
      }

      // UPDATE hansard_record SET ... WHERE id = ... AND sitting_id = ... RETURNING *
      if (text.includes('UPDATE hansard_record SET')) {
        // Parse the SET clause to apply updates
        const setClauses = text.match(/SET (.+?) WHERE/);
        if (setClauses) {
          const setStr = setClauses[1];
          const assignments = setStr.split(', ');
          let paramIdx = 0;

          for (const assignment of assignments) {
            const col = assignment.split(' = ')[0].trim();
            if (col === 'updated_at') {
              record.updated_at = new Date().toISOString();
              continue;
            }
            const value = params[paramIdx++];
            record[col] = value;
          }
        }

        // The last two params are id and sitting_id for the WHERE clause
        return Promise.resolve({ rows: [record] });
      }

      // SELECT by id for GET after PATCH
      if (text.includes('FROM hansard_record WHERE id')) {
        return Promise.resolve({ rows: [record] });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 3.1**
 */
describe('Property 5: Create record round-trip', () => {
  it('POST then GET yields matching fields with server-generated ID', async () => {
    await fc.assert(
      fc.asyncProperty(createRecordDtoArb, async (dto) => {
        const db = createRecordRoundTripDb();
        const app = express();
        app.use(recordsRoutes(passThrough, db));

        // POST to create a record
        const postRes = await request(app)
          .post(`/api/sittings/${SITTING_ID}/records`)
          .send(dto);

        assert.equal(postRes.status, 201, `Expected 201, got ${postRes.status}: ${JSON.stringify(postRes.body)}`);

        const created = postRes.body;

        // Verify server-generated ID is present and not in the input
        assert.ok(created.id !== undefined && created.id !== null, 'Response must have a server-generated id');
        assert.ok(!('id' in dto), 'Input DTO should not contain an id field');

        // Verify sittingId association
        assert.equal(String(created.sittingId), SITTING_ID, 'Record must be associated with the correct sitting');

        // GET the created record by ID
        const getRes = await request(app)
          .get(`/api/sittings/${SITTING_ID}/records/${created.id}`);

        assert.equal(getRes.status, 200, `Expected 200, got ${getRes.status}`);

        const fetched = getRes.body;

        // Assert all input fields match the fetched response
        assert.equal(fetched.title, dto.title, 'title mismatch');
        assert.equal(fetched.date, dto.date, 'date mismatch');
        assert.equal(fetched.language, dto.language || 'English', 'language mismatch');
        assert.equal(fetched.startTime, dto.startTime || null, 'startTime mismatch');
        assert.equal(fetched.endTime, dto.endTime || null, 'endTime mismatch');
        assert.equal(fetched.description, dto.description || null, 'description mismatch');
        assert.equal(fetched.visibility, dto.visibility || 'Public', 'visibility mismatch');

        // Verify the id matches between POST response and GET response
        assert.equal(fetched.id, created.id, 'GET id should match POST id');
        assert.equal(String(fetched.sittingId), SITTING_ID, 'sittingId should match');
      }),
      { numRuns: 50 }
    );
  });
});

/**
 * **Validates: Requirements 3.3, 2.5**
 */
describe('Property 6: Partial update field isolation', () => {
  it('PATCH changes only specified fields, all other fields remain unchanged', async () => {
    await fc.assert(
      fc.asyncProperty(patchFieldsArb, async (patchDto) => {
        // Create a known initial record state
        const now = new Date().toISOString();
        const initialRecord = {
          id: 42,
          sitting_id: SITTING_ID,
          title: 'Original Title',
          date: '2024-03-15',
          duration: '01:30:00',
          duration_hours: 1.5,
          language: 'English',
          audio_file_name: 'session.mp3',
          audio_path: '/audio/session.mp3',
          status: 'Draft',
          progress: 25,
          visibility: 'Internal',
          assignee_name: 'John Doe',
          assignee_avatar: 'https://example.com/avatar.png',
          assignee_role: 'Editor',
          start_time: '09:00',
          end_time: '10:30',
          description: 'A test record',
          error: null,
          created_at: now,
          updated_at: now,
        };

        const db = createPatchDb(initialRecord);
        const app = express();
        app.use(recordsRoutes(passThrough, db));

        // Take a snapshot of mutable field values before patching
        const beforePatch = {
          title: initialRecord.title,
          status: initialRecord.status,
          assigneeName: initialRecord.assignee_name,
          assigneeAvatar: initialRecord.assignee_avatar,
          assigneeRole: initialRecord.assignee_role,
          visibility: initialRecord.visibility,
        };

        // Non-mutable fields that should never change via PATCH
        const immutableFields = {
          id: initialRecord.id,
          date: initialRecord.date,
          duration: initialRecord.duration,
          durationHours: initialRecord.duration_hours,
          language: initialRecord.language,
          audioFileName: initialRecord.audio_file_name,
          audioPath: initialRecord.audio_path,
          progress: initialRecord.progress,
          startTime: initialRecord.start_time,
          endTime: initialRecord.end_time,
          description: initialRecord.description,
          error: initialRecord.error,
          createdAt: initialRecord.created_at,
        };

        // PATCH with only the generated subset of fields
        const patchRes = await request(app)
          .patch(`/api/sittings/${SITTING_ID}/records/${initialRecord.id}`)
          .send(patchDto);

        assert.equal(patchRes.status, 200, `Expected 200, got ${patchRes.status}: ${JSON.stringify(patchRes.body)}`);

        const patched = patchRes.body;

        // Assert patched fields have the new values
        if (patchDto.title !== undefined) {
          assert.equal(patched.title, patchDto.title, 'patched title should match new value');
        }
        if (patchDto.status !== undefined) {
          assert.equal(patched.status, patchDto.status, 'patched status should match new value');
        }
        if (patchDto.assigneeName !== undefined) {
          assert.equal(patched.assigneeName, patchDto.assigneeName, 'patched assigneeName should match new value');
        }
        if (patchDto.assigneeAvatar !== undefined) {
          assert.equal(patched.assigneeAvatar, patchDto.assigneeAvatar, 'patched assigneeAvatar should match new value');
        }
        if (patchDto.assigneeRole !== undefined) {
          assert.equal(patched.assigneeRole, patchDto.assigneeRole, 'patched assigneeRole should match new value');
        }
        if (patchDto.visibility !== undefined) {
          assert.equal(patched.visibility, patchDto.visibility, 'patched visibility should match new value');
        }

        // Assert fields NOT in the patch remain unchanged
        if (patchDto.title === undefined) {
          assert.equal(patched.title, beforePatch.title, 'title should remain unchanged when not patched');
        }
        if (patchDto.status === undefined) {
          assert.equal(patched.status, beforePatch.status, 'status should remain unchanged when not patched');
        }
        if (patchDto.assigneeName === undefined) {
          assert.equal(patched.assigneeName, beforePatch.assigneeName, 'assigneeName should remain unchanged when not patched');
        }
        if (patchDto.assigneeAvatar === undefined) {
          assert.equal(patched.assigneeAvatar, beforePatch.assigneeAvatar, 'assigneeAvatar should remain unchanged when not patched');
        }
        if (patchDto.assigneeRole === undefined) {
          assert.equal(patched.assigneeRole, beforePatch.assigneeRole, 'assigneeRole should remain unchanged when not patched');
        }
        if (patchDto.visibility === undefined) {
          assert.equal(patched.visibility, beforePatch.visibility, 'visibility should remain unchanged when not patched');
        }

        // Assert immutable fields are never changed by a PATCH
        assert.equal(patched.id, immutableFields.id, 'id should never change');
        assert.equal(patched.date, immutableFields.date, 'date should never change');
        assert.equal(patched.duration, immutableFields.duration, 'duration should never change');
        assert.equal(patched.durationHours, immutableFields.durationHours, 'durationHours should never change');
        assert.equal(patched.language, immutableFields.language, 'language should never change');
        assert.equal(patched.audioFileName, immutableFields.audioFileName, 'audioFileName should never change');
        assert.equal(patched.audioPath, immutableFields.audioPath, 'audioPath should never change');
        assert.equal(patched.progress, immutableFields.progress, 'progress should never change');
        assert.equal(patched.startTime, immutableFields.startTime, 'startTime should never change');
        assert.equal(patched.endTime, immutableFields.endTime, 'endTime should never change');
        assert.equal(patched.description, immutableFields.description, 'description should never change');
        assert.equal(patched.error, immutableFields.error, 'error should never change');
        assert.equal(patched.createdAt, immutableFields.createdAt, 'createdAt should never change');
      }),
      { numRuns: 50 }
    );
  });
});
