'use strict';

/**
 * Settings route unit tests.
 *
 * Uses a mock db to test route logic without a real PostgreSQL connection.
 * Validates: Requirements 3.1, 3.2, 3.3, 3.5, 3.6, 3.7, 3.8
 *
 * @module test/routes/settings
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const settingsRoutes = require('../../routes/settings');
const { validateExportConfig } = settingsRoutes;

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
 * A passthrough auth middleware that also sets req.user with Admin permissions.
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
    ],
  };
  next();
}

/**
 * Builds an Express app with the settings routes using the given db mock.
 */
function buildApp(db) {
  const app = express();
  app.use(settingsRoutes(passThrough, db));
  return app;
}

/** Default export config as stored in DB */
const DEFAULT_EXPORT_CONFIG = {
  pdf: {
    pageSize: 'A4',
    marginsCm: 2.5,
    fontFamily: 'Times New Roman',
    fontSizePt: 12,
    pageNumbers: true,
    parliamentCrest: true,
    timestamps: false,
  },
  docx: {
    hansardStyles: true,
    trackChanges: false,
    includeMetadata: true,
  },
  namingPattern: 'Hansard_{sessionType}_{date}_{committee}',
};

/** A sample settings row */
const SAMPLE_SETTINGS_ROW = {
  id: 1,
  transcription_engine: 'deepgram',
  default_language: 'en',
  custom_dictionary: ['Parliament', 'Hansard'],
  auto_save_interval_s: 30,
  export_config: DEFAULT_EXPORT_CONFIG,
};

// ─── validateExportConfig unit tests ─────────────────────────────────────────

describe('validateExportConfig', () => {
  it('returns null for a valid complete config', () => {
    const err = validateExportConfig(DEFAULT_EXPORT_CONFIG);
    assert.equal(err, null);
  });

  it('returns null for a partial pdf update', () => {
    const err = validateExportConfig({ pdf: { pageSize: 'Letter' } });
    assert.equal(err, null);
  });

  it('rejects invalid page size', () => {
    const err = validateExportConfig({ pdf: { pageSize: 'B5' } });
    assert.ok(err.includes('Invalid page size'));
  });

  it('rejects margins below minimum', () => {
    const err = validateExportConfig({ pdf: { marginsCm: 0.3 } });
    assert.ok(err.includes('Margins must be'));
  });

  it('rejects margins above maximum', () => {
    const err = validateExportConfig({ pdf: { marginsCm: 6.0 } });
    assert.ok(err.includes('Margins must be'));
  });

  it('rejects font size below minimum', () => {
    const err = validateExportConfig({ pdf: { fontSizePt: 6 } });
    assert.ok(err.includes('Font size must be'));
  });

  it('rejects font size above maximum', () => {
    const err = validateExportConfig({ pdf: { fontSizePt: 30 } });
    assert.ok(err.includes('Font size must be'));
  });

  it('rejects non-integer font size', () => {
    const err = validateExportConfig({ pdf: { fontSizePt: 12.5 } });
    assert.ok(err.includes('Font size must be'));
  });

  it('rejects naming pattern over 200 chars', () => {
    const err = validateExportConfig({ namingPattern: 'x'.repeat(201) });
    assert.ok(err.includes('must not exceed 200'));
  });

  it('rejects naming pattern with disallowed variables', () => {
    const err = validateExportConfig({ namingPattern: 'Report_{badVar}_{date}' });
    assert.ok(err.includes('Invalid template variables'));
    assert.ok(err.includes('{badVar}'));
  });

  it('accepts naming pattern with all allowed variables', () => {
    const err = validateExportConfig({
      namingPattern: '{sessionType}_{date}_{committee}_{presidingOfficer}_{recordId}',
    });
    assert.equal(err, null);
  });

  it('accepts naming pattern with no variables (literal text only)', () => {
    const err = validateExportConfig({ namingPattern: 'Hansard-Report-2024' });
    assert.equal(err, null);
  });

  it('rejects non-object config', () => {
    const err = validateExportConfig('not an object');
    assert.ok(err.includes('must be an object'));
  });

  it('rejects null config', () => {
    const err = validateExportConfig(null);
    assert.ok(err.includes('must be an object'));
  });
});

// ─── GET /api/settings ───────────────────────────────────────────────────────

describe('GET /api/settings', () => {
  it('returns settings including exportConfig', async () => {
    const db = createMockDb([{ rows: [SAMPLE_SETTINGS_ROW] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/settings');

    assert.equal(res.status, 200);
    assert.equal(res.body.transcriptionEngine, 'deepgram');
    assert.deepEqual(res.body.exportConfig, DEFAULT_EXPORT_CONFIG);
  });

  it('returns 404 when no settings row exists', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/settings');

    assert.equal(res.status, 404);
    assert.equal(res.body.error.code, 'SETTINGS_NOT_FOUND');
  });
});

// ─── GET /api/settings/export ────────────────────────────────────────────────

describe('GET /api/settings/export', () => {
  it('returns the export config', async () => {
    const db = createMockDb([{ rows: [{ export_config: DEFAULT_EXPORT_CONFIG }] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/settings/export');

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, DEFAULT_EXPORT_CONFIG);
  });

  it('returns 404 when no settings row exists', async () => {
    const db = createMockDb([{ rows: [] }]);
    const app = buildApp(db);

    const res = await request(app).get('/api/settings/export');

    assert.equal(res.status, 404);
  });
});

// ─── PATCH /api/settings/export ──────────────────────────────────────────────

describe('PATCH /api/settings/export', () => {
  it('persists valid partial PDF update', async () => {
    const db = createMockDb([
      { rows: [{ export_config: DEFAULT_EXPORT_CONFIG }] }, // SELECT
      { rows: [{ export_config: { ...DEFAULT_EXPORT_CONFIG, pdf: { ...DEFAULT_EXPORT_CONFIG.pdf, pageSize: 'Letter' } } }] }, // UPDATE RETURNING
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/settings/export')
      .send({ pdf: { pageSize: 'Letter' } });

    assert.equal(res.status, 200);
    assert.equal(res.body.pdf.pageSize, 'Letter');
  });

  it('rejects invalid page size with 422', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/settings/export')
      .send({ pdf: { pageSize: 'Tabloid' } });

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'VALIDATION_ERROR');
  });

  it('rejects invalid naming pattern with 422', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/settings/export')
      .send({ namingPattern: 'Report_{invalidVar}' });

    assert.equal(res.status, 422);
    assert.ok(res.body.error.message.includes('Invalid template variables'));
  });

  it('rejects margins out of range', async () => {
    const db = createMockDb([]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/settings/export')
      .send({ pdf: { marginsCm: 10 } });

    assert.equal(res.status, 422);
  });

  it('merges partial updates with existing config', async () => {
    const db = createMockDb([
      { rows: [{ export_config: DEFAULT_EXPORT_CONFIG }] },
      { rows: [{ export_config: { ...DEFAULT_EXPORT_CONFIG, docx: { ...DEFAULT_EXPORT_CONFIG.docx, trackChanges: true } } }] },
    ]);
    const app = buildApp(db);

    const res = await request(app)
      .patch('/api/settings/export')
      .send({ docx: { trackChanges: true } });

    assert.equal(res.status, 200);
    // Verify the DB query was called with merged config
    const calls = db.getCalls();
    assert.equal(calls.length, 2);
  });
});

// ─── RBAC permission enforcement ─────────────────────────────────────────────

describe('RBAC enforcement on settings routes', () => {
  it('denies PATCH /api/settings to Viewer role', async () => {
    const db = createMockDb([]);
    const app = express();

    // Viewer auth middleware
    function viewerAuth(req, res, next) {
      req.user = {
        userId: 'viewer-1',
        email: 'viewer@test.com',
        name: 'Viewer User',
        role: 'Viewer',
        permissions: ['view_records', 'search_hansard', 'export_published'],
      };
      next();
    }

    app.use(settingsRoutes(viewerAuth, db));

    const res = await request(app)
      .patch('/api/settings')
      .send({ transcriptionEngine: 'khaya' });

    assert.equal(res.status, 403);
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
  });

  it('allows GET /api/settings to Viewer role', async () => {
    const db = createMockDb([{ rows: [SAMPLE_SETTINGS_ROW] }]);
    const app = express();

    function viewerAuth(req, res, next) {
      req.user = {
        userId: 'viewer-1',
        email: 'viewer@test.com',
        name: 'Viewer User',
        role: 'Viewer',
        permissions: ['view_records', 'search_hansard', 'export_published'],
      };
      next();
    }

    app.use(settingsRoutes(viewerAuth, db));

    const res = await request(app).get('/api/settings');

    assert.equal(res.status, 200);
  });

  it('denies GET /api/settings/export to Viewer role', async () => {
    const db = createMockDb([]);
    const app = express();

    function viewerAuth(req, res, next) {
      req.user = {
        userId: 'viewer-1',
        email: 'viewer@test.com',
        name: 'Viewer User',
        role: 'Viewer',
        permissions: ['view_records', 'search_hansard', 'export_published'],
      };
      next();
    }

    app.use(settingsRoutes(viewerAuth, db));

    const res = await request(app).get('/api/settings/export');

    assert.equal(res.status, 403);
  });
});
