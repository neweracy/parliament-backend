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
const { isDeepStrictEqual } = require('node:util');
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

/** Escapes a literal for embedding in a RegExp. */
function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Translates a SQL LIKE pattern (with backslash as the ESCAPE character) into an
 * anchored RegExp, so the store db honours % / _ wildcards and their escapes.
 */
function likeToRegExp(pattern) {
  let source = '';
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === '\\') {
      i++;
      source += escapeRegExp(pattern[i] === undefined ? '\\' : pattern[i]);
      continue;
    }
    if (ch === '%') { source += '.*'; continue; }
    if (ch === '_') { source += '.'; continue; }
    source += escapeRegExp(ch);
  }
  return new RegExp(`^${source}$`);
}

/** SQL `=` semantics for scalars: NULL compares as unknown, i.e. never true. */
function sqlEquals(left, right) {
  if (left === null || left === undefined || right === null || right === undefined) {
    return false;
  }
  return String(left) === String(right);
}

/**
 * Evaluates one condition of the duplicate-check WHERE clause against a row.
 * Throws on an unrecognised shape so a silently reworded predicate cannot make
 * these tests pass vacuously.
 */
function evaluateCondition(condition, row, params) {
  const coalesce = /^COALESCE\((\w+), ''\) = COALESCE\(\$(\d+), ''\)$/.exec(condition);
  if (coalesce) {
    const [, column, index] = coalesce;
    const stored = row[column] === null || row[column] === undefined ? '' : row[column];
    const supplied = params[Number(index) - 1];
    return stored === (supplied === null || supplied === undefined ? '' : supplied);
  }

  const jsonb = /^(\w+) = \$(\d+)::jsonb$/.exec(condition);
  if (jsonb) {
    const [, column, index] = jsonb;
    return isDeepStrictEqual(row[column], JSON.parse(params[Number(index) - 1]));
  }

  const scalar = /^(\w+) (=|<>) \$(\d+)$/.exec(condition);
  if (scalar) {
    const [, column, operator, index] = scalar;
    const equal = sqlEquals(row[column], params[Number(index) - 1]);
    return operator === '=' ? equal : !equal;
  }

  throw new Error(`Unsupported condition in duplicate check: ${condition}`);
}

/**
 * Creates a db double that actually evaluates the queries the duplicate-content
 * rules depend on against an in-memory row set:
 *
 *   - the duplicate-check SELECT (every WHERE condition is interpreted)
 *   - SELECT * FROM template WHERE id = $1
 *   - the "(Copy …)" name lookup, LIKE wildcards and ESCAPE included
 *
 * Everything else (INSERT / UPDATE) replays `writeResponse`. Interpreting the
 * predicate is what makes the per-field tests real: dropping a column from the
 * WHERE clause changes the outcome instead of going unnoticed.
 *
 * @param {Object[]} rows - Stored rows (snake_case, as Postgres would return)
 * @param {{rows: Object[]}} writeResponse - Result replayed for writes
 */
function createStoreDb(rows, writeResponse = { rows: [] }) {
  const calls = [];

  return {
    query(text, params) {
      calls.push({ text, params });
      const sql = text.trim();

      if (sql.startsWith('SELECT id, name FROM template WHERE ')) {
        const where = sql
          .slice('SELECT id, name FROM template WHERE '.length)
          .replace(/ LIMIT 1$/, '');
        const conditions = where.split(' AND ');
        const matched = rows.filter((row) =>
          conditions.every((condition) => evaluateCondition(condition, row, params)));
        return Promise.resolve({
          rows: matched.slice(0, 1).map((r) => ({ id: r.id, name: r.name })),
        });
      }

      if (sql.startsWith('SELECT * FROM template WHERE id = $1')) {
        return Promise.resolve({ rows: rows.filter((r) => String(r.id) === String(params[0])) });
      }

      if (sql.startsWith('SELECT name FROM template WHERE name = $1 OR name LIKE $2')) {
        const pattern = likeToRegExp(params[1]);
        const matched = rows.filter((r) => r.name === params[0] || pattern.test(r.name));
        return Promise.resolve({ rows: matched.map((r) => ({ name: r.name })) });
      }

      return Promise.resolve(writeResponse);
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

/**
 * A create payload that sets all ten content attributes to non-default values,
 * so a per-field variation is detectable no matter which field is varied.
 */
const BASE_PAYLOAD = {
  name: 'Baseline',
  description: 'A baseline template',
  type: 'Committee',
  iconKey: 'users',
  headerLines: ['LINE ONE'],
  sections: [{ id: '1', type: 'heading', content: 'PRAYERS' }],
  speakerFormat: '{name}:',
  showTimestamps: true,
  showConstituency: false,
  paragraphNumbering: true,
};

/**
 * Projects a create payload into the DB row it would be stored as, applying the
 * same defaults the route does. Server-owned fields get conspicuous values on
 * purpose: they must never influence the duplicate verdict.
 */
function rowFor(payload, overrides = {}) {
  return {
    id: 3,
    name: payload.name.trim(),
    description: payload.description || null,
    type: payload.type || 'Custom',
    icon_key: payload.iconKey || 'file-text',
    is_default: true,
    usage_count: 99,
    header_lines: payload.headerLines || [],
    sections: payload.sections || [],
    speaker_format: payload.speakerFormat || '{name} ({constituency}):',
    show_timestamps: payload.showTimestamps === undefined ? false : payload.showTimestamps,
    show_constituency: payload.showConstituency === undefined ? true : payload.showConstituency,
    paragraph_numbering:
      payload.paragraphNumbering === undefined ? false : payload.paragraphNumbering,
    created_at: '2026-09-03T09:00:00.000Z',
    updated_at: '2026-09-03T09:00:00.000Z',
    ...overrides,
  };
}

/** The row the write-path double returns for a successful create. */
const CREATED_ROW = { ...rowFor(BASE_PAYLOAD), id: 50, is_default: false, usage_count: 0 };

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
    // Call 1 is the duplicate check (no collision), call 2 the INSERT.
    const db = createMockDb([{ rows: [] }, { rows: [created] }]);
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ name: '  My Template  ', type: 'Committee', iconKey: 'users', headerLines: ['A'] });

    assert.equal(res.status, 201);
    assert.equal(res.body.id, 7);
    assert.equal(res.body.name, 'My Template');
    assert.equal(res.body.usageCount, 0);
    assert.equal(res.body.iconKey, 'users');

    const query = db.getCalls()[1];
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
    const db = createMockDb([{ rows: [] }, { rows: [{ ...NON_DEFAULT_ROW, usage_count: 0 }] }]);
    const app = buildApp(db);

    await request(app).post('/api/templates').send({ name: 'Bare' });

    const query = db.getCalls()[1];
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
    // Call 1 reads the current row, call 2 is the duplicate check (no
    // collision), call 3 is the UPDATE.
    const db = createMockDb([
      { rows: [SAMPLE_ROW] },
      { rows: [] },
      { rows: [{ ...SAMPLE_ROW, name: 'Renamed' }] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/templates/1')
      .send({ name: 'Renamed', showTimestamps: true, sections: [] });

    assert.equal(res.status, 200);
    assert.equal(res.body.name, 'Renamed');

    // Placeholders follow the handler's fixed field order, not the body's key
    // order, so sections takes $2 and show_timestamps takes $3.
    const query = db.getCalls()[2];
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
  it('copies the template under a "(Copy)" name', async () => {
    const copy = {
      ...NON_DEFAULT_ROW,
      id: 12,
      name: 'Committee Report (Copy)',
      is_default: false,
      usage_count: 0,
    };
    const db = createStoreDb([NON_DEFAULT_ROW], { rows: [copy] });
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/2/duplicate');

    assert.equal(res.status, 201);
    assert.equal(res.body.name, 'Committee Report (Copy)');
    assert.equal(res.body.isDefault, false);
    assert.equal(res.body.usageCount, 0);

    // Read the source, look up the taken "(Copy …)" names, then insert. No
    // longer a single INSERT ... SELECT because the name depends on existing rows.
    const [source, names, insert] = db.getCalls();
    assert.equal(db.getCalls().length, 3);
    assert.ok(source.text.includes('SELECT * FROM template WHERE id = $1'));
    assert.deepEqual(source.params, ['2']);
    assert.ok(names.text.includes('SELECT name FROM template WHERE name = $1 OR name LIKE $2'));
    assert.ok(insert.text.includes('INSERT INTO template'));
    assert.ok(insert.text.includes('VALUES ($1, $2, $3, $4, false, 0'),
      'the copy is never the default and starts with a zeroed usage count');
    assert.equal(insert.params[0], 'Committee Report (Copy)');
    assert.equal(insert.params[1], NON_DEFAULT_ROW.description);
    assert.equal(insert.params[2], 'Committee');
    assert.equal(insert.params[3], 'users');
    assert.equal(insert.params[4], JSON.stringify(NON_DEFAULT_ROW.header_lines),
      'jsonb columns are passed as stringified JSON');
    assert.equal(insert.params[5], JSON.stringify(NON_DEFAULT_ROW.sections));
  });

  it('returns 404 when the source template does not exist', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/999/duplicate');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
  });

  it('falls back to "(Copy 2)" when "(Copy)" is already taken', async () => {
    const existingCopy = { ...NON_DEFAULT_ROW, id: 12, name: 'Committee Report (Copy)' };
    const db = createStoreDb(
      [NON_DEFAULT_ROW, existingCopy],
      { rows: [{ ...NON_DEFAULT_ROW, id: 13, name: 'Committee Report (Copy 2)', usage_count: 0 }] }
    );
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/2/duplicate');

    assert.equal(res.status, 201);
    assert.equal(res.body.name, 'Committee Report (Copy 2)');

    const insert = db.getCalls()[2];
    assert.equal(insert.params[0], 'Committee Report (Copy 2)',
      'a taken "(Copy)" must not be reused — it would be an identical duplicate');
  });

  it('falls back to "(Copy 3)" when "(Copy)" and "(Copy 2)" are taken', async () => {
    const db = createStoreDb(
      [
        NON_DEFAULT_ROW,
        { ...NON_DEFAULT_ROW, id: 12, name: 'Committee Report (Copy)' },
        { ...NON_DEFAULT_ROW, id: 13, name: 'Committee Report (Copy 2)' },
      ],
      { rows: [{ ...NON_DEFAULT_ROW, id: 14, name: 'Committee Report (Copy 3)', usage_count: 0 }] }
    );
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/2/duplicate');

    assert.equal(res.status, 201);
    assert.equal(db.getCalls()[2].params[0], 'Committee Report (Copy 3)');
    assert.equal(db.getCalls().length, 3,
      'the taken names come from one query, not a probe per candidate');
  });

  it('escapes LIKE metacharacters in the source name', async () => {
    const source = { ...NON_DEFAULT_ROW, id: 5, name: 'Report_1' };
    // "ReportX1 (Copy)" would be matched by an unescaped "Report_1 (Copy %)"
    // pattern, wrongly marking the first candidate as taken.
    const decoy = { ...NON_DEFAULT_ROW, id: 6, name: 'ReportX1 (Copy)' };
    const db = createStoreDb(
      [source, decoy],
      { rows: [{ ...source, id: 15, name: 'Report_1 (Copy)', usage_count: 0 }] }
    );
    const app = buildApp(db);

    const res = await request(app).post('/api/templates/5/duplicate');

    assert.equal(res.status, 201);

    const names = db.getCalls()[1];
    assert.ok(/ESCAPE\s+'\\'/.test(names.text),
      'the LIKE needs an ESCAPE clause for the escaped literal to mean anything');
    assert.deepEqual(names.params, ['Report_1 (Copy)', 'Report\\_1 (Copy %)'],
      '_ in the source name must be escaped so it is matched literally');
    assert.equal(db.getCalls()[2].params[0], 'Report_1 (Copy)',
      'an unrelated row must not push the copy to "(Copy 2)"');
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

describe('POST /api/templates duplicate-content rejection', () => {
  it('returns 409 and runs no INSERT when every content attribute matches', async () => {
    const db = createStoreDb([rowFor(BASE_PAYLOAD)], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    const res = await request(app).post('/api/templates').send(BASE_PAYLOAD);

    assert.equal(res.status, 409);
    assert.equal(res.body.error.type, 'ConflictError');
    assert.equal(res.body.error.code, 'DUPLICATE_TEMPLATE');
    assert.equal(
      res.body.error.message,
      'An identical template already exists ("Baseline"). Change at least one field to save a new template.',
      'the message must name the colliding template'
    );

    assert.ok(!db.getCalls().some((call) => call.text.includes('INSERT INTO template')),
      'a rejected create must not reach the INSERT');
    assert.equal(db.getCalls().length, 1, 'only the duplicate check should run');
  });

  it('ignores is_default and usage_count when deciding identity', async () => {
    // rowFor gives the stored row is_default: true and usage_count: 99, values a
    // create can never produce. They must not rescue the payload from the 409.
    const stored = rowFor(BASE_PAYLOAD);
    assert.equal(stored.is_default, true);
    assert.equal(stored.usage_count, 99);

    const db = createStoreDb([stored], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    const res = await request(app).post('/api/templates').send(BASE_PAYLOAD);

    assert.equal(res.status, 409);
  });

  it('allows a create that differs only in name', async () => {
    const db = createStoreDb([rowFor(BASE_PAYLOAD)], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ ...BASE_PAYLOAD, name: 'Baseline (regional)' });

    assert.equal(res.status, 201);
    assert.ok(db.getCalls().some((call) => call.text.includes('INSERT INTO template')),
      'a distinct template must be inserted');
  });

  /**
   * One entry per content attribute. Each variation differs from BASE_PAYLOAD in
   * exactly that attribute, so a WHERE clause that forgets a column would report
   * a collision here and fail the corresponding case.
   */
  const FIELD_VARIATIONS = [
    ['name', { name: 'Baseline Two' }],
    ['description', { description: 'A different description' }],
    ['type', { type: 'Plenary' }],
    ['iconKey', { iconKey: 'gavel' }],
    ['headerLines', { headerLines: ['LINE TWO'] }],
    ['sections', { sections: [{ id: '2', type: 'heading', content: 'VOTES' }] }],
    ['speakerFormat', { speakerFormat: '{name} ({constituency}):' }],
    ['showTimestamps', { showTimestamps: false }],
    ['showConstituency', { showConstituency: true }],
    ['paragraphNumbering', { paragraphNumbering: false }],
  ];

  for (const [field, variation] of FIELD_VARIATIONS) {
    it(`allows a create differing only in ${field}`, async () => {
      const db = createStoreDb([rowFor(BASE_PAYLOAD)], { rows: [CREATED_ROW] });
      const app = buildApp(db);

      const res = await request(app)
        .post('/api/templates')
        .send({ ...BASE_PAYLOAD, ...variation });

      assert.equal(res.status, 201, `${field} must be part of the identity comparison`);
    });
  }

  it('treats a null description and an empty-string description as the same', async () => {
    const stored = rowFor({ ...BASE_PAYLOAD, description: undefined });
    assert.equal(stored.description, null);

    const db = createStoreDb([stored], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ ...BASE_PAYLOAD, description: '' });

    assert.equal(res.status, 409,
      "NULL <> NULL must not let a description-less duplicate through");
  });

  it('matches a stored empty-string description against an omitted description', async () => {
    const db = createStoreDb([rowFor(BASE_PAYLOAD, { description: '' })], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    const { description: _omitted, ...withoutDescription } = BASE_PAYLOAD;
    const res = await request(app).post('/api/templates').send(withoutDescription);

    assert.equal(res.status, 409);
  });

  it('allows a create whose description genuinely differs', async () => {
    const db = createStoreDb(
      [rowFor({ ...BASE_PAYLOAD, description: undefined })],
      { rows: [CREATED_ROW] }
    );
    const app = buildApp(db);

    const res = await request(app)
      .post('/api/templates')
      .send({ ...BASE_PAYLOAD, description: 'Now it has one' });

    assert.equal(res.status, 201);
  });

  it('applies the defaults before comparing, so an omitted field still collides', async () => {
    // The stored row spells out every value a bare create would default to.
    const explicitDefaults = rowFor({
      name: 'Bare',
      type: 'Custom',
      iconKey: 'file-text',
      headerLines: [],
      sections: [],
      speakerFormat: '{name} ({constituency}):',
      showTimestamps: false,
      showConstituency: true,
      paragraphNumbering: false,
    });
    const db = createStoreDb([explicitDefaults], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    const res = await request(app).post('/api/templates').send({ name: 'Bare' });

    assert.equal(res.status, 409);
    assert.equal(res.body.error.code, 'DUPLICATE_TEMPLATE');
  });

  it('compares the jsonb columns as jsonb and the description through COALESCE', async () => {
    const db = createStoreDb([], { rows: [CREATED_ROW] });
    const app = buildApp(db);

    await request(app).post('/api/templates').send(BASE_PAYLOAD);

    const check = db.getCalls()[0];
    assert.ok(check.text.startsWith('SELECT id, name FROM template WHERE '));
    assert.ok(check.text.endsWith('LIMIT 1'));
    assert.ok(check.text.includes('header_lines = $5::jsonb'),
      'a text comparison would treat equal JSON with a different key order as different');
    assert.ok(check.text.includes('sections = $6::jsonb'));
    assert.ok(check.text.includes("COALESCE(description, '') = COALESCE($2, '')"));
    assert.ok(!check.text.includes('is_default'),
      'server-owned fields must stay out of the comparison');
    assert.ok(!check.text.includes('usage_count'));
    assert.ok(!check.text.includes('created_at'));
    assert.deepEqual(check.params, [
      'Baseline',
      'A baseline template',
      'Committee',
      'users',
      JSON.stringify(BASE_PAYLOAD.headerLines),
      JSON.stringify(BASE_PAYLOAD.sections),
      '{name}:',
      true,
      false,
      true,
    ]);
  });
});

describe('PATCH /api/templates/:id duplicate-content rejection', () => {
  it('returns 409 and runs no UPDATE when the patch would clone another row', async () => {
    const target = rowFor(BASE_PAYLOAD, { id: 1, name: 'Target' });
    const other = rowFor(BASE_PAYLOAD, { id: 2, name: 'Other' });
    const db = createStoreDb([target, other], { rows: [target] });
    const app = buildApp(db);

    const res = await request(app).patch('/api/templates/1').send({ name: 'Other' });

    assert.equal(res.status, 409);
    assert.equal(res.body.error.code, 'DUPLICATE_TEMPLATE');
    assert.equal(
      res.body.error.message,
      'An identical template already exists ("Other"). Change at least one field to save a new template.'
    );
    assert.ok(!db.getCalls().some((call) => call.text.includes('UPDATE template')),
      'a rejected patch must not reach the UPDATE');
    assert.equal(db.getCalls().length, 2, 'read the row, check for a duplicate, stop');
  });

  it('does not collide with itself when the patch changes nothing meaningful', async () => {
    const target = rowFor(BASE_PAYLOAD, { id: 1, name: 'Target' });
    const db = createStoreDb([target], { rows: [target] });
    const app = buildApp(db);

    const res = await request(app).patch('/api/templates/1').send({ name: 'Target' });

    assert.equal(res.status, 200);
    assert.equal(res.body.name, 'Target');

    const check = db.getCalls()[1];
    assert.ok(check.text.includes('id <> $11'),
      'the row being patched must be excluded from its own duplicate check');
    assert.equal(check.params[10], '1');
    assert.ok(db.getCalls()[2].text.includes('UPDATE template'));
  });

  it('merges unpatched fields from the current row before comparing', async () => {
    const target = rowFor(BASE_PAYLOAD, { id: 1, name: 'Target', type: 'Plenary' });
    const other = rowFor(BASE_PAYLOAD, { id: 2, name: 'Target' });
    const db = createStoreDb([target, other], { rows: [target] });
    const app = buildApp(db);

    // Only `type` is patched; every other value has to come from the stored row
    // for this to be recognised as a clone of id 2.
    const res = await request(app).patch('/api/templates/1').send({ type: 'Committee' });

    assert.equal(res.status, 409);
    assert.equal(db.getCalls()[1].params[2], 'Committee',
      'the patched value wins over the stored one');
    assert.equal(db.getCalls()[1].params[6], BASE_PAYLOAD.speakerFormat,
      'unpatched values come from the stored row');
  });

  it('returns 404 from the upfront read when the row does not exist', async () => {
    const db = createStoreDb([]);
    const app = buildApp(db);

    const res = await request(app).patch('/api/templates/999').send({ name: 'Ghost' });

    assert.equal(res.status, 404);
    assert.equal(res.body.error.type, 'NotFoundError');
    assert.equal(res.body.error.code, 'TEMPLATE_NOT_FOUND');
    assert.equal(res.body.error.message, 'Template with id 999 not found');
    assert.equal(db.getCalls().length, 1, 'neither the duplicate check nor the UPDATE should run');
    assert.ok(db.getCalls()[0].text.includes('SELECT * FROM template WHERE id = $1'));
  });
});
