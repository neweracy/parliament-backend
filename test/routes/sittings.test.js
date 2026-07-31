'use strict';

/**
 * Sittings route unit tests.
 *
 * Uses a mock db to test route logic without a real PostgreSQL connection.
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
 *
 * @module test/routes/sittings
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const sittingsRoutes = require('../../routes/sittings');

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
 * A no-op requireSession middleware that always passes (simulates authenticated requests).
 */
function passThrough(req, res, next) { next(); }

/**
 * Builds an Express app with the sittings routes using the given db mock.
 */
function buildApp(db) {
  const app = express();
  app.use(express.json());
  app.use(sittingsRoutes(passThrough, db));
  return app;
}

/** A sample sitting row as returned by PostgreSQL */
const SAMPLE_SITTING_ROW = {
  id: 1,
  title: 'Budget Session 2024',
  description: 'Annual budget debate',
  session_type: 'Plenary',
  committee: null,
  presiding_officer: 'Speaker Johnson',
  parliament: '9th Parliament',
  date_from: '2024-03-01',
  date_to: '2024-03-02',
  status: 'Active',
  priority: 'High',
  participants: 275,
  topic: 'Budget Approval',
  order_paper_ref: 'OP-2024-001',
  created_at: '2024-03-01T08:00:00.000Z',
  updated_at: '2024-03-01T08:00:00.000Z',
};

/** A sample record row */
const SAMPLE_RECORD_ROW = {
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
  assignee_name: 'Jane Doe',
  assignee_avatar: null,
  assignee_role: 'Editor',
  start_time: '09:00',
  end_time: '11:30',
  description: 'Morning budget debate',
  error: null,
  created_at: '2024-03-01T09:00:00.000Z',
  updated_at: '2024-03-01T09:00:00.000Z',
};

describe('GET /api/sittings', () => {
  it('returns paginated list with default page=1, pageSize=20', async () => {
    const db = createMockDb([
      { rows: [{ total: '2' }] },
      { rows: [SAMPLE_SITTING_ROW, { ...SAMPLE_SITTING_ROW, id: 2 }] },
    ]);
    const app = buildApp(db);

    const res = await request(app).get('/api/sittings');

    assert.equal(res.status, 200);
    assert.equal(res.body.page, 1);
    assert.equal(res.body.pageSize, 20);
    assert.equal(res.body.total, 2);
    assert.equal(res.body.data.length, 2);
  });

  it('respects page and pageSize query params', async () => {
    const db = createMockDb([
      { rows: [{ total: '50' }] },
      { rows: [SAMPLE_SITTING_ROW] },
    ]);
    const app = buildApp(db);

    const res = await request(app).get('/api/sittings?page=3&pageSize=5');

    assert.equal(res.status, 200);
    assert.equal(res.body.page, 3);
    assert.equal(res.body.pageSize, 5);

    // Verify the offset in the SQL query
    const dataQuery = db.getCalls()[1];
    assert.ok(dataQuery.params.includes(5));  // pageSize (LIMIT)
    assert.ok(dataQuery.params.includes(10)); // offset = (3-1)*5
  });

  it('excludes Archived by default', async () => {
    const db = createMockDb([
      { rows: [{ total: '5' }] },
      { rows: [] },
    ]);
    const app = buildApp(db);

    await request(app).get('/api/sittings');

    const countQuery = db.getCalls()[0];
    assert.ok(countQuery.text.includes('status !='));
    assert.ok(countQuery.params.includes('Archived'));
  });

  it('applies status filter when provided', async () => {
    const db = createMockDb([
      { rows: [{ total: '1' }] },
      { rows: [{ ...SAMPLE_SITTING_ROW, status: 'Completed' }] },
    ]);
    const app = buildApp(db);

    const res = await request(app).get('/api/sittings?status=Completed');

    assert.equal(res.status, 200);
    const countQuery = db.getCalls()[0];
    assert.ok(countQuery.text.includes('status ='));
    assert.ok(countQuery.params.includes('Completed'));
  });

  it('applies sessionType filter', async () => {
    const db = createMockDb([
      { rows: [{ total: '1' }] },
      { rows: [SAMPLE_SITTING_ROW] },
    ]);
    const app = buildApp(db);

    await request(app).get('/api/sittings?sessionType=Committee');

    const countQuery = db.getCalls()[0];
    assert.ok(countQuery.text.includes('session_type ='));
    assert.ok(countQuery.params.includes('Committee'));
  });

  it('applies dateFrom and dateTo filters', async () => {
    const db = createMockDb([
      { rows: [{ total: '1' }] },
      { rows: [SAMPLE_SITTING_ROW] },
    ]);
    const app = buildApp(db);

    await request(app).get('/api/sittings?dateFrom=2024-01-01&dateTo=2024-12-31');

    const countQuery = db.getCalls()[0];
    assert.ok(countQuery.text.includes('date_from >='));
    assert.ok(countQuery.text.includes('date_to <='));
    assert.ok(countQuery.params.includes('2024-01-01'));
    assert.ok(countQuery.params.includes('2024-12-31'));
  });

  it('converts snake_case DB columns to camelCase in response', async () => {
    const db = createMockDb([
      { rows: [{ total: '1' }] },
      { rows: [SAMPLE_SITTING_ROW] },
    ]);
    const app = buildApp(db);

    const res = await request(app).get('/api/sittings');

    const sitting = res.body.data[0];
    assert.equal(sitting.sessionType, 'Plenary');
    assert.equal(sitting.presidingOfficer, 'Speaker Johnson');
    assert.equal(sitting.dateFrom, '2024-03-01');
    assert.equal(sitting.dateTo, '2024-03-02');
    assert.equal(sitting.orderPaperRef, 'OP-2024-001');
    assert.equal(sitting.createdAt, '2024-03-01T08:00:00.000Z');
    assert.equal(sitting.updatedAt, '2024-03-01T08:00:00.000Z');
  });
});

describe('POST /api/sittings', () => {
  it('creates a sitting and returns 201 with server-generated ID', async () => {
    const db = createMockDb([
      { rows: [{ ...SAMPLE_SITTING_ROW, id: 42 }] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/sittings')
      .send({
        title: 'Budget Session 2024',
        sessionType: 'Plenary',
        presidingOfficer: 'Speaker Johnson',
        dateFrom: '2024-03-01',
        dateTo: '2024-03-02',
      });

    assert.equal(res.status, 201);
    assert.equal(res.body.id, 42);
    assert.equal(res.body.title, 'Budget Session 2024');
    assert.equal(res.body.sessionType, 'Plenary');
  });

  it('returns 400 when required fields are missing', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/sittings')
      .send({ title: 'Incomplete' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'MISSING_REQUIRED_FIELDS');
  });

  it('uses default values for optional fields', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_SITTING_ROW] },
    ]);
    const app = buildApp(db);

    await request(app)
      .post('/api/sittings')
      .send({
        title: 'Test',
        sessionType: 'Plenary',
        presidingOfficer: 'Speaker',
        dateFrom: '2024-01-01',
        dateTo: '2024-01-02',
      });

    const insertCall = db.getCalls()[0];
    // priority defaults to 'Medium', participants to 0
    assert.equal(insertCall.params[8], 'Medium');
    assert.equal(insertCall.params[9], 0);
  });
});

describe('GET /api/sittings/:id', () => {
  it('returns sitting with associated records', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_SITTING_ROW] },
      { rows: [SAMPLE_RECORD_ROW] },
    ]);
    const app = buildApp(db);

    const res = await request(app).get('/api/sittings/1');

    assert.equal(res.status, 200);
    assert.equal(res.body.id, 1);
    assert.equal(res.body.title, 'Budget Session 2024');
    assert.ok(Array.isArray(res.body.records));
    assert.equal(res.body.records.length, 1);
    assert.equal(res.body.records[0].id, 10);
    assert.equal(res.body.records[0].sittingId, 1);
  });

  it('returns 404 when sitting does not exist', async () => {
    const db = createMockDb([
      { rows: [] },
    ]);
    const app = buildApp(db);

    const res = await request(app).get('/api/sittings/999');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'SITTING_NOT_FOUND');
  });
});

describe('PATCH /api/sittings/:id', () => {
  it('updates only provided fields and returns updated sitting', async () => {
    const updatedRow = { ...SAMPLE_SITTING_ROW, title: 'Updated Title', updated_at: '2024-03-02T10:00:00.000Z' };
    const db = createMockDb([
      { rows: [updatedRow] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/sittings/1')
      .send({ title: 'Updated Title' });

    assert.equal(res.status, 200);
    assert.equal(res.body.title, 'Updated Title');
  });

  it('returns 400 when no fields are provided', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/sittings/1')
      .send({});

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'NO_FIELDS_TO_UPDATE');
  });

  it('returns 404 when sitting does not exist', async () => {
    const db = createMockDb([
      { rows: [] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/sittings/999')
      .send({ title: 'Nope' });

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'SITTING_NOT_FOUND');
  });

  it('sets updated_at on update', async () => {
    const db = createMockDb([
      { rows: [SAMPLE_SITTING_ROW] },
    ]);
    const app = buildApp(db);

    await request(app)
      .patch('/api/sittings/1')
      .send({ priority: 'Low' });

    const updateQuery = db.getCalls()[0];
    assert.ok(updateQuery.text.includes('updated_at = now()'));
  });

  it('supports presidingOfficer field mapping to presiding_officer', async () => {
    const db = createMockDb([
      { rows: [{ ...SAMPLE_SITTING_ROW, presiding_officer: 'New Speaker' }] },
    ]);
    const app = buildApp(db);

    await request(app)
      .patch('/api/sittings/1')
      .send({ presidingOfficer: 'New Speaker' });

    const updateQuery = db.getCalls()[0];
    assert.ok(updateQuery.text.includes('presiding_officer ='));
    assert.ok(updateQuery.params.includes('New Speaker'));
  });
});

describe('DELETE /api/sittings/:id', () => {
  it('soft-deletes by setting status to Archived and returns 204', async () => {
    const db = createMockDb([
      { rows: [{ id: 1 }] },
    ]);
    const app = buildApp(db);

    const res = await request(app).delete('/api/sittings/1');

    assert.equal(res.status, 204);
    assert.equal(res.text, '');

    const deleteQuery = db.getCalls()[0];
    assert.ok(deleteQuery.text.includes("status = 'Archived'"));
    assert.ok(deleteQuery.text.includes('updated_at = now()'));
  });

  it('returns 404 when sitting does not exist', async () => {
    const db = createMockDb([
      { rows: [] },
    ]);
    const app = buildApp(db);

    const res = await request(app).delete('/api/sittings/999');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'SITTING_NOT_FOUND');
  });
});

describe('Authentication requirement', () => {
  it('returns 401 when requireSession rejects', async () => {
    function rejectAuth(req, res, _next) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    const db = createMockDb([]);
    const app = express();
    app.use(express.json());
    app.use(sittingsRoutes(rejectAuth, db));

    const res = await request(app).get('/api/sittings');

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'MISSING_TOKEN');
  });
});
