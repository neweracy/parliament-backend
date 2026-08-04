'use strict';

/**
 * Unit tests for middleware/cors-policy.js — Exact CORS Policy.
 *
 * Tests exact-origin matching, preflight accept/reject, credentials=false,
 * no wildcard origins, and omission of headers on mismatch.
 *
 * Validates: Requirements 13.9–13.16
 *
 * @module test/middleware/cors-policy
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const createCorsPolicy = require('../../middleware/cors-policy');

/**
 * Creates a minimal Express app with the CORS policy middleware
 * and a test handler that returns 200 OK.
 */
function buildApp(options = {}) {
  const app = express();
  app.use(createCorsPolicy(options));

  app.get('/api/test', (_req, res) => res.status(200).json({ ok: true }));
  app.post('/api/auth/login', (_req, res) => res.status(200).json({ ok: true }));
  app.put('/api/test', (_req, res) => res.status(200).json({ ok: true }));
  app.delete('/api/test', (_req, res) => res.status(200).json({ ok: true }));

  return app;
}

// ==========================================================================
// Origin allowlist loading (Requirement 13.9)
// ==========================================================================

describe('CORS Policy — Origin allowlist (Req 13.9)', () => {
  it('loads origins from options.origins', async () => {
    const app = buildApp({ origins: 'http://example.com' });
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://example.com');

    assert.equal(res.headers['access-control-allow-origin'], 'http://example.com');
  });

  it('supports comma-separated multiple origins', async () => {
    const app = buildApp({ origins: 'http://a.com, http://b.com' });

    const res1 = await request(app)
      .get('/api/test')
      .set('Origin', 'http://a.com');
    assert.equal(res1.headers['access-control-allow-origin'], 'http://a.com');

    const res2 = await request(app)
      .get('/api/test')
      .set('Origin', 'http://b.com');
    assert.equal(res2.headers['access-control-allow-origin'], 'http://b.com');
  });

  it('defaults to http://localhost:5173 when no origins configured', async () => {
    // Save and clear env
    const prev = process.env.FRONTEND_ORIGINS;
    delete process.env.FRONTEND_ORIGINS;

    const app = buildApp({});
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173');

    assert.equal(res.headers['access-control-allow-origin'], 'http://localhost:5173');

    // Restore env
    if (prev !== undefined) process.env.FRONTEND_ORIGINS = prev;
  });
});

// ==========================================================================
// Exact byte-for-byte origin matching (Requirements 13.10, 13.11)
// ==========================================================================

describe('CORS Policy — Exact origin matching (Req 13.10, 13.11)', () => {
  const app = buildApp({ origins: 'http://localhost:5173' });

  it('returns Access-Control-Allow-Origin for exact match', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173');

    assert.equal(res.headers['access-control-allow-origin'], 'http://localhost:5173');
  });

  it('omits Access-Control-Allow-Origin when origin differs by port', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:3000');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('omits Access-Control-Allow-Origin when origin differs by scheme', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'https://localhost:5173');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('omits Access-Control-Allow-Origin when origin differs by case', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://LOCALHOST:5173');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('omits Access-Control-Allow-Origin when origin has trailing slash', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173/');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('omits Access-Control-Allow-Origin when origin has path', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173/app');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('omits Access-Control-Allow-Origin for null origin (Req 13.14)', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'null');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('sets Vary: Origin when origin matches', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173');

    assert.equal(res.headers['vary'], 'Origin');
  });

  it('does not set CORS headers when no Origin header present', async () => {
    const res = await request(app)
      .get('/api/test');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
    assert.equal(res.headers['vary'], undefined);
  });
});

// ==========================================================================
// Preflight accept (Requirement 13.12)
// ==========================================================================

describe('CORS Policy — Preflight accept (Req 13.12)', () => {
  const app = buildApp({ origins: 'http://localhost:5173' });

  it('returns 204 with methods/headers for valid preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST')
      .set('Access-Control-Request-Headers', 'Content-Type');

    assert.equal(res.status, 204);
    assert.equal(res.headers['access-control-allow-origin'], 'http://localhost:5173');
    assert.ok(res.headers['access-control-allow-methods'].includes('POST'));
    assert.ok(res.headers['access-control-allow-headers'].includes('Content-Type'));
    assert.equal(res.headers['access-control-max-age'], '86400');
  });

  it('accepts GET method in preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'GET');

    assert.equal(res.status, 204);
  });

  it('accepts DELETE method in preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'DELETE');

    assert.equal(res.status, 204);
  });

  it('accepts Authorization header in preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'GET')
      .set('Access-Control-Request-Headers', 'Authorization');

    assert.equal(res.status, 204);
    assert.ok(res.headers['access-control-allow-headers'].includes('Authorization'));
  });

  it('accepts multiple valid headers in preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST')
      .set('Access-Control-Request-Headers', 'Content-Type, Authorization, X-Request-ID');

    assert.equal(res.status, 204);
  });

  it('header matching is case-insensitive', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST')
      .set('Access-Control-Request-Headers', 'content-type, authorization');

    assert.equal(res.status, 204);
  });
});

// ==========================================================================
// Preflight reject (Requirement 13.13)
// ==========================================================================

describe('CORS Policy — Preflight reject (Req 13.13)', () => {
  const app = buildApp({ origins: 'http://localhost:5173' });

  it('returns 403 for unknown method in preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'TRACE');

    assert.equal(res.status, 403);
    assert.equal(res.headers['access-control-allow-methods'], undefined);
    assert.equal(res.headers['access-control-allow-headers'], undefined);
  });

  it('returns 403 for unknown header in preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST')
      .set('Access-Control-Request-Headers', 'X-Custom-Evil');

    assert.equal(res.status, 403);
    assert.equal(res.headers['access-control-allow-methods'], undefined);
  });

  it('returns 403 when preflight mixes valid and invalid headers', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST')
      .set('Access-Control-Request-Headers', 'Content-Type, X-Bad-Header');

    assert.equal(res.status, 403);
  });

  it('returns 403 for preflight from mismatched origin', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://evil.com')
      .set('Access-Control-Request-Method', 'POST');

    assert.equal(res.status, 403);
    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });
});

// ==========================================================================
// Credentials disabled (Requirement 13.15)
// ==========================================================================

describe('CORS Policy — Credentials disabled (Req 13.15)', () => {
  const app = buildApp({ origins: 'http://localhost:5173' });

  it('does not set Access-Control-Allow-Credentials on simple request', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173');

    assert.equal(res.headers['access-control-allow-credentials'], undefined);
  });

  it('does not set Access-Control-Allow-Credentials on preflight', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST');

    assert.equal(res.headers['access-control-allow-credentials'], undefined);
  });
});

// ==========================================================================
// No wildcards (Requirement 13.16)
// ==========================================================================

describe('CORS Policy — No wildcards (Req 13.16)', () => {
  const app = buildApp({ origins: 'http://localhost:5173' });

  it('never returns wildcard * in Access-Control-Allow-Origin', async () => {
    const res = await request(app)
      .get('/api/test')
      .set('Origin', 'http://localhost:5173');

    assert.notEqual(res.headers['access-control-allow-origin'], '*');
  });

  it('never returns wildcard * in Access-Control-Allow-Methods', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST');

    assert.notEqual(res.headers['access-control-allow-methods'], '*');
  });

  it('never returns wildcard * in Access-Control-Allow-Headers', async () => {
    const res = await request(app)
      .options('/api/test')
      .set('Origin', 'http://localhost:5173')
      .set('Access-Control-Request-Method', 'POST')
      .set('Access-Control-Request-Headers', 'Content-Type');

    assert.notEqual(res.headers['access-control-allow-headers'], '*');
  });
});

// ==========================================================================
// Auth/Protected API endpoints (Requirements 13.14, 13.16)
// ==========================================================================

describe('CORS Policy — Auth endpoint origin enforcement (Req 13.14)', () => {
  const app = buildApp({ origins: 'http://localhost:5173' });

  it('omits CORS headers for non-listed origin on auth endpoint', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .set('Origin', 'http://malicious.com')
      .set('Content-Type', 'application/json')
      .send('{}');

    assert.equal(res.headers['access-control-allow-origin'], undefined);
  });

  it('sets correct origin on auth endpoint for listed origin', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .set('Origin', 'http://localhost:5173')
      .set('Content-Type', 'application/json')
      .send('{}');

    assert.equal(res.headers['access-control-allow-origin'], 'http://localhost:5173');
  });
});
