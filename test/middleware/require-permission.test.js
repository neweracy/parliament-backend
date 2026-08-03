'use strict';

/**
 * Unit tests for middleware/require-permission.js — RBAC enforcement.
 *
 * Tests permission evaluation for each role, 403 responses with correct
 * error format, and pass-through when permission is granted.
 *
 * Validates: Requirements 5.2, 5.3
 *
 * @module test/middleware/require-permission
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const requirePermission = require('../../middleware/require-permission');
const { ROLE_PERMISSIONS } = require('../../middleware/cognito-auth');

// --------------------------------------------------------------------------
// Helper: Build test Express app with a fake auth user attached
// --------------------------------------------------------------------------

/**
 * Creates a minimal Express app that attaches a mock user to req.user
 * (simulating cognitoAuth) and then applies requirePermission.
 *
 * @param {string} operation - Permission to require
 * @param {Object|null} user - Mock user object to attach (null = no user)
 * @returns {Object} Express app
 */
function buildApp(operation, user) {
  const app = express();

  // Simulate cognitoAuth attaching req.user
  app.use((req, _res, next) => {
    if (user !== null) {
      req.user = user;
    }
    next();
  });

  app.get('/protected', requirePermission(operation), (req, res) => {
    res.json({ success: true, user: req.user });
  });

  return app;
}

/**
 * Creates a mock user object for a given role.
 *
 * @param {string} role - Role name
 * @returns {Object} Mock user object matching cognitoAuth output
 */
function mockUser(role) {
  return {
    userId: `user-${role.toLowerCase().replace(/\s+/g, '-')}-001`,
    email: `${role.toLowerCase().replace(/\s+/g, '.')}@parliament.gov.gh`,
    name: `Test ${role}`,
    role,
    permissions: ROLE_PERMISSIONS[role] || ROLE_PERMISSIONS['Viewer'],
  };
}

// ==========================================================================
// Passes through when user has the required permission
// ==========================================================================

describe('requirePermission — access granted', () => {
  it('passes through when Admin has system_config permission', async () => {
    const app = buildApp('system_config', mockUser('Admin'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 200);
    assert.equal(res.body.success, true);
  });

  it('passes through when Editor has edit_record permission', async () => {
    const app = buildApp('edit_record', mockUser('Editor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 200);
    assert.equal(res.body.success, true);
  });

  it('passes through when Viewer has view_records permission', async () => {
    const app = buildApp('view_records', mockUser('Viewer'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 200);
    assert.equal(res.body.success, true);
  });

  it('passes through when Chief Editor has manage_users permission', async () => {
    const app = buildApp('manage_users', mockUser('Chief Editor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 200);
    assert.equal(res.body.success, true);
  });

  it('passes through when Supervisor has review_record permission', async () => {
    const app = buildApp('review_record', mockUser('Supervisor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 200);
    assert.equal(res.body.success, true);
  });
});

// ==========================================================================
// Returns 403 when user lacks the required permission
// ==========================================================================

describe('requirePermission — access denied (403)', () => {
  it('returns 403 when Viewer tries system_config', async () => {
    const app = buildApp('system_config', mockUser('Viewer'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.equal(res.body.error.type, 'AuthorizationError');
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
  });

  it('returns 403 when Editor tries manage_users', async () => {
    const app = buildApp('manage_users', mockUser('Editor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.equal(res.body.error.type, 'AuthorizationError');
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
  });

  it('returns 403 when Supervisor tries system_config', async () => {
    const app = buildApp('system_config', mockUser('Supervisor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.equal(res.body.error.type, 'AuthorizationError');
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
  });

  it('returns 403 when Editor tries certify_record', async () => {
    const app = buildApp('certify_record', mockUser('Editor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
  });

  it('returns 403 when Viewer tries edit_record', async () => {
    const app = buildApp('edit_record', mockUser('Viewer'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
  });
});

// ==========================================================================
// 403 response includes role and required permission in message
// ==========================================================================

describe('requirePermission — 403 error message format', () => {
  it('includes role name in error message', async () => {
    const app = buildApp('system_config', mockUser('Viewer'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.match(res.body.error.message, /Viewer/);
  });

  it('includes required permission in error message', async () => {
    const app = buildApp('system_config', mockUser('Editor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.match(res.body.error.message, /system_config/);
  });

  it('includes both role and permission for Chief Editor denied manage_users is N/A (they have it)', async () => {
    // Chief Editor DOES have manage_users, so test Supervisor instead
    const app = buildApp('manage_users', mockUser('Supervisor'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.match(res.body.error.message, /Supervisor/);
    assert.match(res.body.error.message, /manage_users/);
  });

  it('uses AuthorizationError type consistently', async () => {
    const app = buildApp('create_sitting', mockUser('Viewer'));
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.equal(res.body.error.type, 'AuthorizationError');
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
    assert.ok(typeof res.body.error.message === 'string');
    assert.ok(res.body.error.message.length > 0);
  });
});

// ==========================================================================
// Works with different roles/permissions combinations
// ==========================================================================

describe('requirePermission — various role/permission combos', () => {
  it('Admin can access every defined permission', async () => {
    const adminUser = mockUser('Admin');
    for (const perm of ROLE_PERMISSIONS['Admin']) {
      const app = buildApp(perm, adminUser);
      const res = await request(app).get('/protected');
      assert.equal(res.status, 200, `Admin should have permission: ${perm}`);
    }
  });

  it('Viewer is denied all admin-level permissions', async () => {
    const viewerUser = mockUser('Viewer');
    const adminOnly = ['manage_users', 'system_config', 'create_sitting', 'assign_editor'];

    for (const perm of adminOnly) {
      const app = buildApp(perm, viewerUser);
      const res = await request(app).get('/protected');
      assert.equal(res.status, 403, `Viewer should NOT have permission: ${perm}`);
    }
  });

  it('Supervisor has export_hansard but not system_config', async () => {
    const supervisorUser = mockUser('Supervisor');

    const appAllowed = buildApp('export_hansard', supervisorUser);
    const resAllowed = await request(appAllowed).get('/protected');
    assert.equal(resAllowed.status, 200);

    const appDenied = buildApp('system_config', supervisorUser);
    const resDenied = await request(appDenied).get('/protected');
    assert.equal(resDenied.status, 403);
  });

  it('returns 403 when req.user is not attached (no auth middleware ran)', async () => {
    const app = buildApp('view_records', null);
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
    assert.equal(res.body.error.code, 'INSUFFICIENT_PERMISSIONS');
  });

  it('returns 403 when req.user exists but permissions is not an array', async () => {
    const brokenUser = {
      userId: 'broken-user',
      email: 'broken@test.com',
      name: 'Broken',
      role: 'Unknown',
      permissions: undefined,
    };
    const app = buildApp('view_records', brokenUser);
    const res = await request(app).get('/protected');

    assert.equal(res.status, 403);
  });
});
