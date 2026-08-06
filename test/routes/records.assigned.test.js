'use strict';

/**
 * Assigned-records route unit/integration tests.
 *
 * Uses a mock db to test the GET /api/records/assigned route logic,
 * specifically the INNER JOIN with sitting and the response shape.
 *
 * Validates: Requirements 2.3, 2.4, 2.5
 *
 * @module test/routes/records.assigned
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const recordsRoutes = require('../../routes/records');

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

function withUser(user) {
  return function attachUser(req, res, next) {
    req.user = user;
    next();
  };
}

/**
 * Builds an Express app with the records routes using the given db mock.
 */
function buildApp(db, requireSession = withUser({ role: 'Chief Editor' })) {
  const app = express();
  app.use(express.json());
  app.use(recordsRoutes(requireSession, db));
  return app;
}

/** A sample joined row as returned by the INNER JOIN query (hansard_record + sitting fields) */
const SAMPLE_JOINED_ROW = {
  id: 10,
  sitting_id: 1,
  title: 'Morning Session',
  date: '2024-03-01',
  duration: '2h 30m',
  duration_hours: 2.5,
  language: 'English',
  audio_file_name: 'morning.wav',
  audio_path: '/audio/morning.wav',
  status: 'Draft',
  progress: 50,
  visibility: 'Public',
  assignee_name: 'Sarah Mensah',
  assignee_avatar: 'SM',
  assignee_role: 'Chief Editor',
  start_time: '09:00',
  end_time: '11:30',
  description: 'Morning budget debate',
  error: null,
  created_at: '2024-03-01T09:00:00.000Z',
  updated_at: '2024-03-01T09:00:00.000Z',
  // Joined sitting fields
  sitting_title: 'Budget Session 2024',
  sitting_priority: 'High',
};

describe('GET /api/records/assigned', () => {
  it('returns all assigned records for Admin when scope=all is requested', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_JOINED_ROW] },
    ]);
    const app = buildApp(db, withUser({ role: 'Admin' }));

    const res = await request(app)
      .get('/api/records/assigned?scope=all');

    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.data));
    assert.equal(res.body.data.length, 1);
    assert.equal(res.body.data[0].assigneeName, 'Sarah Mensah');

    const query = db.getCalls()[0];
    assert.ok(query.text.includes('WHERE hr.assignee_name IS NOT NULL'));
    assert.deepEqual(query.params, undefined);
  });

  it('returns records with sittingTitle and sittingPriority from the joined sitting (happy path)', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_JOINED_ROW] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/records/assigned')
      .set('x-user-name', 'Sarah Mensah');

    assert.equal(res.status, 200);
    assert.ok(res.body.data, 'response should have a data array');
    assert.equal(res.body.data.length, 1);

    const record = res.body.data[0];
    // Verify joined sitting fields are present
    assert.equal(record.sittingTitle, 'Budget Session 2024');
    assert.equal(record.sittingPriority, 'High');

    // Verify standard record fields are formatted correctly
    assert.equal(record.id, 10);
    assert.equal(record.sittingId, 1);
    assert.equal(record.title, 'Morning Session');
    assert.equal(record.assigneeName, 'Sarah Mensah');
    assert.equal(record.status, 'Draft');

    // Verify the SQL uses INNER JOIN and filters by assignee_name
    const query = db.getCalls()[0];
    assert.ok(query.text.includes('INNER JOIN sitting'), 'query should INNER JOIN sitting');
    assert.ok(query.text.includes('sitting_title'), 'query should select sitting_title');
    assert.ok(query.text.includes('sitting_priority'), 'query should select sitting_priority');
    assert.deepEqual(query.params, ['Sarah Mensah']);
  });

  it('excludes records whose sitting was deleted (orphaned record absent from response)', async () => {
    // The INNER JOIN naturally excludes records with no matching sitting row.
    // We simulate this by returning an empty result set — as the DB would when
    // the only matching record's sitting_id doesn't join to any sitting row.
    const db = createMockDb([
      { rows: [] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/records/assigned')
      .set('x-user-name', 'Sarah Mensah');

    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.data));
    assert.equal(res.body.data.length, 0,
      'orphaned records (no matching sitting) should not appear in the response');
  });

  it('returns 400 when x-user-name header is missing', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .get('/api/records/assigned');

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'MISSING_USER_IDENTITY');
  });

  it('passes the exact x-user-name value to the query (case-sensitive)', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    await request(app)
      .get('/api/records/assigned')
      .set('x-user-name', 'sarah mensah');

    const query = db.getCalls()[0];
    assert.deepEqual(query.params, ['sarah mensah'],
      'should pass the header value unmodified to the SQL query');
  });
});
