'use strict';

/**
 * Login Request Validator Middleware
 *
 * Validates login requests before expensive work (database lookup, bcrypt).
 * Enforces content type, schema validation, and credential bounds.
 *
 * NOTE: express.json() must be mounted BEFORE this middleware in the chain
 * to parse the raw body (Express 5 requires explicit body parsing).
 *
 * Processing order:
 * 1. Content-Type: application/json (415)
 * 2. Body present and valid JSON object (400/422)
 * 3. Schema: only email + password fields (422)
 * 4. Email: present, string, non-empty after trim, ≤254 chars (422)
 * 5. Password: present, string, non-empty, ≤72 UTF-8 bytes (422)
 * 6. Normalize email and pass through
 *
 * @module middleware/login-validator
 */

const MAX_BODY_BYTES = 2048;
const MAX_EMAIL_CHARS = 254;
const MAX_PASSWORD_BYTES = 72;
const ALLOWED_FIELDS = new Set(['email', 'password']);

function validationError(code, message) {
  return { error: { type: 'ValidationError', code, message } };
}

function isJsonContentType(contentType) {
  if (!contentType) return false;
  const normalized = contentType.toLowerCase().trim();
  return normalized === 'application/json' || normalized.startsWith('application/json;');
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasNestedValues(obj) {
  for (const key of Object.keys(obj)) {
    const val = obj[key];
    if (val !== null && typeof val === 'object') return true;
  }
  return false;
}

function loginValidator(req, res, next) {
  // 1. Content-Type check
  if (!isJsonContentType(req.headers['content-type'])) {
    return res.status(415).json(
      validationError('INVALID_REQUEST', 'Content-Type must be application/json')
    );
  }

  // Body is already parsed by express.json() mounted before this middleware
  const parsed = req.body;

  if (parsed === undefined) {
    return res.status(400).json(
      validationError('INVALID_REQUEST', 'Request body must be valid JSON')
    );
  }

  if (parsed === null || !isPlainObject(parsed)) {
    return res.status(422).json(
      validationError('INVALID_REQUEST', 'Request body must be a JSON object')
    );
  }

  // Only email and password allowed
  for (const key of Object.keys(parsed)) {
    if (!ALLOWED_FIELDS.has(key)) {
      return res.status(422).json(
        validationError('INVALID_REQUEST', 'Request body contains unexpected fields')
      );
    }
  }

  if (hasNestedValues(parsed)) {
    return res.status(422).json(
      validationError('INVALID_REQUEST', 'Request body contains unexpected fields')
    );
  }

  const { email, password } = parsed;

  // Validate email
  if (email === undefined || email === null) {
    return res.status(422).json(validationError('MISSING_FIELDS', 'Email and password are required'));
  }
  if (typeof email !== 'string') {
    return res.status(422).json(validationError('INVALID_REQUEST', 'Email must be a string'));
  }
  const trimmedEmail = email.trim();
  if (trimmedEmail.length === 0) {
    return res.status(422).json(validationError('MISSING_FIELDS', 'Email and password are required'));
  }
  if (trimmedEmail.length > MAX_EMAIL_CHARS) {
    return res.status(422).json(validationError('INVALID_REQUEST', 'Email exceeds maximum length of 254 characters'));
  }

  // Validate password
  if (password === undefined || password === null) {
    return res.status(422).json(validationError('MISSING_FIELDS', 'Email and password are required'));
  }
  if (typeof password !== 'string') {
    return res.status(422).json(validationError('INVALID_REQUEST', 'Password must be a string'));
  }
  if (password.length === 0) {
    return res.status(422).json(validationError('MISSING_FIELDS', 'Email and password are required'));
  }
  if (Buffer.byteLength(password, 'utf8') > MAX_PASSWORD_BYTES) {
    return res.status(422).json(validationError('INVALID_REQUEST', 'Password exceeds maximum length'));
  }

  // Set normalized values for downstream middleware
  req.normalizedEmail = trimmedEmail.toLowerCase();
  req.submittedPassword = password;
  req.loginBody = { email, password };

  next();
}

module.exports = loginValidator;
module.exports.loginValidator = loginValidator;
module.exports.MAX_BODY_BYTES = MAX_BODY_BYTES;
module.exports.MAX_EMAIL_CHARS = MAX_EMAIL_CHARS;
module.exports.MAX_PASSWORD_BYTES = MAX_PASSWORD_BYTES;
