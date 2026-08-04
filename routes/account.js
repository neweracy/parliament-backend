/**
 * Account Routes — Self-Service Profile, Password, and Preferences
 *
 * Every endpoint here acts on the *caller's own* users row, resolved from the
 * verified token, so none of them take a user id from the request. That is why
 * they need authentication but no `requirePermission` check: there is no
 * privilege to escalate when the subject is always `req.user.userId`.
 *
 * The split from routes/users.js is deliberate. That router is administrative
 * (`manage_users`) and can act on anyone; this one can only ever act on you.
 *
 * Mounts at /api/account
 *
 * @module routes/account
 */

'use strict';

const express = require('express');
const bcrypt = require('bcrypt');

const { logAuditEvent } = require('../lib/audit-logger');

// ─── Constants ───────────────────────────────────────────────────────────────

/**
 * Fields a user may change about themselves.
 *
 * `role`, `status`, `department`, and `email` are deliberately absent. Role and
 * status are the RBAC boundary and belong to `manage_users`; department is
 * assigned by an administrator; email is the login identifier and the Cognito
 * username, so changing it here would desynchronise the identity provider.
 */
const SELF_EDITABLE_FIELDS = Object.freeze(['name', 'phone']);

/** Fields that, if present in a PATCH body, indicate an escalation attempt. */
const ADMIN_ONLY_FIELDS = Object.freeze([
  'role',
  'status',
  'department',
  'email',
  'id',
  'permissions',
  'password_hash',
  'passwordHash',
]);

/** Display name bounds. Matches the users.name column being free text. */
const NAME_MIN_LENGTH = 2;
const NAME_MAX_LENGTH = 120;

/** Phone is stored as free text so international formats survive round-trip. */
const PHONE_MAX_LENGTH = 32;
const PHONE_PATTERN = /^[+]?[0-9 ()\-.]{6,32}$/;

/**
 * Password policy for self-service changes.
 *
 * 12 characters is the floor rather than a character-class rule because length
 * dominates for offline-cracking resistance, and class rules push users toward
 * predictable substitutions.
 */
const PASSWORD_MIN_LENGTH = 12;
const PASSWORD_MAX_LENGTH = 128;

/**
 * Preference keys the server will persist, with their expected types.
 *
 * An allowlist rather than a passthrough: `preferences` is jsonb, so accepting
 * arbitrary keys would let any authenticated user write unbounded data into
 * their own row.
 */
const PREFERENCE_SCHEMA = Object.freeze({
  compactView: 'boolean',
  showTimestamps: 'boolean',
  darkAudioPlayer: 'boolean',
  autoSave: 'boolean',
  spellCheck: 'boolean',
  showWordCount: 'boolean',
  editorFontSize: 'number',
  emailNotifications: 'boolean',
  browserNotifications: 'boolean',
  soundAlerts: 'boolean',
  dateFormat: 'string',
  timeFormat: 'string',
});

/** Defaults returned for any key the user has never set. */
const DEFAULT_PREFERENCES = Object.freeze({
  compactView: false,
  showTimestamps: true,
  darkAudioPlayer: true,
  autoSave: true,
  spellCheck: true,
  showWordCount: false,
  editorFontSize: 14,
  emailNotifications: true,
  browserNotifications: false,
  soundAlerts: false,
  dateFormat: 'DD/MM/YYYY',
  timeFormat: '12h',
});

/** Closed sets for the two enumerated string preferences. */
const ALLOWED_DATE_FORMATS = new Set(['DD/MM/YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD']);
const ALLOWED_TIME_FORMATS = new Set(['12h', '24h']);
const ALLOWED_FONT_SIZES = new Set([14, 15, 16, 18]);

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Builds a 422 validation error body.
 *
 * @param {string} message - Human-readable, safe to surface in the UI
 * @returns {object}
 */
function validationError(message) {
  return {
    error: {
      type: 'ValidationError',
      code: 'VALIDATION_ERROR',
      message,
    },
  };
}

/**
 * Shapes a users row into the API's camelCase account representation.
 *
 * `password_hash` is never included — the column is selected only where a
 * comparison needs it.
 *
 * @param {object} row - A users table row
 * @param {string[]} permissions - Server-derived permissions for the role
 * @returns {object}
 */
function toAccountProfile(row, permissions) {
  return {
    id: row.id,
    email: row.email,
    name: row.name,
    role: row.role,
    status: row.status,
    department: row.department ?? null,
    phone: row.phone ?? null,
    lastActive: row.last_active,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    permissions,
  };
}

/**
 * Merges stored preferences over the defaults, dropping unknown keys.
 *
 * Reading through the allowlist means a key removed from PREFERENCE_SCHEMA
 * stops being served without needing a data migration.
 *
 * @param {object|null} stored - Raw jsonb value from the row
 * @returns {object} A complete preferences object
 */
function mergePreferences(stored) {
  const merged = { ...DEFAULT_PREFERENCES };
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) {
    return merged;
  }
  for (const key of Object.keys(PREFERENCE_SCHEMA)) {
    if (Object.prototype.hasOwnProperty.call(stored, key)) {
      merged[key] = stored[key];
    }
  }
  return merged;
}

/**
 * Validates an incoming preferences patch against the allowlist.
 *
 * @param {unknown} body - Request body
 * @returns {{ ok: true, value: object } | { ok: false, message: string }}
 */
function validatePreferences(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, message: 'Request body must be a preferences object' };
  }

  const unknownKeys = Object.keys(body).filter(
    (key) => !Object.prototype.hasOwnProperty.call(PREFERENCE_SCHEMA, key)
  );
  if (unknownKeys.length > 0) {
    return {
      ok: false,
      message: `Unknown preference key(s): ${unknownKeys.join(', ')}`,
    };
  }

  const value = {};
  for (const [key, expectedType] of Object.entries(PREFERENCE_SCHEMA)) {
    if (!Object.prototype.hasOwnProperty.call(body, key)) continue;

    const raw = body[key];
    if (typeof raw !== expectedType) {
      return {
        ok: false,
        message: `Preference '${key}' must be a ${expectedType}`,
      };
    }

    if (key === 'editorFontSize' && !ALLOWED_FONT_SIZES.has(raw)) {
      return {
        ok: false,
        message: `Preference 'editorFontSize' must be one of: ${[...ALLOWED_FONT_SIZES].join(', ')}`,
      };
    }
    if (key === 'dateFormat' && !ALLOWED_DATE_FORMATS.has(raw)) {
      return {
        ok: false,
        message: `Preference 'dateFormat' must be one of: ${[...ALLOWED_DATE_FORMATS].join(', ')}`,
      };
    }
    if (key === 'timeFormat' && !ALLOWED_TIME_FORMATS.has(raw)) {
      return {
        ok: false,
        message: `Preference 'timeFormat' must be one of: ${[...ALLOWED_TIME_FORMATS].join(', ')}`,
      };
    }

    value[key] = raw;
  }

  return { ok: true, value };
}

/**
 * Validates that a stored hash is a usable bcrypt hash at an approved cost.
 *
 * Mirrors routes/auth.js. Re-declared rather than imported because that module
 * is only mounted in legacy auth mode, and this router mounts in both.
 *
 * @param {string|null|undefined} hash
 * @returns {boolean}
 */
function isValidBcryptHash(hash) {
  if (!hash || typeof hash !== 'string') return false;
  if (!hash.startsWith('$2b$') && !hash.startsWith('$2a$')) return false;
  const cost = parseInt(hash.substring(4, 6), 10);
  return Number.isInteger(cost) && cost >= 12 && cost <= 14;
}

// ─── Factory ─────────────────────────────────────────────────────────────────

/**
 * Creates the account router.
 *
 * @param {Function} authMiddleware - JWT/Cognito auth middleware
 * @param {import('pg').Pool} db - PostgreSQL pool
 * @param {object} [options]
 * @param {number} [options.bcryptCost=12] - Cost factor for re-hashing on change
 * @param {string} [options.authMode='legacy'] - 'legacy' or 'cognito'
 * @returns {import('express').Router}
 */
module.exports = function accountRoutes(authMiddleware, db, options = {}) {
  const router = express.Router();
  const bcryptCost = options.bcryptCost ?? 12;
  const authMode = options.authMode ?? 'legacy';

  /**
   * Loads the caller's row. Returns null when the token identifies a user that
   * no longer exists locally, which is possible after a Cognito-side delete.
   *
   * @param {object} user - req.user
   * @returns {Promise<object|null>}
   */
  async function loadOwnRow(user) {
    const result = await db.query(
      `SELECT id, email, name, role, status, department, phone, preferences,
              last_active, created_at, updated_at
       FROM users
       WHERE id = $1
       LIMIT 1`,
      [user.userId]
    );
    if (result.rows.length > 0) return result.rows[0];

    // Cognito's `sub` and the local id can diverge for users seeded before the
    // pool existed; email is the stable join key in that case.
    if (!user.email) return null;
    const byEmail = await db.query(
      `SELECT id, email, name, role, status, department, phone, preferences,
              last_active, created_at, updated_at
       FROM users
       WHERE email = $1
       LIMIT 1`,
      [String(user.email).toLowerCase()]
    );
    return byEmail.rows[0] ?? null;
  }

  /** 404 body for a token whose user row is gone. */
  function accountNotFound(res) {
    return res.status(404).json({
      error: {
        type: 'NotFoundError',
        code: 'ACCOUNT_NOT_FOUND',
        message: 'Your account could not be found',
      },
    });
  }

  // ─── GET /api/account ──────────────────────────────────────────────────────

  /**
   * Returns the caller's own profile plus their server-derived permissions.
   *
   * The permissions come from `req.user`, which both auth middlewares populate
   * from the server-owned map — never from the token's claims — so the UI can
   * trust this list to match what requirePermission will enforce.
   */
  router.get('/api/account', authMiddleware, async (req, res) => {
    try {
      const row = await loadOwnRow(req.user);
      if (!row) return accountNotFound(res);

      res.set('Cache-Control', 'no-store');
      return res.json(toAccountProfile(row, req.user.permissions ?? []));
    } catch (err) {
      console.error('GET /api/account error:', err);
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'Failed to load your account',
        },
      });
    }
  });

  // ─── PATCH /api/account ────────────────────────────────────────────────────

  /**
   * Updates the caller's own name and/or phone.
   *
   * Any attempt to include an administrative field is rejected with 403 rather
   * than silently ignored, so a caller never believes a role change succeeded.
   */
  router.patch('/api/account', authMiddleware, express.json(), async (req, res) => {
    try {
      const body = req.body;
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        return res.status(422).json(validationError('Request body must be an object'));
      }

      const attempted = ADMIN_ONLY_FIELDS.filter((field) =>
        Object.prototype.hasOwnProperty.call(body, field)
      );
      if (attempted.length > 0) {
        return res.status(403).json({
          error: {
            type: 'AuthorizationError',
            code: 'FIELD_NOT_SELF_EDITABLE',
            message:
              `The following field(s) cannot be changed from your own account: ${attempted.join(', ')}. ` +
              'Ask an administrator.',
          },
        });
      }

      const unknown = Object.keys(body).filter((key) => !SELF_EDITABLE_FIELDS.includes(key));
      if (unknown.length > 0) {
        return res.status(422).json(
          validationError(`Unsupported field(s): ${unknown.join(', ')}`)
        );
      }

      // ── Validate name ──
      let nextName;
      if (Object.prototype.hasOwnProperty.call(body, 'name')) {
        if (typeof body.name !== 'string') {
          return res.status(422).json(validationError('name must be a string'));
        }
        nextName = body.name.trim().replace(/\s+/g, ' ');
        if (nextName.length < NAME_MIN_LENGTH || nextName.length > NAME_MAX_LENGTH) {
          return res.status(422).json(
            validationError(`name must be between ${NAME_MIN_LENGTH} and ${NAME_MAX_LENGTH} characters`)
          );
        }
      }

      // ── Validate phone. An empty string clears it. ──
      let nextPhone;
      let phoneProvided = false;
      if (Object.prototype.hasOwnProperty.call(body, 'phone')) {
        phoneProvided = true;
        if (body.phone === null || body.phone === '') {
          nextPhone = null;
        } else if (typeof body.phone !== 'string') {
          return res.status(422).json(validationError('phone must be a string or null'));
        } else {
          nextPhone = body.phone.trim();
          if (nextPhone.length > PHONE_MAX_LENGTH || !PHONE_PATTERN.test(nextPhone)) {
            return res.status(422).json(
              validationError('phone must be 6–32 characters using digits, spaces, and + ( ) - .')
            );
          }
        }
      }

      if (nextName === undefined && !phoneProvided) {
        return res.status(422).json(
          validationError(`Provide at least one of: ${SELF_EDITABLE_FIELDS.join(', ')}`)
        );
      }

      const row = await loadOwnRow(req.user);
      if (!row) return accountNotFound(res);

      const updated = await db.query(
        `UPDATE users
         SET name = COALESCE($1, name),
             phone = CASE WHEN $2::boolean THEN $3 ELSE phone END,
             updated_at = now()
         WHERE id = $4
         RETURNING id, email, name, role, status, department, phone,
                   last_active, created_at, updated_at`,
        [nextName ?? null, phoneProvided, nextPhone ?? null, row.id]
      );

      return res.json(toAccountProfile(updated.rows[0], req.user.permissions ?? []));
    } catch (err) {
      console.error('PATCH /api/account error:', err);
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'Failed to update your account',
        },
      });
    }
  });

  // ─── POST /api/account/password ────────────────────────────────────────────

  /**
   * Changes the caller's own password.
   *
   * Requires the current password, so a stolen-but-unexpired token alone cannot
   * lock the real owner out. Only meaningful in legacy auth mode; under Cognito
   * the identity provider owns credentials and this returns 501 rather than
   * writing a hash the login path would never read.
   */
  router.post('/api/account/password', authMiddleware, express.json(), async (req, res) => {
    const auditBase = {
      requestId: req.headers['x-request-id'],
      userId: req.user?.userId,
      ip: req.ip,
      userAgent: req.headers['user-agent'],
    };

    try {
      if (authMode !== 'legacy') {
        return res.status(501).json({
          error: {
            type: 'NotImplementedError',
            code: 'PASSWORD_MANAGED_EXTERNALLY',
            message:
              'Passwords are managed by the identity provider. Use the hosted sign-in page to change yours.',
          },
        });
      }

      const { currentPassword, newPassword } = req.body ?? {};

      if (typeof currentPassword !== 'string' || typeof newPassword !== 'string') {
        logAuditEvent({ ...auditBase, event: 'password_change_failure', reason: 'validation' });
        return res.status(422).json(
          validationError('currentPassword and newPassword are required')
        );
      }
      if (newPassword.length < PASSWORD_MIN_LENGTH || newPassword.length > PASSWORD_MAX_LENGTH) {
        logAuditEvent({ ...auditBase, event: 'password_change_failure', reason: 'validation' });
        return res.status(422).json(
          validationError(
            `newPassword must be between ${PASSWORD_MIN_LENGTH} and ${PASSWORD_MAX_LENGTH} characters`
          )
        );
      }
      if (newPassword === currentPassword) {
        logAuditEvent({ ...auditBase, event: 'password_change_failure', reason: 'validation' });
        return res.status(422).json(
          validationError('newPassword must differ from your current password')
        );
      }

      const result = await db.query(
        'SELECT id, password_hash FROM users WHERE id = $1 LIMIT 1',
        [req.user.userId]
      );
      const row = result.rows[0];

      // Compare against the real hash when there is one, otherwise a throwaway
      // hash, so the response time does not reveal whether the account has a
      // usable credential.
      const hasValidHash = row ? isValidBcryptHash(row.password_hash) : false;
      const hashToCompare = hasValidHash
        ? row.password_hash
        : await bcrypt.hash('invalid-placeholder', bcryptCost);

      const matches = await bcrypt.compare(currentPassword, hashToCompare);

      if (!row || !hasValidHash || !matches) {
        logAuditEvent({
          ...auditBase,
          event: 'password_change_failure',
          reason: hasValidHash ? 'invalid_credentials' : 'missing_hash',
        });
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'INVALID_CREDENTIALS',
            message: 'Your current password is incorrect',
          },
        });
      }

      const newHash = await bcrypt.hash(newPassword, bcryptCost);
      await db.query(
        'UPDATE users SET password_hash = $1, updated_at = now() WHERE id = $2',
        [newHash, row.id]
      );

      logAuditEvent({ ...auditBase, event: 'password_changed', reason: 'success' });

      res.set('Cache-Control', 'no-store');
      return res.json({
        changed: true,
        message: 'Password updated. Existing sessions remain valid until they expire.',
      });
    } catch (err) {
      console.error('POST /api/account/password error:', err);
      logAuditEvent({ ...auditBase, event: 'password_change_failure', reason: 'dependency_error' });
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'Failed to change your password',
        },
      });
    }
  });

  // ─── GET /api/account/preferences ──────────────────────────────────────────

  /** Returns the caller's preferences, with defaults filled in. */
  router.get('/api/account/preferences', authMiddleware, async (req, res) => {
    try {
      const row = await loadOwnRow(req.user);
      if (!row) return accountNotFound(res);
      return res.json(mergePreferences(row.preferences));
    } catch (err) {
      console.error('GET /api/account/preferences error:', err);
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'Failed to load your preferences',
        },
      });
    }
  });

  // ─── PUT /api/account/preferences ──────────────────────────────────────────

  /**
   * Replaces the caller's preferences.
   *
   * Stores only allowlisted keys and always responds with the merged result, so
   * the client never has to guess which defaults applied.
   */
  router.put('/api/account/preferences', authMiddleware, express.json(), async (req, res) => {
    try {
      const validated = validatePreferences(req.body);
      if (!validated.ok) {
        return res.status(422).json(validationError(validated.message));
      }

      const row = await loadOwnRow(req.user);
      if (!row) return accountNotFound(res);

      const updated = await db.query(
        `UPDATE users
         SET preferences = $1::jsonb, updated_at = now()
         WHERE id = $2
         RETURNING preferences`,
        [JSON.stringify(validated.value), row.id]
      );

      return res.json(mergePreferences(updated.rows[0].preferences));
    } catch (err) {
      console.error('PUT /api/account/preferences error:', err);
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'Failed to save your preferences',
        },
      });
    }
  });

  return router;
};

// Exported for testing
module.exports.SELF_EDITABLE_FIELDS = SELF_EDITABLE_FIELDS;
module.exports.ADMIN_ONLY_FIELDS = ADMIN_ONLY_FIELDS;
module.exports.DEFAULT_PREFERENCES = DEFAULT_PREFERENCES;
module.exports.PREFERENCE_SCHEMA = PREFERENCE_SCHEMA;
module.exports.mergePreferences = mergePreferences;
module.exports.validatePreferences = validatePreferences;
module.exports.isValidBcryptHash = isValidBcryptHash;
module.exports.PASSWORD_MIN_LENGTH = PASSWORD_MIN_LENGTH;
