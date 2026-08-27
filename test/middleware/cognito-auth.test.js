'use strict';

/**
 * Unit tests for middleware/cognito-auth.js — Cognito JWT validation.
 *
 * Tests token parsing, signature verification (mocked JWKS),
 * expiry checks, and group-to-role mapping.
 *
 * Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.9
 *
 * @module test/middleware/cognito-auth
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const jwt = require('jsonwebtoken');
const express = require('express');
const request = require('supertest');

const {
  resolveRole,
  getPermissionsForRole,
  GROUP_ROLE_MAP,
  ROLE_PERMISSIONS,
} = require('../../middleware/cognito-auth');

// --------------------------------------------------------------------------
// Test RSA key pair (generated at module load for signing test JWTs)
// --------------------------------------------------------------------------

const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', {
  modulusLength: 2048,
  publicKeyEncoding: { type: 'spki', format: 'pem' },
  privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
});

const TEST_CONFIG = {
  userPoolId: 'eu-west-1_TestPool',
  region: 'eu-west-1',
  appClientId: 'test-client-123',
};

const ISSUER = `https://cognito-idp.${TEST_CONFIG.region}.amazonaws.com/${TEST_CONFIG.userPoolId}`;

/**
 * Creates a signed JWT with the given payload overrides.
 */
function createToken(overrides = {}, headerOverrides = {}) {
  const payload = {
    sub: 'user-123-uuid',
    email: 'user@parliament.gov.gh',
    name: 'Test User',
    'cognito:groups': ['editor'],
    iss: ISSUER,
    aud: TEST_CONFIG.appClientId,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...overrides,
  };

  const header = {
    alg: 'RS256',
    kid: 'test-kid-001',
    ...headerOverrides,
  };

  return jwt.sign(payload, privateKey, { algorithm: 'RS256', header });
}

/**
 * Builds a minimal Express app with a mocked cognitoAuth middleware
 * that uses our test RSA key pair instead of fetching from JWKS.
 */
function buildApp() {
  const app = express();

  // Since jwks-rsa will fail (no real endpoint), we create a custom middleware
  // that replicates the logic but uses our local test key pair.
  const patchedMiddleware = createTestMiddleware();

  app.get('/protected', patchedMiddleware, (req, res) => {
    res.json({ user: req.user });
  });

  return app;
}

/**
 * Creates a test-friendly version of the cognitoAuth middleware that
 * uses the local test RSA keys instead of fetching from JWKS.
 */
function createTestMiddleware() {
  return async function cognitoAuth(req, res, next) {
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

    let decoded;
    try {
      decoded = jwt.decode(token, { complete: true });
    } catch (_err) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    if (!decoded || !decoded.header || !decoded.header.kid) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    // Reject tokens not using RS256 before key verification (defense-in-depth).
    if (decoded.header.alg !== 'RS256') {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    // Use the test public key
    let payload;
    try {
      payload = jwt.verify(token, publicKey, {
        algorithms: ['RS256'],
        issuer: ISSUER,
        audience: TEST_CONFIG.appClientId,
      });
    } catch (err) {
      if (err.name === 'TokenExpiredError') {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'TOKEN_EXPIRED',
            message: 'Session expired, please sign in again',
          },
        });
      }
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    const userId = payload.sub;
    const email = payload.email || '';
    const name = payload.name || payload['cognito:username'] || '';
    const groups = payload['cognito:groups'] || [];

    const role = resolveRole(groups);
    const permissions = getPermissionsForRole(role);

    req.user = { userId, email, name, role, permissions };
    next();
  };
}

// ==========================================================================
// resolveRole() unit tests
// ==========================================================================

describe('resolveRole()', () => {
  it('returns Viewer for empty groups', () => {
    assert.equal(resolveRole([]), 'Viewer');
  });

  it('returns Viewer for null/undefined groups', () => {
    assert.equal(resolveRole(null), 'Viewer');
    assert.equal(resolveRole(undefined), 'Viewer');
  });

  it('returns Admin for admin group', () => {
    assert.equal(resolveRole(['admin']), 'Admin');
  });

  it('returns Chief Editor for chief-editor group', () => {
    assert.equal(resolveRole(['chief-editor']), 'Chief Editor');
  });

  it('returns Supervisor for supervisor group', () => {
    assert.equal(resolveRole(['supervisor']), 'Supervisor');
  });

  it('returns Editor for editor group', () => {
    assert.equal(resolveRole(['editor']), 'Editor');
  });

  it('returns Viewer for viewer group', () => {
    assert.equal(resolveRole(['viewer']), 'Viewer');
  });

  it('returns highest-precedence role when user has multiple groups', () => {
    assert.equal(resolveRole(['editor', 'admin']), 'Admin');
    assert.equal(resolveRole(['viewer', 'supervisor', 'editor']), 'Supervisor');
    assert.equal(resolveRole(['chief-editor', 'editor']), 'Chief Editor');
  });

  it('returns Viewer for unrecognized groups', () => {
    assert.equal(resolveRole(['unknown-group', 'another-group']), 'Viewer');
  });

  it('ignores unrecognized groups and resolves by recognized ones', () => {
    assert.equal(resolveRole(['unknown', 'editor', 'foo']), 'Editor');
  });
});

// ==========================================================================
// getPermissionsForRole() unit tests
// ==========================================================================

describe('getPermissionsForRole()', () => {
  it('returns non-empty permissions for every defined role', () => {
    for (const role of ['Admin', 'Chief Editor', 'Supervisor', 'Editor', 'Viewer']) {
      const perms = getPermissionsForRole(role);
      assert.ok(perms.length > 0, `${role} should have at least one permission`);
    }
  });

  it('Admin has all permissions (superset of every other role)', () => {
    const adminPerms = new Set(getPermissionsForRole('Admin'));
    for (const [role, perms] of Object.entries(ROLE_PERMISSIONS)) {
      if (role === 'Admin') continue;
      for (const p of perms) {
        assert.ok(adminPerms.has(p), `Admin missing ${p} from ${role}`);
      }
    }
  });

  it('returns empty permissions for unknown roles (fail-closed)', () => {
    const perms = getPermissionsForRole('NonExistentRole');
    assert.deepEqual(perms, []);
  });
});

// ==========================================================================
// Middleware integration tests (with mocked JWKS via test key pair)
// ==========================================================================

describe('cognitoAuth middleware', () => {
  describe('MISSING_TOKEN errors', () => {
    it('returns 401 when no Authorization header', async () => {
      const app = buildApp();
      const res = await request(app).get('/protected');

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'MISSING_TOKEN');
      assert.equal(res.body.error.type, 'AuthenticationError');
    });

    it('returns 401 when Authorization header has no Bearer prefix', async () => {
      const app = buildApp();
      const res = await request(app)
        .get('/protected')
        .set('Authorization', 'Basic abc123');

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'MISSING_TOKEN');
    });

    it('returns 401 when Bearer token is empty', async () => {
      const app = buildApp();
      const res = await request(app)
        .get('/protected')
        .set('Authorization', 'Bearer ');

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'MISSING_TOKEN');
    });
  });

  describe('INVALID_TOKEN errors', () => {
    it('returns 401 for malformed JWT', async () => {
      const app = buildApp();
      const res = await request(app)
        .get('/protected')
        .set('Authorization', 'Bearer not-a-valid-jwt');

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'INVALID_TOKEN');
    });

    it('returns 401 when signature is invalid (wrong key)', async () => {
      const app = buildApp();
      // Sign with a different key
      const { privateKey: otherKey } = crypto.generateKeyPairSync('rsa', {
        modulusLength: 2048,
        publicKeyEncoding: { type: 'spki', format: 'pem' },
        privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
      });

      const badToken = jwt.sign(
        {
          sub: 'user-456',
          email: 'bad@example.com',
          name: 'Bad User',
          'cognito:groups': ['admin'],
          iss: ISSUER,
          aud: TEST_CONFIG.appClientId,
        },
        otherKey,
        { algorithm: 'RS256', header: { kid: 'test-kid-001', alg: 'RS256' } }
      );

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${badToken}`);

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'INVALID_TOKEN');
    });

    it('returns 401 when issuer does not match', async () => {
      const app = buildApp();
      const token = createToken({ iss: 'https://wrong-issuer.example.com' });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'INVALID_TOKEN');
    });

    it('returns 401 when audience does not match', async () => {
      const app = buildApp();
      const token = createToken({ aud: 'wrong-client-id' });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'INVALID_TOKEN');
    });
  });

  describe('TOKEN_EXPIRED errors', () => {
    it('returns 401 TOKEN_EXPIRED for expired tokens', async () => {
      const app = buildApp();
      const token = createToken({
        iat: Math.floor(Date.now() / 1000) - 7200,
        exp: Math.floor(Date.now() / 1000) - 3600, // expired 1 hour ago
      });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 401);
      assert.equal(res.body.error.code, 'TOKEN_EXPIRED');
      assert.match(res.body.error.message, /expired/i);
    });
  });

  describe('successful authentication', () => {
    it('attaches req.user with correct fields for valid token', async () => {
      const app = buildApp();
      const token = createToken({
        sub: 'user-abc-123',
        email: 'clerk@parliament.gov.gh',
        name: 'Parliament Clerk',
        'cognito:groups': ['editor'],
      });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 200);
      assert.equal(res.body.user.userId, 'user-abc-123');
      assert.equal(res.body.user.email, 'clerk@parliament.gov.gh');
      assert.equal(res.body.user.name, 'Parliament Clerk');
      assert.equal(res.body.user.role, 'Editor');
      assert.ok(Array.isArray(res.body.user.permissions));
      assert.ok(res.body.user.permissions.includes('edit_record'));
    });

    it('resolves Admin role for admin group', async () => {
      const app = buildApp();
      const token = createToken({ 'cognito:groups': ['admin'] });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 200);
      assert.equal(res.body.user.role, 'Admin');
      assert.ok(res.body.user.permissions.includes('manage_users'));
      assert.ok(res.body.user.permissions.includes('system_config'));
    });

    it('resolves highest precedence role for multiple groups', async () => {
      const app = buildApp();
      const token = createToken({ 'cognito:groups': ['viewer', 'supervisor', 'editor'] });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 200);
      assert.equal(res.body.user.role, 'Supervisor');
    });

    it('defaults to Viewer role when no groups claim', async () => {
      const app = buildApp();
      const token = createToken({ 'cognito:groups': [] });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 200);
      assert.equal(res.body.user.role, 'Viewer');
      assert.deepEqual(res.body.user.permissions, ROLE_PERMISSIONS['Viewer']);
    });

    it('defaults to Viewer when cognito:groups is missing from claims', async () => {
      const app = buildApp();
      // Create token without cognito:groups
      const payload = {
        sub: 'user-no-groups',
        email: 'nogroups@test.com',
        name: 'No Groups',
        iss: ISSUER,
        aud: TEST_CONFIG.appClientId,
        iat: Math.floor(Date.now() / 1000),
        exp: Math.floor(Date.now() / 1000) + 3600,
      };
      const token = jwt.sign(payload, privateKey, {
        algorithm: 'RS256',
        header: { kid: 'test-kid-001', alg: 'RS256' },
      });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 200);
      assert.equal(res.body.user.role, 'Viewer');
    });

    it('uses cognito:username as name fallback when name claim missing', async () => {
      const app = buildApp();
      const payload = {
        sub: 'user-fallback',
        email: 'fallback@test.com',
        'cognito:username': 'fallback_user',
        'cognito:groups': ['viewer'],
        iss: ISSUER,
        aud: TEST_CONFIG.appClientId,
        iat: Math.floor(Date.now() / 1000),
        exp: Math.floor(Date.now() / 1000) + 3600,
      };
      const token = jwt.sign(payload, privateKey, {
        algorithm: 'RS256',
        header: { kid: 'test-kid-001', alg: 'RS256' },
      });

      const res = await request(app)
        .get('/protected')
        .set('Authorization', `Bearer ${token}`);

      assert.equal(res.status, 200);
      assert.equal(res.body.user.name, 'fallback_user');
    });
  });
});

// ==========================================================================
// HS256 local token rejection tests (Requirement 17.10)
// ==========================================================================

describe('cognitoAuth middleware — HS256 local token rejection', () => {
  it('returns 401 for HS256-signed tokens (locally-signed JWT in Cognito mode)', async () => {
    const app = buildApp();
    // Create an HS256 token like the local auth system would issue
    const localSecret = 'a'.repeat(64); // simulated SESSION_SECRET
    const localToken = jwt.sign(
      {
        sub: 'user-local-123',
        email: 'admin@parliament.gov.gh',
        name: 'Admin User',
        role: 'Admin',
        iss: 'parliament-gateway',
        aud: 'hansard-spa',
        jti: 'local-jti-abc123',
      },
      localSecret,
      { algorithm: 'HS256', header: { alg: 'HS256', kid: 'local-kid' } }
    );

    const res = await request(app)
      .get('/protected')
      .set('Authorization', `Bearer ${localToken}`);

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'INVALID_TOKEN');
  });

  it('returns 401 for HS256 token without kid header', async () => {
    const app = buildApp();
    const localSecret = 'b'.repeat(64);
    const localToken = jwt.sign(
      {
        sub: 'user-local-456',
        email: 'editor@parliament.gov.gh',
        name: 'Editor User',
        role: 'Editor',
        iss: 'parliament-gateway',
        aud: 'hansard-spa',
        jti: 'local-jti-def456',
      },
      localSecret,
      { algorithm: 'HS256' }
    );

    const res = await request(app)
      .get('/protected')
      .set('Authorization', `Bearer ${localToken}`);

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'INVALID_TOKEN');
  });

  it('returns 401 for HS256 token even with valid Cognito-like claims', async () => {
    const app = buildApp();
    // Attempt to craft an HS256 token with Cognito-like issuer/audience
    const localSecret = 'c'.repeat(64);
    const localToken = jwt.sign(
      {
        sub: 'user-spoof',
        email: 'admin@parliament.gov.gh',
        name: 'Spoofed Admin',
        'cognito:groups': ['admin'],
        iss: ISSUER,
        aud: TEST_CONFIG.appClientId,
      },
      localSecret,
      { algorithm: 'HS256', header: { alg: 'HS256', kid: 'test-kid-001' } }
    );

    const res = await request(app)
      .get('/protected')
      .set('Authorization', `Bearer ${localToken}`);

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'INVALID_TOKEN');
  });

  it('returns 401 for alg:none token (algorithm downgrade attack)', async () => {
    const app = buildApp();
    // Create an unsigned token (alg: none)
    const header = Buffer.from(JSON.stringify({ alg: 'none', kid: 'test-kid-001' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify({
      sub: 'user-none',
      email: 'hacker@evil.com',
      name: 'None Alg',
      'cognito:groups': ['admin'],
      iss: ISSUER,
      aud: TEST_CONFIG.appClientId,
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600,
    })).toString('base64url');
    const unsignedToken = `${header}.${payload}.`;

    const res = await request(app)
      .get('/protected')
      .set('Authorization', `Bearer ${unsignedToken}`);

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'INVALID_TOKEN');
  });

  it('returns 401 for HS384 token', async () => {
    const app = buildApp();
    const localSecret = 'd'.repeat(64);
    const localToken = jwt.sign(
      {
        sub: 'user-hs384',
        email: 'test@parliament.gov.gh',
        name: 'HS384 User',
        role: 'Admin',
        iss: ISSUER,
        aud: TEST_CONFIG.appClientId,
      },
      localSecret,
      { algorithm: 'HS384', header: { alg: 'HS384', kid: 'test-kid-001' } }
    );

    const res = await request(app)
      .get('/protected')
      .set('Authorization', `Bearer ${localToken}`);

    assert.equal(res.status, 401);
    assert.equal(res.body.error.code, 'INVALID_TOKEN');
  });
});

// ==========================================================================
// GROUP_ROLE_MAP structure tests
// ==========================================================================

describe('GROUP_ROLE_MAP structure', () => {
  it('has admin as highest precedence (index 0)', () => {
    assert.equal(GROUP_ROLE_MAP[0].group, 'admin');
    assert.equal(GROUP_ROLE_MAP[0].role, 'Admin');
  });

  it('has viewer as lowest precedence (last index)', () => {
    const last = GROUP_ROLE_MAP[GROUP_ROLE_MAP.length - 1];
    assert.equal(last.group, 'viewer');
    assert.equal(last.role, 'Viewer');
  });

  it('contains exactly 5 roles', () => {
    assert.equal(GROUP_ROLE_MAP.length, 5);
  });
});
