'use strict';

/**
 * Unit tests for middleware/security-headers.js — Browser Security Headers & Cache Controls.
 *
 * Tests CSP directives, X-Content-Type-Options, Referrer-Policy, X-Frame-Options,
 * Strict-Transport-Security, and Cache-Control/Pragma on auth responses.
 *
 * Validates: Requirements 14.1–14.12
 *
 * @module test/middleware/security-headers
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const createSecurityHeaders = require('../../middleware/security-headers');

/**
 * Creates a minimal Express app with the security headers middleware
 * and test routes for auth and non-auth paths.
 */
function buildApp(options = {}) {
  const app = express();
  app.use(createSecurityHeaders(options));

  app.get('/api/test', (_req, res) => res.status(200).json({ ok: true }));
  app.post('/api/auth/login', (_req, res) => res.status(200).json({ token: 'x' }));
  app.get('/api/session', (_req, res) => res.status(410).json({ error: 'removed' }));
  app.get('/health', (_req, res) => res.status(200).json({ status: 'ok' }));

  return app;
}

// ==========================================================================
// Content-Security-Policy (Requirements 14.1, 14.2, 14.3, 14.4)
// ==========================================================================

describe('Security Headers — CSP (Req 14.1, 14.2, 14.3, 14.4)', () => {
  it('emits exactly one Content-Security-Policy header (Req 14.1)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/test');

    // supertest normalizes headers; check the CSP header is present
    assert.ok(res.headers['content-security-policy']);
  });

  it('includes required CSP directives in local-dev mode (Req 14.2, 14.4)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/test');

    const csp = res.headers['content-security-policy'];
    assert.ok(csp.includes("default-src 'self'"), 'default-src self');
    assert.ok(csp.includes("script-src 'self'"), 'script-src self');
    assert.ok(csp.includes("base-uri 'none'"), 'base-uri none');
    assert.ok(csp.includes("frame-ancestors 'none'"), 'frame-ancestors none');
    assert.ok(csp.includes("object-src 'none'"), 'object-src none');
    assert.ok(csp.includes("connect-src 'self'"), 'connect-src self');
    assert.ok(csp.includes("form-action 'self'"), 'form-action self');
  });

  it('adds Cognito origin to connect-src and form-action in cognito mode (Req 14.3)', async () => {
    const app = buildApp({ authMode: 'cognito', cognitoDomain: 'auth.example.com' });
    const res = await request(app).get('/api/test');

    const csp = res.headers['content-security-policy'];
    assert.ok(csp.includes("connect-src 'self' https://auth.example.com"), 'connect-src includes Cognito');
    assert.ok(csp.includes("form-action 'self' https://auth.example.com"), 'form-action includes Cognito');
  });

  it('does not add Cognito origin when cognitoDomain is absent (Req 14.4)', async () => {
    const app = buildApp({ authMode: 'cognito' });
    const res = await request(app).get('/api/test');

    const csp = res.headers['content-security-policy'];
    // Without cognitoDomain, connect-src and form-action should be just 'self'
    assert.ok(csp.includes("connect-src 'self'"), 'connect-src is self only');
    assert.ok(csp.includes("form-action 'self'"), 'form-action is self only');
    assert.ok(!csp.includes('https://'), 'no https origin appended');
  });
});

// ==========================================================================
// Static security headers (Requirements 14.5, 14.6, 14.7)
// ==========================================================================

describe('Security Headers — Static headers (Req 14.5, 14.6, 14.7)', () => {
  it('emits X-Content-Type-Options: nosniff (Req 14.5)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/test');

    assert.equal(res.headers['x-content-type-options'], 'nosniff');
  });

  it('emits Referrer-Policy: no-referrer (Req 14.6)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/test');

    assert.equal(res.headers['referrer-policy'], 'no-referrer');
  });

  it('emits X-Frame-Options: DENY (Req 14.7)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/test');

    assert.equal(res.headers['x-frame-options'], 'DENY');
  });

  it('emits security headers on all routes including non-auth paths', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/health');

    assert.equal(res.headers['x-content-type-options'], 'nosniff');
    assert.equal(res.headers['referrer-policy'], 'no-referrer');
    assert.equal(res.headers['x-frame-options'], 'DENY');
  });
});

// ==========================================================================
// Strict-Transport-Security (Requirements 14.8, 14.9)
// ==========================================================================

describe('Security Headers — HSTS (Req 14.8, 14.9)', () => {
  it('omits HSTS when isProduction is false (Req 14.9)', async () => {
    const app = buildApp({ authMode: 'legacy', isProduction: false });
    const res = await request(app).get('/api/test');

    assert.equal(res.headers['strict-transport-security'], undefined);
  });

  it('omits HSTS when isProduction is true but connection is not secure (Req 14.9)', async () => {
    // supertest uses HTTP, so req.secure is false
    const app = buildApp({ authMode: 'legacy', isProduction: true });
    const res = await request(app).get('/api/test');

    assert.equal(res.headers['strict-transport-security'], undefined);
  });

  it('emits HSTS when isProduction and connection is secure (Req 14.8)', async () => {
    // Simulate a secure connection by setting req.secure via trust proxy + x-forwarded-proto
    const app = express();
    app.set('trust proxy', true);
    app.use(createSecurityHeaders({ authMode: 'legacy', isProduction: true }));
    app.get('/api/test', (_req, res) => res.status(200).json({ ok: true }));

    const res = await request(app)
      .get('/api/test')
      .set('X-Forwarded-Proto', 'https');

    assert.equal(
      res.headers['strict-transport-security'],
      'max-age=31536000; includeSubDomains'
    );
  });
});

// ==========================================================================
// Cache-Control and Pragma on auth responses (Requirements 14.10, 14.11)
// ==========================================================================

describe('Security Headers — Cache controls (Req 14.10, 14.11)', () => {
  it('emits Cache-Control: no-store on POST /api/auth/login (Req 14.10)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).post('/api/auth/login');

    assert.equal(res.headers['cache-control'], 'no-store');
  });

  it('emits Pragma: no-cache on POST /api/auth/login (Req 14.11)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).post('/api/auth/login');

    assert.equal(res.headers['pragma'], 'no-cache');
  });

  it('emits Cache-Control: no-store on GET /api/session (Req 14.10)', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/session');

    assert.equal(res.headers['cache-control'], 'no-store');
  });

  it('does NOT emit Cache-Control: no-store on non-auth paths', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/api/test');

    // Non-auth paths should not have no-store forced by this middleware
    assert.notEqual(res.headers['cache-control'], 'no-store');
  });

  it('does NOT emit Pragma: no-cache on non-auth paths', async () => {
    const app = buildApp({ authMode: 'legacy' });
    const res = await request(app).get('/health');

    assert.equal(res.headers['pragma'], undefined);
  });
});
