/**
 * Authentication Routes — Local Password Auth (Equalized Bcrypt)
 *
 * Provides POST /api/auth/login with equalized credential verification:
 * - Always executes exactly one bcrypt.compare (real hash or dummy hash)
 * - All credential failures return identical 401 + Credential_Failure_Body
 * - Timing is equalized regardless of account existence
 *
 * Processing order: loginValidator → rateLimiter → requestDeadline → handler
 *
 * This is the "legacy" local auth mode — for development and demo environments only.
 *
 * @module routes/auth
 */

'use strict';

const express = require('express');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');

const loginValidator = require('../middleware/login-validator');
const { createRateLimiter, recordFailure, clearOnSuccess, getProgressiveDelay } = require('../middleware/rate-limiter');
const requestDeadline = require('../middleware/request-deadline');

// ─── Constants ───────────────────────────────────────────────────────────────

/**
 * Role → Permissions mapping (server-authoritative).
 * Client-side permissions are convenience only; the gateway enforces these.
 */
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

/**
 * Roles that are allowed to authenticate via local login.
 */
const ALLOWLISTED_ROLES = new Set(['Admin', 'Chief Editor', 'Supervisor', 'Editor', 'Viewer']);

/**
 * The identical response body returned for ALL credential failure causes.
 * Byte-for-byte equivalent across every failure path.
 */
const CREDENTIAL_FAILURE_BODY = Object.freeze({
  error: {
    type: 'AuthenticationError',
    code: 'INVALID_CREDENTIALS',
    message: 'Invalid email or password',
  },
});

/**
 * Minimum and maximum approved bcrypt cost factors.
 */
const MIN_BCRYPT_COST = 12;
const MAX_BCRYPT_COST = 14;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Validates that a stored hash is a usable bcrypt hash with an approved cost.
 *
 * A hash is "valid" if:
 * - It starts with $2b$ or $2a$
 * - Its cost factor is between 12 and 14 inclusive
 *
 * @param {string|null|undefined} hash - The stored password_hash value
 * @returns {boolean} true if the hash is valid and usable
 */
function isValidBcryptHash(hash) {
  if (!hash || typeof hash !== 'string') return false;

  // Must start with $2b$ or $2a$
  if (!hash.startsWith('$2b$') && !hash.startsWith('$2a$')) return false;

  // Extract cost factor (characters 4-5, before the next $)
  const costStr = hash.substring(4, 6);
  const cost = parseInt(costStr, 10);

  if (isNaN(cost)) return false;
  if (cost < MIN_BCRYPT_COST || cost > MAX_BCRYPT_COST) return false;

  return true;
}

/**
 * Sleep for the specified number of milliseconds.
 * Used for progressive delay on credential failures.
 *
 * @param {number} ms - Milliseconds to sleep
 * @returns {Promise<void>}
 */
function sleep(ms) {
  if (ms <= 0) return Promise.resolve();
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Factory ─────────────────────────────────────────────────────────────────

/**
 * Creates the auth router with equalized bcrypt credential verification.
 *
 * @param {import('pg').Pool} db - PostgreSQL pool
 * @param {Object} options - Configuration (from server.js startup validation)
 * @param {string} options.sessionSecret - Validated JWT signing secret
 * @param {number} options.jwtLifetime - JWT lifetime in seconds (1–3600, default 900)
 * @param {number} options.bcryptCost - Approved bcrypt cost (12–14)
 * @param {string} options.dummyHash - Dummy bcrypt hash at configured cost
 * @returns {import('express').Router}
 */
function authRoutes(db, options = {}) {
  const router = express.Router();

  const { sessionSecret, jwtLifetime = 900, bcryptCost, dummyHash } = options;

  // Fail closed: if critical config is missing, refuse to mount
  if (!sessionSecret || !dummyHash) {
    throw new Error('[auth] Cannot create auth router without sessionSecret and dummyHash');
  }

  // Create the rate limiter middleware instance
  const rateLimiter = createRateLimiter();

  /**
   * POST /api/auth/login
   *
   * Processing order: loginValidator → rateLimiter → requestDeadline(5000) → handler
   *
   * The handler performs equalized bcrypt comparison and returns identical
   * responses for all credential failure causes.
   */
  router.post(
    '/api/auth/login',
    loginValidator,
    rateLimiter,
    async (req, res) => {
      try {
        const normalizedEmail = req.normalizedEmail;
        const password = req.submittedPassword;

        // Check if deadline already exceeded before DB work
        if (req.deadlineSignal && req.deadlineSignal.aborted) {
          return; // requestDeadline middleware already sent 504
        }

        // ── Database lookup: parameterized query with normalized email ──
        const result = await db.query(
          'SELECT id, email, name, role, status, password_hash FROM users WHERE email = $1 LIMIT 1',
          [normalizedEmail]
        );

        // Check deadline after DB query
        if (req.deadlineSignal && req.deadlineSignal.aborted) {
          return;
        }

        const user = result.rows[0];

        // ── Determine which hash to compare against ──
        // If user found AND has a valid bcrypt hash → use real hash
        // Otherwise → use dummy hash (equalized timing)
        const hasValidHash = user ? isValidBcryptHash(user.password_hash) : false;
        const hashToCompare = hasValidHash ? user.password_hash : dummyHash;

        // ── Execute exactly one bcrypt comparison ──
        const passwordMatches = await bcrypt.compare(password, hashToCompare);

        // Check deadline after bcrypt
        if (req.deadlineSignal && req.deadlineSignal.aborted) {
          return;
        }

        // ── Evaluate all credential failure causes ──
        // Success requires ALL of:
        // 1. User exists
        // 2. Hash was valid (not dummy)
        // 3. Password matches
        // 4. Account status is 'Active'
        // 5. Role is in allowlist
        const isSuccess =
          user &&
          hasValidHash &&
          passwordMatches &&
          user.status === 'Active' &&
          ALLOWLISTED_ROLES.has(user.role);

        if (!isSuccess) {
          // ── Failure path ──
          // Record failure for progressive delay and lockout
          recordFailure(normalizedEmail);

          // Apply progressive delay before responding
          const delay = getProgressiveDelay(normalizedEmail);
          if (delay > 0) {
            await sleep(delay);
          }

          // Check deadline after delay
          if (req.deadlineSignal && req.deadlineSignal.aborted) {
            return;
          }

          // Return identical 401 for ALL failure causes
          // No JWT issued, no last_active updated
          return res.status(401).json(CREDENTIAL_FAILURE_BODY);
        }

        // ── Success path ──
        // Clear rate limiter state for this email
        clearOnSuccess(normalizedEmail);

        // Check deadline before issuing token
        if (req.deadlineSignal && req.deadlineSignal.aborted) {
          return;
        }

        // Sign JWT with required claims
        const now = Math.floor(Date.now() / 1000);
        const jti = crypto.randomUUID().replace(/-/g, '');

        const token = jwt.sign(
          {
            sub: String(user.id),
            email: user.email,
            name: user.name,
            role: user.role,
            iss: 'parliament-gateway',
            aud: 'hansard-spa',
            iat: now,
            exp: now + jwtLifetime,
            jti,
          },
          sessionSecret,
          { algorithm: 'HS256' }
        );

        // Update last_active (non-blocking — don't let it delay the response)
        db.query('UPDATE users SET last_active = now() WHERE id = $1', [user.id])
          .catch(() => { /* non-critical, swallow */ });

        // Resolve permissions for the user's role
        const permissions = ROLE_PERMISSIONS[user.role] || [];

        // Set security response headers
        res.set('Cache-Control', 'no-store');
        res.set('Pragma', 'no-cache');

        // Final deadline check before sending response
        if (req.deadlineSignal && req.deadlineSignal.aborted) {
          return;
        }

        return res.json({
          token,
          user: {
            id: String(user.id),
            email: user.email,
            name: user.name,
            role: user.role,
            permissions,
          },
        });
      } catch (err) {
        // Unexpected error — fail closed (no token, no credential details)
        if (!res.headersSent) {
          return res.status(500).json({
            error: {
              type: 'ServerError',
              code: 'INTERNAL_ERROR',
              message: 'An internal error occurred',
            },
          });
        }
      }
    }
  );

  return router;
}

// Export ROLE_PERMISSIONS for use by requireSession/requirePermission
authRoutes.ROLE_PERMISSIONS = ROLE_PERMISSIONS;
authRoutes.ALLOWLISTED_ROLES = ALLOWLISTED_ROLES;
authRoutes.isValidBcryptHash = isValidBcryptHash;

module.exports = authRoutes;
