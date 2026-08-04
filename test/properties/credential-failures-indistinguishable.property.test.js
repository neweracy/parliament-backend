'use strict';

/**
 * Property tests for credential failure indistinguishability.
 *
 * Feature: dev-login-page, Property 5: Credential failures are publicly indistinguishable
 *
 * For any credential failure cause in {unknown user, wrong password, inactive user,
 * missing local hash, unsupported stored role, invalid hash format}, the endpoint
 * SHALL perform exactly one real-or-dummy bcrypt comparison and return the same
 * HTTP 401 status, public code, and message without revealing account existence.
 *
 * **Validates: Requirement 3.1**
 *
 * @module test/properties/credential-failures-indistinguishable.property
 */

const { describe, it, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

// Mock the rate-limiter to eliminate progressive delays that would make tests slow
const rateLimiterModule = require('../../middleware/rate-limiter');
const originalGetProgressiveDelay = rateLimiterModule.getProgressiveDelay;
const originalRecordFailure = rateLimiterModule.recordFailure;

// Override to disable delays — we test rate limiting separately
rateLimiterModule.getProgressiveDelay = () => 0;
rateLimiterModule.recordFailure = () => {};

// ─── Constants ───────────────────────────────────────────────────────────────

/**
 * The exact credential failure body that must be returned for ALL failure causes.
 */
const EXPECTED_FAILURE_BODY = {
  error: {
    type: 'AuthenticationError',
    code: 'INVALID_CREDENTIALS',
    message: 'Invalid email or password',
  },
};

/**
 * A valid bcrypt hash at cost 12 for the password "correctpassword".
 * Pre-generated for test fixture use.
 */
const VALID_BCRYPT_HASH = '$2b$12$LJ3m4sMKfXzSgyGNpOzU0OjGjFMGHzsr8g97sF7DTsVLkN7N5.3Ce';

/**
 * Dummy bcrypt hash at cost 12 (matches configured cost in tests).
 */
const DUMMY_HASH = '$2b$12$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ012345';

/**
 * All credential failure causes we test.
 */
const FAILURE_CAUSES = [
  'unknown_user',
  'wrong_password',
  'inactive_user',
  'missing_hash',
  'unsupported_role',
  'invalid_hash_format',
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Creates a user row fixture based on the failure cause.
 *
 * @param {string} cause - The failure cause
 * @returns {{ rows: Array }} - Mock DB query result
 */
function createDbResultForCause(cause) {
  switch (cause) {
    case 'unknown_user':
      return { rows: [] };

    case 'wrong_password':
      return {
        rows: [{
          id: 1,
          email: 'test@parliament.gov.gh',
          name: 'Test User',
          role: 'Editor',
          status: 'Active',
          password_hash: VALID_BCRYPT_HASH,
        }],
      };

    case 'inactive_user':
      return {
        rows: [{
          id: 2,
          email: 'test@parliament.gov.gh',
          name: 'Inactive User',
          role: 'Editor',
          status: 'Suspended',
          password_hash: VALID_BCRYPT_HASH,
        }],
      };

    case 'missing_hash':
      return {
        rows: [{
          id: 3,
          email: 'test@parliament.gov.gh',
          name: 'No Hash User',
          role: 'Editor',
          status: 'Active',
          password_hash: null,
        }],
      };

    case 'unsupported_role':
      return {
        rows: [{
          id: 4,
          email: 'test@parliament.gov.gh',
          name: 'Bad Role User',
          role: 'ExternalAuditor',
          status: 'Active',
          password_hash: VALID_BCRYPT_HASH,
        }],
      };

    case 'invalid_hash_format':
      return {
        rows: [{
          id: 5,
          email: 'test@parliament.gov.gh',
          name: 'Bad Hash User',
          role: 'Editor',
          status: 'Active',
          password_hash: 'plaintext_not_bcrypt_hash',
        }],
      };

    default:
      return { rows: [] };
  }
}

/**
 * Creates a test Express app with the auth route, using mocked dependencies.
 *
 * @param {object} options
 * @param {Function} options.dbQuery - Mock db.query function
 * @param {Function} options.bcryptCompare - Mock bcrypt.compare function
 * @returns {object} - { app, bcryptCompare }
 */
function createTestApp(options) {
  const { dbQuery, bcryptCompare } = options;

  // We need to intercept bcrypt.compare — the auth route uses it from the bcrypt module.
  // We'll monkey-patch bcrypt in the module cache.
  const bcrypt = require('bcrypt');
  const originalCompare = bcrypt.compare;
  bcrypt.compare = bcryptCompare;

  // Create a mock DB pool
  const db = { query: dbQuery };

  // Create the auth router with test options
  const authRoutes = require('../../routes/auth');
  const router = authRoutes(db, {
    sessionSecret: 'a'.repeat(64), // 64-char hex string
    jwtLifetime: 900,
    bcryptCost: 12,
    dummyHash: DUMMY_HASH,
  });

  const app = express();
  app.use(router);

  // Return cleanup function
  return {
    app,
    cleanup: () => {
      bcrypt.compare = originalCompare;
    },
  };
}

/**
 * Simulates the login-validator middleware by directly setting the expected
 * req properties that the auth handler consumes.
 *
 * We bypass the raw body parsing by pre-mounting middleware that sets the
 * normalized values.
 */
function createTestAppWithBypassedValidator(options) {
  const { dbQuery, bcryptCompare, email, password } = options;

  const bcrypt = require('bcrypt');
  const originalCompare = bcrypt.compare;
  bcrypt.compare = bcryptCompare;

  const db = { query: dbQuery };

  const authRoutes = require('../../routes/auth');
  const router = authRoutes(db, {
    sessionSecret: 'a'.repeat(64),
    jwtLifetime: 900,
    bcryptCost: 12,
    dummyHash: DUMMY_HASH,
  });

  const app = express();

  // Bypass login-validator and rate-limiter by pre-setting expected properties
  // and sending a raw POST that the real validator would accept
  app.use(router);

  return {
    app,
    cleanup: () => {
      bcrypt.compare = originalCompare;
    },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirement 3.1**
 */
describe('Property 5: Credential failures are publicly indistinguishable', () => {
  let bcryptModule;
  let originalCompare;

  beforeEach(() => {
    bcryptModule = require('bcrypt');
    originalCompare = bcryptModule.compare;
  });

  afterEach(() => {
    bcryptModule.compare = originalCompare;
  });

  it('all credential failure causes return identical 401 status, body, and exactly one bcrypt call', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom(...FAILURE_CAUSES),
        async (failureCause) => {
          let bcryptCallCount = 0;

          // Mock bcrypt.compare to track calls and control outcome
          const mockBcryptCompare = async (_password, _hash) => {
            bcryptCallCount++;
            // For wrong_password cause, user exists with valid hash but password doesn't match
            // For all other causes with valid hash, it doesn't matter what bcrypt returns
            // because other conditions will fail the auth
            if (failureCause === 'wrong_password') {
              return false;
            }
            // For inactive_user and unsupported_role: hash is valid, bcrypt returns true
            // but other conditions prevent success
            if (failureCause === 'inactive_user' || failureCause === 'unsupported_role') {
              return true;
            }
            // For unknown_user, missing_hash, invalid_hash_format: uses dummy hash
            return false;
          };

          bcryptModule.compare = mockBcryptCompare;

          const dbQuery = async (_sql, _params) => {
            return createDbResultForCause(failureCause);
          };

          const db = { query: dbQuery };

          // Create fresh auth router for each iteration
          const authRoutes = require('../../routes/auth');
          const router = authRoutes(db, {
            sessionSecret: 'a'.repeat(64),
            jwtLifetime: 900,
            bcryptCost: 12,
            dummyHash: DUMMY_HASH,
          });

          const app = express();
          app.use(router);

          // Send a properly-formed login request via supertest
          // The loginValidator middleware will parse the raw body
          const res = await request(app)
            .post('/api/auth/login')
            .set('Content-Type', 'application/json')
            .send(JSON.stringify({ email: 'test@parliament.gov.gh', password: 'wrongpassword123' }));

          // Assert: HTTP 401 for all causes
          assert.equal(
            res.status, 401,
            `Cause '${failureCause}' should return 401, got ${res.status}`
          );

          // Assert: Body is byte-for-byte identical to CREDENTIAL_FAILURE_BODY
          assert.deepStrictEqual(
            res.body,
            EXPECTED_FAILURE_BODY,
            `Cause '${failureCause}' should return identical failure body`
          );

          // Assert: Exactly one bcrypt.compare call (either real or dummy)
          assert.equal(
            bcryptCallCount, 1,
            `Cause '${failureCause}' should make exactly 1 bcrypt.compare call, got ${bcryptCallCount}`
          );

          // Assert: No account-existence information leaks in response
          const responseText = JSON.stringify(res.body);
          assert.ok(
            !responseText.includes('not found'),
            `Cause '${failureCause}': response must not reveal user not found`
          );
          assert.ok(
            !responseText.includes('inactive'),
            `Cause '${failureCause}': response must not reveal inactive status`
          );
          assert.ok(
            !responseText.includes('role'),
            `Cause '${failureCause}': response must not reveal role issues`
          );
          assert.ok(
            !responseText.includes('hash'),
            `Cause '${failureCause}': response must not reveal hash issues`
          );
        }
      ),
      { numRuns: 30 }
    );
  });

  it('response body is byte-for-byte identical across all failure causes', async () => {
    const responses = [];

    for (const cause of FAILURE_CAUSES) {
      let bcryptCallCount = 0;

      const mockBcryptCompare = async (_password, _hash) => {
        bcryptCallCount++;
        if (cause === 'wrong_password') return false;
        if (cause === 'inactive_user' || cause === 'unsupported_role') return true;
        return false;
      };

      bcryptModule.compare = mockBcryptCompare;

      const dbQuery = async () => createDbResultForCause(cause);
      const db = { query: dbQuery };

      const authRoutes = require('../../routes/auth');
      const router = authRoutes(db, {
        sessionSecret: 'a'.repeat(64),
        jwtLifetime: 900,
        bcryptCost: 12,
        dummyHash: DUMMY_HASH,
      });

      const app = express();
      app.use(router);

      const res = await request(app)
        .post('/api/auth/login')
        .set('Content-Type', 'application/json')
        .send(JSON.stringify({ email: 'test@parliament.gov.gh', password: 'anypassword' }));

      responses.push({ cause, status: res.status, body: JSON.stringify(res.body), headers: res.headers });
    }

    // All statuses must be identical (401)
    const statuses = new Set(responses.map(r => r.status));
    assert.equal(statuses.size, 1, `All causes should return same status, got: ${[...statuses]}`);
    assert.ok(statuses.has(401), 'Status should be 401');

    // All bodies must be byte-for-byte identical
    const bodies = new Set(responses.map(r => r.body));
    assert.equal(bodies.size, 1, `All causes should return identical body, got ${bodies.size} variants`);

    // The single body must match expected
    assert.equal(
      [...bodies][0],
      JSON.stringify(EXPECTED_FAILURE_BODY),
      'Response body must match CREDENTIAL_FAILURE_BODY'
    );
  });

  it('no account existence info leaks in headers for any failure cause', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom(...FAILURE_CAUSES),
        async (failureCause) => {
          const mockBcryptCompare = async () => {
            if (failureCause === 'wrong_password') return false;
            if (failureCause === 'inactive_user' || failureCause === 'unsupported_role') return true;
            return false;
          };

          bcryptModule.compare = mockBcryptCompare;

          const dbQuery = async () => createDbResultForCause(failureCause);
          const db = { query: dbQuery };

          const authRoutes = require('../../routes/auth');
          const router = authRoutes(db, {
            sessionSecret: 'a'.repeat(64),
            jwtLifetime: 900,
            bcryptCost: 12,
            dummyHash: DUMMY_HASH,
          });

          const app = express();
          app.use(router);

          const res = await request(app)
            .post('/api/auth/login')
            .set('Content-Type', 'application/json')
            .send(JSON.stringify({ email: 'test@parliament.gov.gh', password: 'test' }));

          // No headers should reveal why authentication failed
          const headerString = JSON.stringify(res.headers).toLowerCase();
          assert.ok(
            !headerString.includes('user-not-found'),
            `Cause '${failureCause}': headers must not reveal user not found`
          );
          assert.ok(
            !headerString.includes('inactive'),
            `Cause '${failureCause}': headers must not reveal inactive status`
          );
          assert.ok(
            !headerString.includes('invalid-hash'),
            `Cause '${failureCause}': headers must not reveal hash issues`
          );
          assert.ok(
            !headerString.includes('unsupported-role'),
            `Cause '${failureCause}': headers must not reveal role issues`
          );
        }
      ),
      { numRuns: 18 }
    );
  });
});
