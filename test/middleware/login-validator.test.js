'use strict';

/**
 * Unit tests for middleware/login-validator.js — Login Request Validator.
 *
 * Tests content-type enforcement, body size limit (2048 bytes),
 * JSON parsing, schema validation, email/password bounds, and normalization.
 *
 * Validates: Requirements 5.1–5.10
 *
 * @module test/middleware/login-validator
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const loginValidator = require('../../middleware/login-validator');

/**
 * Creates a minimal Express app with the login validator middleware
 * and a success handler that returns the normalized values.
 */
function buildApp() {
  const app = express();

  app.post('/api/auth/login', loginValidator, (req, res) => {
    res.status(200).json({
      normalizedEmail: req.normalizedEmail,
      submittedPassword: req.submittedPassword,
    });
  });

  return app;
}

// ==========================================================================
// Content-Type enforcement (Requirement 5.1)
// ==========================================================================

describe('Login Validator — Content-Type (Req 5.1)', () => {
  it('rejects request with no Content-Type header → 415', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', '')
      .send('{"email":"a@b.c","password":"pass"}');

    assert.equal(res.status, 415);
    assert.equal(res.body.error.type, 'ValidationError');
    assert.equal(res.body.error.code, 'INVALID_REQUEST');
  });

  it('rejects request with text/plain Content-Type → 415', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'text/plain')
      .send('{"email":"a@b.c","password":"pass"}');

    assert.equal(res.status, 415);
  });

  it('rejects request with multipart/form-data Content-Type → 415', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'multipart/form-data')
      .send('{"email":"a@b.c","password":"pass"}');

    assert.equal(res.status, 415);
  });

  it('accepts application/json Content-Type', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: 'pass123' }));

    assert.equal(res.status, 200);
  });

  it('accepts application/json with charset parameter', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json; charset=utf-8')
      .send(JSON.stringify({ email: 'user@test.com', password: 'pass123' }));

    assert.equal(res.status, 200);
  });
});

// ==========================================================================
// Body size limit (Requirements 5.2, 5.3)
// ==========================================================================

describe('Login Validator — Body Size Limit (Req 5.2, 5.3)', () => {
  it('rejects body larger than 2048 bytes → 413', async () => {
    const app = buildApp();
    // Create a body that exceeds 2048 bytes
    const longEmail = 'a'.repeat(200) + '@test.com';
    const longPassword = 'x'.repeat(1900);
    const body = JSON.stringify({ email: longEmail, password: longPassword });
    // Ensure it exceeds 2048 bytes
    assert.ok(Buffer.byteLength(body, 'utf8') > 2048);

    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(body);

    assert.equal(res.status, 413);
    assert.equal(res.body.error.code, 'INVALID_REQUEST');
  });

  it('accepts body of exactly 2048 bytes (boundary)', async () => {
    const app = buildApp();
    // Build a valid JSON body that is exactly 2048 bytes
    // {"email":"...","password":"..."} structure
    const prefix = '{"email":"user@test.com","password":"';
    const suffix = '"}';
    const overhead = Buffer.byteLength(prefix + suffix, 'utf8');
    const passwordLen = 2048 - overhead;
    const password = 'p'.repeat(passwordLen);
    const body = prefix + password + suffix;

    assert.equal(Buffer.byteLength(body, 'utf8'), 2048);

    // Password must be ≤72 bytes for validation to pass fully.
    // Since we need exactly 2048 bytes, and password would be too long,
    // the validator should accept body size but may reject password length.
    // Let's test that body size acceptance works (no 413).
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(body);

    // Should NOT be 413 — body size is accepted
    assert.notEqual(res.status, 413);
  });

  it('accepts body smaller than 2048 bytes', async () => {
    const app = buildApp();
    const body = JSON.stringify({ email: 'user@test.com', password: 'pass123' });
    assert.ok(Buffer.byteLength(body, 'utf8') < 2048);

    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(body);

    assert.equal(res.status, 200);
  });
});

// ==========================================================================
// JSON parsing (Requirement 5.4)
// ==========================================================================

describe('Login Validator — JSON Parsing (Req 5.4)', () => {
  it('rejects malformed JSON → 400', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('{not valid json');

    assert.equal(res.status, 400);
    assert.equal(res.body.error.code, 'INVALID_REQUEST');
  });

  it('rejects empty body → 400', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('');

    assert.equal(res.status, 400);
  });

  it('rejects trailing comma JSON → 400', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('{"email":"a@b.c","password":"x",}');

    assert.equal(res.status, 400);
  });
});

// ==========================================================================
// Schema validation (Requirements 5.5, 5.6)
// ==========================================================================

describe('Login Validator — Schema (Req 5.5, 5.6)', () => {
  it('rejects JSON array → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify([{ email: 'a@b.c', password: 'x' }]));

    assert.equal(res.status, 422);
  });

  it('rejects JSON null → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('null');

    assert.equal(res.status, 422);
  });

  it('rejects JSON number → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('42');

    assert.equal(res.status, 422);
  });

  it('rejects JSON string → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('"hello"');

    assert.equal(res.status, 422);
  });

  it('rejects JSON boolean → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send('true');

    assert.equal(res.status, 422);
  });

  it('rejects extra top-level fields → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'a@b.c', password: 'x', extra: 'field' }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'INVALID_REQUEST');
  });

  it('rejects nested object values → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: { nested: true }, password: 'x' }));

    assert.equal(res.status, 422);
  });

  it('rejects array values in fields → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: ['a@b.c'], password: 'x' }));

    assert.equal(res.status, 422);
  });
});

// ==========================================================================
// Email validation (Requirement 5.7)
// ==========================================================================

describe('Login Validator — Email (Req 5.7)', () => {
  it('rejects missing email field → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ password: 'pass123' }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'MISSING_FIELDS');
  });

  it('rejects null email → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: null, password: 'pass123' }));

    assert.equal(res.status, 422);
  });

  it('rejects non-string email (number) → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 123, password: 'pass123' }));

    assert.equal(res.status, 422);
  });

  it('rejects empty email after trim → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: '   ', password: 'pass123' }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'MISSING_FIELDS');
  });

  it('rejects email longer than 254 characters → 422', async () => {
    const app = buildApp();
    const longEmail = 'a'.repeat(250) + '@b.co';  // 255 chars
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: longEmail, password: 'pass123' }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'INVALID_REQUEST');
  });

  it('accepts email of exactly 254 characters', async () => {
    const app = buildApp();
    const email254 = 'a'.repeat(249) + '@b.co';  // 254 chars
    assert.equal(email254.length, 254);
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: email254, password: 'pass123' }));

    assert.equal(res.status, 200);
  });
});

// ==========================================================================
// Password validation (Requirement 5.8)
// ==========================================================================

describe('Login Validator — Password (Req 5.8)', () => {
  it('rejects missing password field → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com' }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'MISSING_FIELDS');
  });

  it('rejects null password → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: null }));

    assert.equal(res.status, 422);
  });

  it('rejects non-string password (boolean) → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: true }));

    assert.equal(res.status, 422);
  });

  it('rejects empty password → 422', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: '' }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'MISSING_FIELDS');
  });

  it('rejects password exceeding 72 UTF-8 bytes → 422', async () => {
    const app = buildApp();
    // Each emoji is 4 UTF-8 bytes; 18 emojis = 72 bytes + one more = 76 bytes
    const longPassword = '😀'.repeat(19);  // 19 * 4 = 76 bytes
    assert.ok(Buffer.byteLength(longPassword, 'utf8') > 72);

    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: longPassword }));

    assert.equal(res.status, 422);
    assert.equal(res.body.error.code, 'INVALID_REQUEST');
  });

  it('accepts password of exactly 72 UTF-8 bytes', async () => {
    const app = buildApp();
    // 72 ASCII chars = 72 UTF-8 bytes
    const password72 = 'a'.repeat(72);
    assert.equal(Buffer.byteLength(password72, 'utf8'), 72);

    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: password72 }));

    assert.equal(res.status, 200);
  });

  it('correctly measures multi-byte password length', async () => {
    const app = buildApp();
    // 18 emojis = 72 bytes exactly (each emoji is 4 bytes)
    const password72Emoji = '😀'.repeat(18);
    assert.equal(Buffer.byteLength(password72Emoji, 'utf8'), 72);

    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password: password72Emoji }));

    assert.equal(res.status, 200);
  });
});

// ==========================================================================
// Email normalization (Requirement 5.9)
// ==========================================================================

describe('Login Validator — Normalization (Req 5.9)', () => {
  it('normalizes email by trimming and lowercasing', async () => {
    const app = buildApp();
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: '  User@Test.COM  ', password: 'pass123' }));

    assert.equal(res.status, 200);
    assert.equal(res.body.normalizedEmail, 'user@test.com');
  });

  it('preserves password unmodified (Req 5.10)', async () => {
    const app = buildApp();
    const password = '  My Pass Word!  ';
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password }));

    assert.equal(res.status, 200);
    assert.equal(res.body.submittedPassword, password);
  });

  it('does not trim or modify password', async () => {
    const app = buildApp();
    const password = '  spaces  ';
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'application/json')
      .send(JSON.stringify({ email: 'user@test.com', password }));

    assert.equal(res.status, 200);
    assert.equal(res.body.submittedPassword, '  spaces  ');
  });
});

// ==========================================================================
// Order of checks — 415 before 413 before 400 before 422
// ==========================================================================

describe('Login Validator — Rejection Order', () => {
  it('returns 415 before checking body size (oversized body with wrong content-type)', async () => {
    const app = buildApp();
    const largeBody = 'x'.repeat(3000);
    const res = await request(app)
      .post('/api/auth/login')
      .set('Content-Type', 'text/plain')
      .send(largeBody);

    // Should be 415 (content-type), not 413 (body size)
    assert.equal(res.status, 415);
  });
});
