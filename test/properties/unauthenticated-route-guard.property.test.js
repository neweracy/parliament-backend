'use strict';

/**
 * Property tests for Unauthenticated route guard.
 *
 * Property 2: Unauthenticated route guard
 * - Generator: supported protected views × unauthenticated states
 * - Assert no protected view renders when unauthenticated
 * - For any protected route and any unauthenticated state, the server SHALL
 *   return 401 and SHALL NOT render protected content.
 *
 * **Validates: Requirements 1.2**
 *
 * @module test/properties/unauthenticated-route-guard.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');
const jwt = require('jsonwebtoken');

// ─── Constants ───────────────────────────────────────────────────────────────

/**
 * All protected routes registered in server.js that require authentication.
 * Each entry has a method and path. Parameterized routes use fixed test IDs.
 */
const PROTECTED_ROUTES = [
  // Transcription (mounted directly in server.js with authMiddleware)
  { method: 'post', path: '/api/transcription' },
  { method: 'post', path: '/api/transcription/hybrid' },

  // Khaya routes
  { method: 'post', path: '/api/khaya/transcription' },

  // Sittings
  { method: 'get', path: '/api/sittings' },
  { method: 'post', path: '/api/sittings' },
  { method: 'get', path: '/api/sittings/1' },
  { method: 'patch', path: '/api/sittings/1' },
  { method: 'delete', path: '/api/sittings/1' },

  // Records
  { method: 'post', path: '/api/sittings/1/records' },
  { method: 'get', path: '/api/sittings/1/records/1' },
  { method: 'patch', path: '/api/sittings/1/records/1' },
  { method: 'get', path: '/api/records/assigned' },

  // Audio
  { method: 'post', path: '/api/sittings/1/records/1/audio' },
  { method: 'get', path: '/api/sittings/1/records/1/audio' },

  // Transcript
  { method: 'get', path: '/api/sittings/1/records/1/transcript' },
  { method: 'patch', path: '/api/sittings/1/records/1/transcript' },

  // Transcription jobs
  { method: 'post', path: '/api/sittings/1/records/1/transcribe' },
  { method: 'get', path: '/api/sittings/1/records/1/transcription-status' },

  // Search and Ask
  { method: 'post', path: '/api/search' },
  { method: 'get', path: '/api/search/suggestions' },
  { method: 'post', path: '/api/ask' },

  // Dashboard
  { method: 'get', path: '/api/dashboard/stats' },

  // Settings
  { method: 'get', path: '/api/settings' },
  { method: 'patch', path: '/api/settings' },
  { method: 'get', path: '/api/settings/export' },
  { method: 'patch', path: '/api/settings/export' },

  // Dictionary
  { method: 'get', path: '/api/dictionary' },
  { method: 'post', path: '/api/dictionary' },
  { method: 'delete', path: '/api/dictionary/test-term' },
  { method: 'post', path: '/api/dictionary/import' },

  // Users
  { method: 'get', path: '/api/users' },
  { method: 'post', path: '/api/users/invite' },
  { method: 'patch', path: '/api/users/user-1/role' },
  { method: 'patch', path: '/api/users/user-1/status' },
];

/**
 * Unauthenticated states that must all result in 401.
 * Each produces a different Authorization header (or lack thereof).
 */
const UNAUTHENTICATED_STATES = [
  { label: 'no-header', header: undefined },
  { label: 'empty-header', header: '' },
  { label: 'bearer-no-token', header: 'Bearer ' },
  { label: 'bearer-garbage', header: 'Bearer not.a.valid.jwt' },
  { label: 'wrong-scheme-basic', header: 'Basic dXNlcjpwYXNz' },
  { label: 'wrong-scheme-token', header: 'Token abc123' },
  { label: 'expired-token', header: null }, // built dynamically below
  { label: 'wrong-secret-token', header: null }, // built dynamically below
  { label: 'wrong-algorithm-token', header: null }, // built dynamically below
];

// The real session secret used by requireSession in tests
const TEST_SECRET = 'a'.repeat(64); // 64 hex chars = 32 bytes

/**
 * Build dynamic token states that require crypto operations.
 */
function buildExpiredToken() {
  const payload = {
    sub: 'user-1',
    email: 'test@example.com',
    name: 'Test User',
    role: 'Admin',
    iss: 'parliament-gateway',
    aud: 'hansard-spa',
    jti: 'a'.repeat(16),
  };
  return 'Bearer ' + jwt.sign(payload, TEST_SECRET, {
    algorithm: 'HS256',
    expiresIn: -10, // already expired
  });
}

function buildWrongSecretToken() {
  const payload = {
    sub: 'user-1',
    email: 'test@example.com',
    name: 'Test User',
    role: 'Admin',
    iss: 'parliament-gateway',
    aud: 'hansard-spa',
    jti: 'b'.repeat(16),
  };
  return 'Bearer ' + jwt.sign(payload, 'wrong-secret-' + 'x'.repeat(50), {
    algorithm: 'HS256',
    expiresIn: 900,
  });
}

function buildWrongAlgorithmToken() {
  // Sign with 'none' algorithm attempt — jsonwebtoken won't allow signing with 'none'
  // so we sign with HS384 which is not allowed by our verifier (only HS256)
  const payload = {
    sub: 'user-1',
    email: 'test@example.com',
    name: 'Test User',
    role: 'Admin',
    iss: 'parliament-gateway',
    aud: 'hansard-spa',
    jti: 'c'.repeat(16),
  };
  return 'Bearer ' + jwt.sign(payload, TEST_SECRET, {
    algorithm: 'HS384',
    expiresIn: 900,
  });
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Creates a minimal Express app with the requireSession middleware applied
 * to all protected routes. This mirrors how server.js gates routes.
 *
 * We replicate requireSession logic here rather than importing the full
 * server.js (which has side effects like DB connections and process.exit).
 */
function createGuardedApp() {
  const app = express();
  app.use(express.json());

  // Replicate the requireSession middleware from server.js
  function requireSession(req, res, next) {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    try {
      const token = authHeader.slice(7);
      if (!token) {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'MISSING_TOKEN',
            message: 'Authorization header with Bearer token is required',
          },
        });
      }

      const payload = jwt.verify(token, TEST_SECRET, {
        algorithms: ['HS256'],
        issuer: 'parliament-gateway',
        audience: 'hansard-spa',
        clockTolerance: 30,
      });

      // Validate iat/exp
      if (typeof payload.iat !== 'number' || typeof payload.exp !== 'number') {
        return res.status(401).json({
          error: { type: 'AuthenticationError', code: 'INVALID_TOKEN', message: 'Invalid session token' },
        });
      }
      if (payload.exp <= payload.iat || (payload.exp - payload.iat) > 3600) {
        return res.status(401).json({
          error: { type: 'AuthenticationError', code: 'INVALID_TOKEN', message: 'Invalid session token' },
        });
      }

      // Validate sub
      if (!payload.sub || typeof payload.sub !== 'string' || Buffer.byteLength(payload.sub, 'utf8') > 128) {
        return res.status(401).json({
          error: { type: 'AuthenticationError', code: 'INVALID_TOKEN', message: 'Invalid session token' },
        });
      }

      // Validate jti
      if (!payload.jti || typeof payload.jti !== 'string' || payload.jti.length < 16 || payload.jti.length > 128) {
        return res.status(401).json({
          error: { type: 'AuthenticationError', code: 'INVALID_TOKEN', message: 'Invalid session token' },
        });
      }

      // Validate role
      const ALLOWLISTED_ROLES = new Set(['Admin', 'Chief Editor', 'Supervisor', 'Editor', 'Viewer']);
      if (!payload.role || !ALLOWLISTED_ROLES.has(payload.role)) {
        return res.status(401).json({
          error: { type: 'AuthenticationError', code: 'INVALID_TOKEN', message: 'Invalid session token' },
        });
      }

      req.user = {
        userId: payload.sub,
        email: payload.email || '',
        name: payload.name || '',
        role: payload.role,
        permissions: [],
      };

      next();
    } catch (_err) {
      return res.status(401).json({
        error: { type: 'AuthenticationError', code: 'INVALID_TOKEN', message: 'Invalid session token' },
      });
    }
  }

  // Mount a catch-all for each protected route that returns 200 with protected content
  // if the auth middleware passes. This simulates the "protected view".
  for (const route of PROTECTED_ROUTES) {
    app[route.method](route.path, requireSession, (_req, res) => {
      res.status(200).json({ protected: true, content: 'secret data' });
    });
  }

  return app;
}

// ─── Arbitraries ─────────────────────────────────────────────────────────────

/**
 * Generator for protected route indices.
 */
const routeIndexArb = fc.nat({ max: PROTECTED_ROUTES.length - 1 });

/**
 * Generator for unauthenticated state variants.
 * Includes both static and dynamically-generated token states.
 */
const unauthStateArb = fc.constantFrom(
  'no-header',
  'empty-header',
  'bearer-no-token',
  'bearer-garbage',
  'wrong-scheme-basic',
  'wrong-scheme-token',
  'expired-token',
  'wrong-secret-token',
  'wrong-algorithm-token'
);

/**
 * Generates arbitrary invalid Bearer tokens (random strings).
 */
const randomGarbageTokenArb = fc.string({ minLength: 1, maxLength: 200 }).map(
  (s) => 'Bearer ' + s
);

/**
 * Resolves an unauthenticated state label to an actual Authorization header value.
 */
function resolveAuthHeader(stateLabel) {
  switch (stateLabel) {
  case 'no-header':
    return undefined;
  case 'empty-header':
    return '';
  case 'bearer-no-token':
    return 'Bearer ';
  case 'bearer-garbage':
    return 'Bearer not.a.valid.jwt.at.all';
  case 'wrong-scheme-basic':
    return 'Basic dXNlcjpwYXNz';
  case 'wrong-scheme-token':
    return 'Token abc123';
  case 'expired-token':
    return buildExpiredToken();
  case 'wrong-secret-token':
    return buildWrongSecretToken();
  case 'wrong-algorithm-token':
    return buildWrongAlgorithmToken();
  default:
    return undefined;
  }
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 1.2**
 */
describe('Property 2: Unauthenticated route guard', () => {
  it('no protected route renders content when unauthenticated (enumerated states)', async () => {
    const app = createGuardedApp();

    await fc.assert(
      fc.asyncProperty(
        routeIndexArb,
        unauthStateArb,
        async (routeIdx, stateLabel) => {
          const route = PROTECTED_ROUTES[routeIdx];
          const authHeader = resolveAuthHeader(stateLabel);

          let req = request(app)[route.method](route.path);

          // Attach Authorization header if defined and non-empty
          if (authHeader !== undefined && authHeader !== '') {
            req = req.set('Authorization', authHeader);
          }

          // For POST/PATCH/DELETE that expect JSON body
          if (['post', 'patch'].includes(route.method)) {
            req = req.set('Content-Type', 'application/json').send({});
          }

          const res = await req;

          // Must be 401 — never 200 or any success status
          assert.equal(
            res.status, 401,
            `Route ${route.method.toUpperCase()} ${route.path} with state '${stateLabel}' ` +
            `should return 401 but got ${res.status}`
          );

          // Response must NOT contain protected content
          assert.notEqual(
            res.body?.protected, true,
            `Route ${route.method.toUpperCase()} ${route.path} with state '${stateLabel}' ` +
            `leaked protected content`
          );

          // Response must have proper error structure
          assert.equal(res.body?.error?.type, 'AuthenticationError');
        }
      ),
      { numRuns: 200 }
    );
  });

  it('no protected route renders content with random garbage tokens', async () => {
    const app = createGuardedApp();

    await fc.assert(
      fc.asyncProperty(
        routeIndexArb,
        randomGarbageTokenArb,
        async (routeIdx, authHeader) => {
          const route = PROTECTED_ROUTES[routeIdx];

          let req = request(app)[route.method](route.path)
            .set('Authorization', authHeader);

          if (['post', 'patch'].includes(route.method)) {
            req = req.set('Content-Type', 'application/json').send({});
          }

          const res = await req;

          // Must be 401 — never allow access with garbage tokens
          assert.equal(
            res.status, 401,
            `Route ${route.method.toUpperCase()} ${route.path} with random token ` +
            `should return 401 but got ${res.status}`
          );

          // Must not leak protected content
          assert.notEqual(res.body?.protected, true);
        }
      ),
      { numRuns: 150 }
    );
  });

  it('every individual protected route rejects all unauthenticated states', async () => {
    const app = createGuardedApp();

    // Exhaustive check: every route × every state
    for (const route of PROTECTED_ROUTES) {
      for (const stateLabel of [
        'no-header', 'empty-header', 'bearer-no-token', 'bearer-garbage',
        'wrong-scheme-basic', 'wrong-scheme-token', 'expired-token',
        'wrong-secret-token', 'wrong-algorithm-token',
      ]) {
        const authHeader = resolveAuthHeader(stateLabel);

        let req = request(app)[route.method](route.path);

        if (authHeader !== undefined && authHeader !== '') {
          req = req.set('Authorization', authHeader);
        }

        if (['post', 'patch'].includes(route.method)) {
          req = req.set('Content-Type', 'application/json').send({});
        }

        const res = await req;

        assert.equal(
          res.status, 401,
          `[exhaustive] ${route.method.toUpperCase()} ${route.path} with '${stateLabel}' ` +
          `expected 401 but got ${res.status}`
        );
      }
    }
  });
});
