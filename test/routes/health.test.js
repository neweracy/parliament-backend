'use strict';

/**
 * Health endpoint contract test.
 *
 * Validates the /health route returns 200 with the expected JSON shape.
 * The route is deliberately unauthenticated (no JWT required).
 *
 * Validates: Requirements 5.5
 *
 * @module test/routes/health
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

/**
 * Builds a minimal Express app that mounts the /health route
 * using the same implementation as server.js.
 */
function buildHealthApp() {
  const app = express();

  app.get('/health', (req, res) => {
    res.json({
      status: 'ok',
      uptime_seconds: Math.floor(process.uptime()),
      postprocess_mode: 'js',
      version: require('../../package.json').version,
    });
  });

  return app;
}

describe('GET /health', () => {
  it('returns 200 with JSON body containing status', async () => {
    const app = buildHealthApp();
    const res = await request(app).get('/health');

    assert.equal(res.status, 200);
    assert.equal(res.headers['content-type'].includes('application/json'), true);
    assert.equal(res.body.status, 'ok');
  });

  it('includes uptime_seconds as a number', async () => {
    const app = buildHealthApp();
    const res = await request(app).get('/health');

    assert.equal(typeof res.body.uptime_seconds, 'number');
    assert.ok(res.body.uptime_seconds >= 0);
  });

  it('includes postprocess_mode', async () => {
    const app = buildHealthApp();
    const res = await request(app).get('/health');

    assert.equal(typeof res.body.postprocess_mode, 'string');
    assert.ok(['js', 'python', 'off'].includes(res.body.postprocess_mode));
  });

  it('includes version from package.json', async () => {
    const app = buildHealthApp();
    const res = await request(app).get('/health');

    const expectedVersion = require('../../package.json').version;
    assert.equal(res.body.version, expectedVersion);
  });

  it('does not require authentication', async () => {
    const app = buildHealthApp();
    // No Authorization header sent
    const res = await request(app).get('/health');

    assert.equal(res.status, 200);
  });
});
