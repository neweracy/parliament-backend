"use strict";

const { describe, it, beforeEach, afterEach, mock } = require("node:test");
const assert = require("node:assert/strict");
const request = require("supertest");
const buildApp = require("../helpers/app");
const { signValidToken, signExpiredToken, invalidToken } = require("../helpers/jwt");
const audio = require("../fixtures/audio");

const TEST_SECRET = "test-secret-for-jwt";

describe("routes/khaya - authentication", () => {
  let app;

  beforeEach(() => {
    app = buildApp({ secret: TEST_SECRET });
  });

  it("POST /api/khaya/transcription without Authorization header returns 401 MISSING_TOKEN", async () => {
    const res = await request(app)
      .post("/api/khaya/transcription")
      .expect(401);

    assert.equal(res.body.error.code, "MISSING_TOKEN");
    assert.equal(res.body.error.type, "AuthenticationError");
  });

  it("POST /api/khaya/transcription with invalid token returns 401 INVALID_TOKEN", async () => {
    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + invalidToken())
      .expect(401);

    assert.equal(res.body.error.code, "INVALID_TOKEN");
    assert.equal(res.body.error.type, "AuthenticationError");
  });

  it("POST /api/khaya/transcription with expired token returns 401 INVALID_TOKEN", async () => {
    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + signExpiredToken(TEST_SECRET))
      .expect(401);

    assert.equal(res.body.error.code, "INVALID_TOKEN");
    assert.equal(res.body.error.type, "AuthenticationError");
  });

  it("POST /api/khaya/transcription with valid token passes auth and reaches validation", async () => {
    // Send as multipart/form-data (so multer parses req.body) but with no file
    // expecting 400 MISSING_INPUT (proving auth passed)
    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + signValidToken(TEST_SECRET))
      .field("language", "tw")
      .expect(400);

    assert.equal(res.body.error.code, "MISSING_INPUT");
    assert.equal(res.body.error.type, "ValidationError");
  });

  it("GET /api/khaya/languages is processed without auth failure when no Authorization header is present", async () => {
    // The languages endpoint does not require auth.
    // Without KHAYA_API_KEY set it may return 500 MISSING_API_KEY,
    // but it must NOT return 401.
    const res = await request(app)
      .get("/api/khaya/languages");

    assert.notEqual(res.status, 401, "languages endpoint should not require authentication");
  });
});

describe("routes/khaya - input validation", () => {
  const TEST_SECRET_VALIDATION = "test-secret-for-validation";
  let app;
  let token;
  const khaya = require("../../providers/khaya");

  beforeEach(() => {
    app = buildApp({ secret: TEST_SECRET_VALIDATION });
    token = signValidToken(TEST_SECRET_VALIDATION);
  });

  afterEach(() => {
    mock.restoreAll();
  });

  it("missing file returns 400 ValidationError / MISSING_INPUT", async () => {
    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .field("language", "tw")
      .expect(400);

    assert.equal(res.body.error.type, "ValidationError");
    assert.equal(res.body.error.code, "MISSING_INPUT");
  });

  it("file present but missing language returns 400 ValidationError / MISSING_LANGUAGE", async () => {
    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .expect(400);

    assert.equal(res.body.error.type, "ValidationError");
    assert.equal(res.body.error.code, "MISSING_LANGUAGE");
  });

  it("both file and language present invokes provider transcribe with correct args", async () => {
    mock.method(khaya, "transcribe", async () => ({ transcript: "test", words: [], metadata: {} }));

    await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .field("language", "tw")
      .expect(200);

    assert.equal(khaya.transcribe.mock.calls.length, 1);

    const call = khaya.transcribe.mock.calls[0];
    const [buffer, mimetype, language] = call.arguments;

    // Buffer content should match the uploaded audio fixture
    assert.ok(Buffer.isBuffer(buffer), "first arg should be a Buffer");
    assert.deepEqual(buffer, audio.mp3.buffer);
    assert.equal(mimetype, "audio/mpeg");
    assert.equal(language, "tw");
  });
});

describe("routes/khaya - success responses", () => {
  const TEST_SECRET_SUCCESS = "test-secret-for-success";
  const khaya = require("../../providers/khaya");
  const responses = require("../fixtures/responses");
  let app;
  let token;

  const transcriptionResult = {
    transcript: "Ɛte sɛn? Me din de Kofi.",
    words: [{ word: "Ɛte", start: 0.0, end: 0.3 }],
    duration: 5.2,
    metadata: { provider: "khaya-ai", api_version: "v3", language: "tw" },
  };

  beforeEach(() => {
    app = buildApp({ secret: TEST_SECRET_SUCCESS });
    token = signValidToken(TEST_SECRET_SUCCESS);
  });

  afterEach(() => {
    mock.restoreAll();
  });

  it("POST /api/khaya/transcription returns 200 with transcription result", async () => {
    mock.method(khaya, "transcribe", async () => transcriptionResult);

    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .field("language", "tw")
      .expect(200);

    assert.deepEqual(res.body, transcriptionResult);
  });

  it("GET /api/khaya/languages returns 200 with languages payload", async () => {
    mock.method(khaya, "getLanguages", async () => responses.languages);

    const res = await request(app)
      .get("/api/khaya/languages")
      .expect(200);

    assert.deepEqual(res.body, responses.languages);
  });
});

describe("routes/khaya - error mapping", () => {
  const TEST_SECRET_ERRORS = "test-secret-for-errors";
  const khaya = require("../../providers/khaya");
  let app;
  let token;

  beforeEach(() => {
    app = buildApp({ secret: TEST_SECRET_ERRORS });
    token = signValidToken(TEST_SECRET_ERRORS);
  });

  afterEach(() => {
    mock.restoreAll();
  });

  it("transcribe throws with statusCode 401 / INVALID_API_KEY / AuthenticationError → 401 structured error", async () => {
    mock.method(khaya, "transcribe", async () => {
      const err = new Error("Invalid Khaya API key");
      err.statusCode = 401;
      err.code = "INVALID_API_KEY";
      err.type = "AuthenticationError";
      throw err;
    });

    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .field("language", "tw")
      .expect(401);

    assert.equal(res.body.error.type, "AuthenticationError");
    assert.equal(res.body.error.code, "INVALID_API_KEY");
    assert.equal(res.body.error.message, "Invalid Khaya API key");
  });

  it("transcribe throws with statusCode 429 / QUOTA_EXCEEDED / RateLimitError → 429 structured error", async () => {
    mock.method(khaya, "transcribe", async () => {
      const err = new Error("Rate limit exceeded");
      err.statusCode = 429;
      err.code = "QUOTA_EXCEEDED";
      err.type = "RateLimitError";
      throw err;
    });

    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .field("language", "tw")
      .expect(429);

    assert.equal(res.body.error.type, "RateLimitError");
    assert.equal(res.body.error.code, "QUOTA_EXCEEDED");
    assert.equal(res.body.error.message, "Rate limit exceeded");
  });

  it("transcribe throws with statusCode 500 / MISSING_API_KEY / ConfigurationError → 500 structured error", async () => {
    mock.method(khaya, "transcribe", async () => {
      const err = new Error("KHAYA_API_KEY is not configured");
      err.statusCode = 500;
      err.code = "MISSING_API_KEY";
      err.type = "ConfigurationError";
      throw err;
    });

    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .field("language", "tw")
      .expect(500);

    assert.equal(res.body.error.type, "ConfigurationError");
    assert.equal(res.body.error.code, "MISSING_API_KEY");
    assert.equal(res.body.error.message, "KHAYA_API_KEY is not configured");
  });

  it("transcribe throws without statusCode → 500 with TRANSCRIPTION_FAILED / TranscriptionError", async () => {
    mock.method(khaya, "transcribe", async () => {
      throw new Error("Something unexpected");
    });

    const res = await request(app)
      .post("/api/khaya/transcription")
      .set("Authorization", "Bearer " + token)
      .attach("file", audio.mp3.buffer, { filename: "test.mp3", contentType: "audio/mpeg" })
      .field("language", "tw")
      .expect(500);

    assert.equal(res.body.error.type, "TranscriptionError");
    assert.equal(res.body.error.code, "TRANSCRIPTION_FAILED");
    assert.equal(res.body.error.message, "Something unexpected");
  });

  it("getLanguages throws with statusCode 503 → uses that status + structured error", async () => {
    mock.method(khaya, "getLanguages", async () => {
      const err = new Error("Service unavailable");
      err.statusCode = 503;
      err.code = "SERVICE_UNAVAILABLE";
      err.type = "ProviderError";
      throw err;
    });

    const res = await request(app)
      .get("/api/khaya/languages")
      .expect(503);

    assert.equal(res.body.error.type, "ProviderError");
    assert.equal(res.body.error.code, "SERVICE_UNAVAILABLE");
    assert.equal(res.body.error.message, "Service unavailable");
  });

  it("getLanguages throws without statusCode → 500 with LANGUAGES_FETCH_FAILED / ProviderError", async () => {
    mock.method(khaya, "getLanguages", async () => {
      throw new Error("Unknown failure");
    });

    const res = await request(app)
      .get("/api/khaya/languages")
      .expect(500);

    assert.equal(res.body.error.type, "ProviderError");
    assert.equal(res.body.error.code, "LANGUAGES_FETCH_FAILED");
    assert.equal(res.body.error.message, "Unknown failure");
  });
});
