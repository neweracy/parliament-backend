'use strict';

/**
 * JSON body limit tests.
 *
 * The gateway used to mount a single global `express.json({ limit: '2kb' })`.
 * That rejected POST /api/ask with 413 as soon as the forwarded
 * conversationHistory grew past 2KB, and it also consumed the login body first,
 * making the login route's own tighter parser dead code.
 *
 * These tests pin the replacement behaviour:
 * - non-login routes accept bodies well past 2KB, up to the global limit
 * - over the global limit returns the project's error envelope, not a bare 413
 * - /api/auth/login keeps its own 2KB cap
 * - malformed JSON returns the INVALID_JSON envelope
 *
 * @module test/routes/body-limit
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const loginValidator = require('../../middleware/login-validator');

/** Same default as server.js. */
const DEFAULT_JSON_BODY_LIMIT = '256kb';
const LOGIN_PATH = '/api/auth/login';

/**
 * Builds a minimal app that mirrors server.js's body-parsing chain: a global
 * JSON parser that skips the login path, a login route carrying its own 2KB
 * parser, and the shared body-parser error envelope registered after routes.
 *
 * @param {{ limit?: string }} [options]
 */
function buildApp({ limit = DEFAULT_JSON_BODY_LIMIT } = {}) {
  const app = express();

  const globalJsonParser = express.json({ limit, strict: true });
  app.use((req, res, next) => {
    if (req.path === LOGIN_PATH) return next();
    return globalJsonParser(req, res, next);
  });

  // Stand-in for a real protected route (e.g. POST /api/ask). Echoes back what
  // the parser produced so a successful parse is observable.
  app.post('/api/ask', (req, res) => {
    res.json({ ok: true, bytes: Buffer.byteLength(JSON.stringify(req.body), 'utf8') });
  });

  // Login mounts its own parser, exactly as routes/auth.js does.
  app.post(
    LOGIN_PATH,
    express.json({ limit: loginValidator.MAX_BODY_BYTES, strict: false }),
    (req, res) => {
      res.json({ ok: true, keys: Object.keys(req.body || {}) });
    }
  );

  app.use((err, _req, res, next) => {
    if (err && (err.type === 'entity.too.large' || err.status === 413)) {
      return res.status(413).json({
        error: {
          type: 'ValidationError',
          code: 'PAYLOAD_TOO_LARGE',
          message: 'Request body is too large. Start a new conversation or shorten your question.',
        },
      });
    }
    if (err && err.type === 'entity.parse.failed') {
      return res.status(400).json({
        error: {
          type: 'ValidationError',
          code: 'INVALID_JSON',
          message: 'Request body must be valid JSON',
        },
      });
    }
    return next(err);
  });

  return app;
}

/** Builds a JSON body whose serialized size is at least `bytes`. */
function bodyOfAtLeast(bytes) {
  return { question: 'x'.repeat(bytes) };
}

describe('Global JSON body limit', () => {
  it('accepts a body over the old 2kb cap on a non-login route', async () => {
    const app = buildApp();
    const payload = bodyOfAtLeast(8 * 1024);

    const res = await request(app).post('/api/ask').send(payload);

    assert.notEqual(res.status, 413);
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.ok(res.body.bytes > 2048, 'parsed body should exceed the old 2kb limit');
  });

  it('accepts a ~64KB conversation history, the shape that used to fail', async () => {
    const app = buildApp();
    const conversationHistory = Array.from({ length: 20 }, (_, i) => ({
      role: i % 2 === 0 ? 'user' : 'assistant',
      content: 'y'.repeat(3000),
    }));

    const res = await request(app)
      .post('/api/ask')
      .send({ question: 'And what changed since then?', conversationHistory });

    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
  });

  it('rejects a body over the global limit with the PAYLOAD_TOO_LARGE envelope', async () => {
    const app = buildApp();

    const res = await request(app).post('/api/ask').send(bodyOfAtLeast(300 * 1024));

    assert.equal(res.status, 413);
    assert.equal(res.body.error.type, 'ValidationError');
    assert.equal(res.body.error.code, 'PAYLOAD_TOO_LARGE');
    assert.match(res.body.error.message, /too large/i);
  });

  it('honours a configured limit lower than the default', async () => {
    const app = buildApp({ limit: '4kb' });

    const under = await request(app).post('/api/ask').send(bodyOfAtLeast(1024));
    assert.equal(under.status, 200);

    const over = await request(app).post('/api/ask').send(bodyOfAtLeast(8 * 1024));
    assert.equal(over.status, 413);
    assert.equal(over.body.error.code, 'PAYLOAD_TOO_LARGE');
  });
});

describe('Login route body limit', () => {
  it('accepts a normal credential payload', async () => {
    const app = buildApp();

    const res = await request(app)
      .post(LOGIN_PATH)
      .send({ email: 'clerk@parliament.gov.gh', password: 'correct-horse-battery' });

    assert.equal(res.status, 200);
    assert.deepEqual(res.body.keys.sort(), ['email', 'password']);
  });

  it('still rejects a body over its own 2KB cap', async () => {
    const app = buildApp();

    const res = await request(app)
      .post(LOGIN_PATH)
      .send({ email: 'clerk@parliament.gov.gh', password: 'p'.repeat(4096) });

    assert.equal(res.status, 413);
    assert.equal(res.body.error.code, 'PAYLOAD_TOO_LARGE');
  });

  it('applies the 2KB cap even though the global limit is far larger', async () => {
    // Sized to sit between the login cap and the global limit: proof that the
    // login parser — not the global one — decided this request.
    const app = buildApp();
    const between = 32 * 1024;

    const login = await request(app)
      .post(LOGIN_PATH)
      .send({ email: 'clerk@parliament.gov.gh', password: 'p'.repeat(between) });
    assert.equal(login.status, 413);

    const nonLogin = await request(app).post('/api/ask').send(bodyOfAtLeast(between));
    assert.equal(nonLogin.status, 200);
  });
});

describe('Malformed JSON', () => {
  it('returns the INVALID_JSON envelope on a non-login route', async () => {
    const app = buildApp();

    const res = await request(app)
      .post('/api/ask')
      .set('Content-Type', 'application/json')
      .send('{"question": "unterminated');

    assert.equal(res.status, 400);
    assert.equal(res.body.error.type, 'ValidationError');
    assert.equal(res.body.error.code, 'INVALID_JSON');
  });

  it('returns the INVALID_JSON envelope on the login route', async () => {
    const app = buildApp();

    const res = await request(app)
      .post(LOGIN_PATH)
      .set('Content-Type', 'application/json')
      .send('{"email": ');

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'INVALID_JSON');
  });
});
