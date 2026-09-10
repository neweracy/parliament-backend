'use strict';

/**
 * Templates route unit/integration tests.
 *
 * Uses a mock db to exercise every /api/templates endpoint: the camelCase
 * response mapping, the validation envelopes, the default-template protections,
 * and the clear-then-set ordering that keeps the single-default invariant
 * satisfied at every intermediate state.
 *
 * @module test/routes/templates
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const templatesRoutes = require('../../routes/templates');

/**
 * Creates a mock db that replays the given responses in order and records
 * every (text, params) pair it was called with.
 *
 * @param {Array<{rows: Object[]}>} responses
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
 * Pass-through session middleware that attaches a role.
 * requirePermission resolves permissions server-side from req.user.role.
 */
function withUser(user) {
  return function attachUser(req, res, next) {
    req.user = user;
    next();
  };
}

/** Admin holds manage_templates and view_records. */
const asAdmin = withUser({ role: 'Admin' });

/** Viewer holds view_records but NOT manage_templates. */
const asViewer = withUser({ role: 'Viewer' });

function buildApp(db, requireSession = asAdmin) {
  const app = express();
  app.use(templatesRoutes(requireSession, db));
  return app;
}

/** A sample template row as stored in Postgres (snake_case). */
const SAMPLE_ROW = {
  id: 1,
  name: 'Official Hansard - Plenary',
  description: 'Standard template for full House plenary sittings.',
  type: 'Plenary',
  icon_key: 'book-open',
  is_default: true,
  usage_count: 142,
  header_lines: ['PARLIAMENT OF GHANA', 'OFFICIAL REPORT (HANSARD)'],
  sections: [{ id: '1', type: 'heading', content: 'PRAYERS', uppercase: true, bold: true }],
  speaker_format: '{name} ({constituency}):',
  show_timestamps: false,
  show_constituency: true,
  paragraph_numbering: false,
  created_at: '2026-09-03T09:00:00.000Z',
  updated_at: '2026-09-03T09:00:00.000Z',
};

/** A non-default row, used for the delete/duplicate happy paths. */
const NON_DEFAULT_ROW = {
  ...SAMPLE_ROW,
  id: 2,
  name: 'Committee Report',
  type: 'Committee',
  icon_key: 'users',
  is_default: false,
  usage_count: 67,
};

describe('GET /api/templates', () => {
  it('returns templates mapped to camelCase with a total count', async () => {
    const db = createMockDb([{ rows: [SAMPLE_ROW, NON_DEFAULT_ROW] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/templates');

    assert.equal(res.status, 200);
    assert.equal(res.body.total, 2);
    assert.equal(res.body.data.length, 2);

    const [first] = res.body.data;
    assert.equal(first.iconKey, 'book-open');
    assert.equal(first.isDefault, true);
    assert.equal(first.usageCount, 142);
    assert.equal(first.speakerFormat, '{name} ({constituency}):');
    assert.equal(first.showConstituency, true);
    assert.equal(first.paragraphNumbering, false);
    assert.deepEqual(first.headerLines, SAMPLE_ROW.header_lines);
    assert.equal(first.createdAt, SAMPLE_ROW.created_at);
    // Snake_case keys must not leak through the mapping
    assert.equal(first.icon_key, undefined);

    const query = db.getCalls()[0];
    assert.ok(
      query.text.includes('ORDER BY is_default DESC, usage_count DESC, name ASC'),
      'default template should sort first, then most-used, then alphabetical'
    );
    assert.deepEqual(query.params, []);
  });

  it('applies search and type filters as parameterised placeholders', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/templates?search=  plenary  &type=Plenary');

    assert.equal(res.status, 200);

    const query = db.getCalls()[0];
    assert.ok(query.text.includes('name ILIKE $1'), 'search should match name');
    assert.ok(
      query.text.includes("COALESCE(description, '') ILIKE $1"),
      'search should also match description, treating NULL as empty'
    );
    assert.ok(query.text.includes('type = $2'));
    assert.deepEqual(query.params, ['%plenary%', 'Plenary'],
      'search term should be trimmed and wrapped in wildcards');
  });

  it('ignores a search param that is blank after trimming', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    await request(app).get('/api/templates?search=%20%20');

    const query = db.getCalls()[0];
    assert.ok(!query.text.includes('ILIKE'), 'blank search should add no condition');
    assert.deepEqual(query.params, []);
  });

  it('returns 500 with the ServerError envelope when the query fails', async () => {
    const db = {
      query() { return Promise.reject(new Error('connection lost')); },
    };
    const app = buildApp(db);

    const res = await request(app).get('/api/templates');

    assert.equal(res.status, 500);
    assert.equal(res.body.error.type, 'ServerError');
    assert.equal(res.body.error.code, 'INTERNAL_ERROR');
  });
});

describe('GET /api/templates/:id', () => {
  it('returns the mapped template', async () => {
    const db = createMockDb([{ rows: [SAMPLE_ROW] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/templates/1');

    assert.equal(res.status, 200);
    assert.equal(res.body.id, 1);
    assert.equal(res.body.iconKey, 'book-open');
    assert.deepEqual(db.getCalls()[0].params, ['1']);
  });

  it('returns 404 when the template does not exist', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/templates/999');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.type, 'NotFoundError');
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
    assert.equal(res.body.error.message, 'Template with id 999 not found');
  });
});

describe('POST /api/templates', () => {
  it('creates a template, returns 201, and never sets is_default', async () => {
    const created = { ...NON_DEFAULT_ROW, id: 7, name: 'My Template', usage_count: 0 };
    const db = createMockDb([{ rows: [created] }]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ name: '  My Template  ', type: 'Committee', iconKey: 'users', headerLines: ['A'] });

    assert.equal(res.status, 201);
    assert.equal(res.body.id, 7);
    assert.equal(res.body.name, 'My Template');
    assert.equal(res.body.usageCount, 0);
    assert.equal(res.body.iconKey, 'users');

    const query = db.getCalls()[0];
    assert.ok(!query.text.includes('is_default'),
      'new templates must never be inserted as the default');
    assert.equal(query.params[0], 'My Template', 'name should be trimmed');
    assert.equal(query.params[2], 'Committee');
    assert.equal(query.params[3], 'users');
    assert.equal(query.params[4], JSON.stringify(['A']), 'jsonb columns are stringified');
    assert.equal(query.params[5], JSON.stringify([]), 'omitted sections default to []');
    assert.equal(query.params[6], '{name} ({constituency}):');
    assert.equal(query.params[7], false);
    assert.equal(query.params[8], true);
    assert.equal(query.params[9], false);
  });

  it('applies Custom / file-text defaults when type and iconKey are omitted', async () => {
    const db = createMockDb([{ rows: [{ ...NON_DEFAULT_ROW, usage_count: 0 }] }]);
    const app = buildApp(db);

    await request(app).post('/api/templates').send({ name: 'Bare' });

    const query = db.getCalls()[0];
    assert.equal(query.params[1], null, 'omitted description becomes NULL');
    assert.equal(query.params[2], 'Custom');
    assert.equal(query.params[3], 'file-text');
  });

  it('returns 400 when name is omitted', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates').send({ type: 'Custom' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.type, 'ValidationError');
    assert.equal(res.body.error.code, 'MISSING_REQUIRED_FIELDS');
    assert.equal(res.body.error.message, 'name is required');
    assert.equal(db.getCalls().length, 0, 'no query should run on a rejected payload');
  });

  it('returns 400 when name is whitespace only', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates').send({ name: '   ' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'MISSING_REQUIRED_FIELDS');
  });

  it('returns 400 on an invalid type', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ name: 'Bad', type: 'Emergency' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'INVALID_TYPE');
    assert.equal(res.body.error.message,
      'type must be one of Plenary, Committee, Special, Custom');
  });

  it('returns 400 on an invalid iconKey', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ name: 'Bad', iconKey: 'rocket' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'INVALID_ICON_KEY');
    assert.equal(res.body.error.message,
      'iconKey must be one of book-open, users, gavel, file-text');
  });

  it('returns 400 when headerLines is not an array', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ name: 'Bad', headerLines: 'PARLIAMENT OF GHANA' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'INVALID_PAYLOAD');
    assert.equal(res.body.error.message, 'headerLines and sections must be arrays');
  });

  it('returns 403 for a Viewer, who lacks manage_templates', async () => {
    const db = createMockDb([]);
    const app = buildApp(db, asViewer);

    const res = await request(app).post('/api/templates').send({ name: 'Nope' });

    assert.equal(res.status, 403);
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
    assert.equal(db.getCalls().length, 0,
      'the handler must not run when permission is denied');
  });
});

describe('PATCH /api/templates/:id', () => {
  it('updates only the supplied fields and always bumps updated_at', async () => {
    const db = createMockDb([{ rows: [{ ...SAMPLE_ROW, name: 'Renamed' }] }]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/templates/1')
      .send({ name: 'Renamed', showTimestamps: true, sections: [] });

    assert.equal(res.status, 200);
    assert.equal(res.body.name, 'Renamed');

    // Placeholders follow the handler's fixed field order, not the body's key
    // order, so sections takes $2 and show_timestamps takes $3.
    const query = db.getCalls()[0];
    assert.ok(query.text.includes('name = $1'));
    assert.ok(query.text.includes('sections = $2'));
    assert.ok(query.text.includes('show_timestamps = $3'));
    assert.ok(query.text.includes('WHERE id = $4'));
    assert.ok(query.text.includes('updated_at = now()'));
    assert.ok(!query.text.includes('description'),
      'omitted fields must not appear in the SET clause');
    assert.ok(!query.text.includes('is_default'),
      'is_default must not be patchable through PATCH');
    assert.deepEqual(query.params, ['Renamed', JSON.stringify([]), true, '1']);
  });

  it('returns 400 when the body carries no updatable field', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app).patch('/api/templates/1').send({});

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'NO_FIELDS_TO_UPDATE');
    assert.equal(res.body.error.message,
      'At least one field must be provided for update');
    assert.equal(db.getCalls().length, 0);
  });

  it('returns 400 on an invalid type', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app).patch('/api/templates/1').send({ type: 'Urgent' });

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'INVALID_TYPE');
  });

  it('returns 404 when the row does not exist', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).patch('/api/templates/999').send({ name: 'Ghost' });

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
  });
});

describe('DELETE /api/templates/:id', () => {
  it('deletes a non-default template and returns 204', async () => {
    const db = createMockDb([
      { rows: [{ is_default: false }] },
      { rows: [{ id: 2 }] },
    ]);
    const app = buildApp(db);

    const res = await request(app).delete('/api/templates/2');

    assert.equal(res.status, 204);

    const [lookup, del] = db.getCalls();
    assert.ok(lookup.text.includes('SELECT is_default FROM template'));
    assert.ok(del.text.includes('DELETE FROM template WHERE id = $1 RETURNING id'));
    assert.deepEqual(del.params, ['2']);
  });

  it('returns 409 and deletes nothing when the template is the default', async () => {
    const db = createMockDb([{ rows: [{ is_default: true }] }]);
    const app = buildApp(db);

    const res = await request(app).delete('/api/templates/1');

    assert.equal(res.status, 409);
    assert.equal(res.body.error.type, 'ConflictError');
    assert.equal(res.body.error.code, 'CANNOT_DELETE_DEFAULT');
    assert.equal(db.getCalls().length, 1,
      'the DELETE statement must not run for the default template');
  });

  it('returns 404 when the template does not exist', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).delete('/api/templates/999');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
  });
});

describe('POST /api/templates/:id/duplicate', () => {
  it('copies the template under a "(Copy)" name in a single statement', async () => {
    const copy = {
      ...NON_DEFAULT_ROW,
      id: 12,
      name: 'Committee Report (Copy)',
      is_default: false,
      usage_count: 0,
    };
    const db = createMockDb([{ rows: [copy] }]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/2/duplicate');

    assert.equal(res.status, 201);
    assert.equal(res.body.name, 'Committee Report (Copy)');
    assert.equal(res.body.isDefault, false);
    assert.equal(res.body.usageCount, 0);

    assert.equal(db.getCalls().length, 1,
      'the copy must be a single atomic INSERT ... SELECT');
    const query = db.getCalls()[0];
    assert.ok(query.text.includes('INSERT INTO template'));
    assert.ok(query.text.includes('SELECT name || $2'));
    assert.deepEqual(query.params, ['2', ' (Copy)']);
  });

  it('returns 404 when the source template does not exist', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/999/duplicate');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
  });
});

describe('POST /api/templates/:id/set-default', () => {
  it('clears the incumbent default before setting the new one', async () => {
    const db = createMockDb([
      { rows: [{ id: 2 }] },
      { rows: [] },
      { rows: [{ ...NON_DEFAULT_ROW, is_default: true }] },
    ]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/2/set-default');

    assert.equal(res.status, 200);
    assert.equal(res.body.isDefault, true);

    const calls = db.getCalls();
    assert.equal(calls.length, 3);

    // 1. existence check, 2. clear, 3. set — in that order, so the partial
    // unique index ux_template_single_default never sees two default rows.
    assert.ok(calls[0].text.includes('SELECT id FROM template'));
    assert.ok(calls[1].text.includes('is_default = false'),
      'the second statement must clear the previous default');
    assert.ok(calls[1].text.includes('id <> $1'));
    assert.ok(!calls[1].text.includes('is_default = true'),
      'clear and set must be separate statements');
    assert.ok(calls[2].text.includes('is_default = true'),
      'the third statement must set the new default');
    assert.ok(!calls[2].text.includes('is_default = false'));
    assert.deepEqual(calls[1].params, ['2']);
    assert.deepEqual(calls[2].params, ['2']);
  });

  it('returns 404 without touching is_default when the template is missing', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/999/set-default');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
    assert.equal(db.getCalls().length, 1,
      'no UPDATE should run when the target does not exist');
  });
});

describe('POST /api/templates/:id/use', () => {
  it('increments the usage count and returns the updated template', async () => {
    const db = createMockDb([{ rows: [{ ...SAMPLE_ROW, usage_count: 143 }] }]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/1/use');

    assert.equal(res.status, 200);
    assert.equal(res.body.usageCount, 143);

    const query = db.getCalls()[0];
    assert.ok(query.text.includes('usage_count = usage_count + 1'));
    assert.deepEqual(query.params, ['1']);
  });

  it('is available to a Viewer, who only holds view_records', async () => {
    const db = createMockDb([{ rows: [{ ...SAMPLE_ROW, usage_count: 143 }] }]);
    const app = buildApp(db, asViewer);

    const res = await request(app).post('/api/templates/1/use');

    assert.equal(res.status, 200);
    assert.equal(res.body.usageCount, 143);
  });

  it('returns 404 when the template does not exist', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/999/use');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
  });
});
