/**
 * Authentication Routes — Local Password Auth
 *
 * Provides a POST /api/auth/login endpoint that validates email + password
 * against bcrypt hashes stored in the users table. On success, issues a JWT
 * with user claims (sub, email, name, role) and returns user info.
 *
 * This is the "legacy" local auth mode — an alternative to Cognito for
 * development and demo environments.
 *
 * @module routes/auth
 */

'use strict';

const express = require('express');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');

// ─── Role → Permissions mapping ─────────────────────────────────────────────

const ROLE_PERMISSIONS = {
  'Admin': [
    'manage_users', 'system_config', 'create_sitting', 'assign_editor',
    'certify_record', 'manage_templates', 'export_hansard', 'view_audit_trail',
    'review_record', 'approve_certification', 'edit_record', 'upload_audio',
    'rename_speakers', 'submit_for_review', 'export_drafts', 'view_records',
    'search_hansard', 'export_published',
  ],
  'Chief Editor': [
    'create_sitting', 'assign_editor', 'certify_record', 'manage_templates',
    'export_hansard', 'view_audit_trail', 'review_record', 'approve_certification',
    'edit_record', 'upload_audio', 'rename_speakers', 'submit_for_review',
    'export_drafts', 'view_records', 'search_hansard', 'export_published',
  ],
  'Supervisor': [
    'review_record', 'approve_certification', 'edit_record', 'upload_audio',
    'rename_speakers', 'submit_for_review', 'export_drafts', 'view_records',
    'search_hansard', 'export_published',
  ],
  'Editor': [
    'edit_record', 'upload_audio', 'rename_speakers', 'submit_for_review',
    'export_drafts', 'view_records', 'search_hansard', 'export_published',
  ],
  'Viewer': [
    'view_records', 'search_hansard', 'export_published',
  ],
};

// ─── Factory ─────────────────────────────────────────────────────────────────

/**
 * Creates the auth router. No auth middleware required — login is public.
 *
 * @param {import('pg').Pool} db - PostgreSQL pool
 * @param {Object} [options] - Options
 * @param {string} [options.sessionSecret] - JWT signing secret (falls back to SESSION_SECRET env var)
 * @returns {import('express').Router}
 */
function authRoutes(db, options = {}) {
  const router = express.Router();

  // Use provided secret, env var, or generate one (should match server.js secret)
  const SESSION_SECRET = options.sessionSecret
    || process.env.SESSION_SECRET
    || crypto.randomBytes(32).toString('hex');

  // Parse JSON bodies on this router
  router.use(express.json());

  /**
   * POST /api/auth/login
   *
   * Authenticates a user with email + password. Returns a JWT and user info.
   */
  router.post('/api/auth/login', async (req, res) => {
    try {
      const { email, password } = req.body;

      if (!email || !password) {
        return res.status(400).json({
          error: {
            type: 'ValidationError',
            code: 'MISSING_FIELDS',
            message: 'Email and password are required',
          },
        });
      }

      // Look up user by email
      const result = await db.query(
        'SELECT id, email, name, role, status, password_hash FROM users WHERE email = $1',
        [email]
      );

      const user = result.rows[0];

      // User not found — return generic error to avoid leaking user existence
      if (!user) {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'INVALID_CREDENTIALS',
            message: 'Invalid email or password',
          },
        });
      }

      // No password hash set — user hasn't been configured for local auth
      if (!user.password_hash) {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'INVALID_CREDENTIALS',
            message: 'Invalid email or password',
          },
        });
      }

      // Verify password against bcrypt hash
      const valid = await bcrypt.compare(password, user.password_hash);
      if (!valid) {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'INVALID_CREDENTIALS',
            message: 'Invalid email or password',
          },
        });
      }

      // Check if user is active
      if (user.status !== 'Active') {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'INVALID_CREDENTIALS',
            message: 'Invalid email or password',
          },
        });
      }

      // Update last_active timestamp
      await db.query(
        'UPDATE users SET last_active = now() WHERE id = $1',
        [user.id]
      );

      // Build JWT with user claims
      const token = jwt.sign(
        {
          sub: user.id,
          email: user.email,
          name: user.name,
          role: user.role,
        },
        SESSION_SECRET,
        { expiresIn: '1h' }
      );

      // Resolve permissions for the user's role
      const permissions = ROLE_PERMISSIONS[user.role] || ROLE_PERMISSIONS['Viewer'];

      return res.json({
        token,
        user: {
          id: user.id,
          email: user.email,
          name: user.name,
          role: user.role,
          permissions,
        },
      });
    } catch (err) {
      console.error('[auth] Login error:', err);
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'An internal error occurred',
        },
      });
    }
  });

  return router;
}

// Export the ROLE_PERMISSIONS for use by requireSession
authRoutes.ROLE_PERMISSIONS = ROLE_PERMISSIONS;

module.exports = authRoutes;
