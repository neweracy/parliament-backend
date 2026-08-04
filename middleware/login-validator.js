'use strict';

/**
 * Login Request Validator Middleware
 *
 * Validates login requests before expensive work (database lookup, bcrypt).
 * Enforces content type, body size limit, JSON parsing, schema validation,
 * and credential bounds in strict order.
 *
 * Processing order:
 * 1. Content-Type: application/json (415)
 * 2. Body size ≤ 2048 UTF-8 bytes (413)
 * 3. Valid JSON (400)
 * 4. Schema: plain object, only email + password fields (422)
 * 5. Email: present, string, non-empty after trim, ≤254 chars (422)
 * 6. Password: present, string, non-empty, ≤72 UTF-8 bytes (422)
 * 7. Normalize email and pass through
 *
 * @module middleware/login-validator
 */

/**
 * Maximum allowed request body size in bytes (UTF-8 encoded).
 * Exactly 2048 bytes is accepted; anything above is rejected.
 */
const MAX_BODY_BYTES = 2048;

/**
 * Maximum email length in characters after trimming.
 */
const MAX_EMAIL_CHARS = 254;

/**
 * Maximum password length in UTF-8 bytes.
 * Matches bcrypt's effective input limit to avoid truncation ambiguity.
 */
const MAX_PASSWORD_BYTES = 72;

/**
 * Only these top-level fields are permitted in the login request body.
 */
const ALLOWED_FIELDS = new Set(['email', 'password']);

/**
 * Creates a validation error response object.
 *
 * @param {string} code - Error code (INVALID_REQUEST or MISSING_FIELDS)
 * @param {string} message - Human-readable error message
 * @returns {{ error: { type: string, code: string, message: string } }}
 */
function validationError(code, message) {
  return {
    error: {
      type: 'ValidationError',
      code,
      message,
    },
  };
}

/**
 * Checks if the Content-Type header starts with 'application/json'.
 *
 * @param {string|undefined} contentType - The Content-Type header value
 * @returns {boolean}
 */
function isJsonContentType(contentType) {
  if (!contentType) return false;
  // Match 'application/json' with optional charset/params
  const normalized = contentType.toLowerCase().trim();
  return normalized === 'application/json' || normalized.startsWith('application/json;');
}

/**
 * Checks if a value is a plain object (not array, null, or other type).
 *
 * @param {*} value - The value to check
 * @returns {boolean}
 */
function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Checks if any field value contains nested objects or arrays.
 *
 * @param {Object} obj - The parsed JSON object
 * @returns {boolean} - true if nested values exist
 */
function hasNestedValues(obj) {
  for (const key of Object.keys(obj)) {
    const val = obj[key];
    if (val !== null && typeof val === 'object') {
      return true;
    }
  }
  return false;
}

/**
 * Login request validator middleware.
 *
 * Reads the raw body, enforces size and content-type constraints,
 * parses JSON, validates schema and credential bounds, then normalizes
 * email and passes control to the next middleware/handler.
 *
 * Sets on req:
 * - req.normalizedEmail: email.trim().toLowerCase()
 * - req.submittedPassword: password (unmodified)
 * - req.loginBody: the parsed { email, password } object
 *
 * @param {import('express').Request} req
 * @param {import('express').Response} res
 * @param {import('express').NextFunction} next
 */
function loginValidator(req, res, next) {
  // 1. Content-Type check — must be application/json (415 before any work)
  const contentType = req.headers['content-type'];
  if (!isJsonContentType(contentType)) {
    return res.status(415).json(
      validationError('INVALID_REQUEST', 'Content-Type must be application/json')
    );
  }

  // 2. Read raw body and enforce size limit
  const chunks = [];
  let totalBytes = 0;
  let limitExceeded = false;

  req.on('data', (chunk) => {
    if (limitExceeded) return;

    totalBytes += chunk.length;
    if (totalBytes > MAX_BODY_BYTES) {
      limitExceeded = true;
      return;
    }
    chunks.push(chunk);
  });

  req.on('end', () => {
    // 2a. Check body size limit (413 before JSON parsing)
    if (limitExceeded) {
      return res.status(413).json(
        validationError('INVALID_REQUEST', 'Request body exceeds maximum size of 2048 bytes')
      );
    }

    const rawBody = Buffer.concat(chunks);

    // Empty body → malformed JSON
    if (rawBody.length === 0) {
      return res.status(400).json(
        validationError('INVALID_REQUEST', 'Request body must be valid JSON')
      );
    }

    // 3. Parse JSON (400 for malformed)
    let parsed;
    try {
      parsed = JSON.parse(rawBody.toString('utf8'));
    } catch (_err) {
      return res.status(400).json(
        validationError('INVALID_REQUEST', 'Request body must be valid JSON')
      );
    }

    // 4. Schema validation — must be a plain object (not array, null, scalar)
    if (!isPlainObject(parsed)) {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Request body must be a JSON object')
      );
    }

    // 4a. Check for extra fields (only email and password allowed)
    const bodyKeys = Object.keys(parsed);
    for (const key of bodyKeys) {
      if (!ALLOWED_FIELDS.has(key)) {
        return res.status(422).json(
          validationError('INVALID_REQUEST', 'Request body contains unexpected fields')
        );
      }
    }

    // 4b. Check for nested values (objects or arrays as field values)
    if (hasNestedValues(parsed)) {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Request body contains unexpected fields')
      );
    }

    const { email, password } = parsed;

    // 5. Validate email: present, string, non-empty after trim, ≤254 chars
    if (email === undefined || email === null) {
      return res.status(422).json(
        validationError('MISSING_FIELDS', 'Email and password are required')
      );
    }
    if (typeof email !== 'string') {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Email must be a string')
      );
    }
    const trimmedEmail = email.trim();
    if (trimmedEmail.length === 0) {
      return res.status(422).json(
        validationError('MISSING_FIELDS', 'Email and password are required')
      );
    }
    if (trimmedEmail.length > MAX_EMAIL_CHARS) {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Email exceeds maximum length of 254 characters')
      );
    }

    // 6. Validate password: present, string, non-empty, ≤72 UTF-8 bytes
    if (password === undefined || password === null) {
      return res.status(422).json(
        validationError('MISSING_FIELDS', 'Email and password are required')
      );
    }
    if (typeof password !== 'string') {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Password must be a string')
      );
    }
    if (password.length === 0) {
      return res.status(422).json(
        validationError('MISSING_FIELDS', 'Email and password are required')
      );
    }
    const passwordByteLength = Buffer.byteLength(password, 'utf8');
    if (passwordByteLength > MAX_PASSWORD_BYTES) {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Password exceeds maximum length')
      );
    }

    // 7. Produce Normalized_Email and pass through
    req.normalizedEmail = trimmedEmail.toLowerCase();
    req.submittedPassword = password;
    req.loginBody = { email, password };

    next();
  });

  req.on('error', (_err) => {
    // Stream error → treat as bad request
    if (!res.headersSent) {
      return res.status(400).json(
        validationError('INVALID_REQUEST', 'Failed to read request body')
      );
    }
  });
}

module.exports = loginValidator;
module.exports.loginValidator = loginValidator;
module.exports.MAX_BODY_BYTES = MAX_BODY_BYTES;
module.exports.MAX_EMAIL_CHARS = MAX_EMAIL_CHARS;
module.exports.MAX_PASSWORD_BYTES = MAX_PASSWORD_BYTES;
