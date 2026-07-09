"use strict";

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const express = require("express");
const multer = require("multer");
const jwt = require("jsonwebtoken");
const request = require("supertest");
const createRouter = require("../../routes/hybrid");
const { signValidToken } = require("../helpers/jwt");
const audio = require("../fixtures/audio");

const TEST_SECRET = "test-secret-for-hybrid";

/**
 * Fake Deepgram response with two high-confidence words. runHybridPipeline's
 * extractWords reads results.channels[0].alternatives[0].words[]. All words are
 * high-confidence, so the pipeline takes the passthrough branch and still
 * returns a full unified body (segments, words, metadata.correctionStats).
 */
const fakeDeepgramResponse = {
  result: {
    metadata: { duration: 5.0, model_name: "nova-3" },
    results: {
      channels: [
        {
          alternatives: [
            {
              transcript: "hello world",
              words: [
                { word: "hello", start: 0.0, end: 0.5, confidence: 0.99 },
                { word: "world", start: 0.6, end: 1.0, confidence: 0.95 },
              ],
            },
          ],
        },
      ],
    },
  },
};

/**
 * Builds injected fake pipeline collaborators. transcribePrimary returns a valid
 * Deepgram response so the REAL runHybridPipeline produces a real unified result.
 * @returns {import('../../lib/hybrid/pipeline').HybridDeps}
 */
function buildFakeDeps() {
  return {
    transcribePrimary: async () => fakeDeepgramResponse,
    khayaTranscribe: async () => ({ transcript: "correction" }),
    sliceAndConcatAudio: async () => ({ buffer: Buffer.from("x"), mimetype: "audio/mpeg" }),
    khayaConfigured: () => true,
  };
}

/**
 * Builds an Express app mounting the hybrid router with a production-faithful
 * requireSession middleware (mirrors test/helpers/app.js), an in-memory multer
 * instance, and injected fake deps.
 * @param {{ secret: string, deps?: object }} opts
 * @returns {import('express').Express}
 */
function buildHybridApp({ secret, deps = buildFakeDeps() }) {
  const app = express();

  function requireSession(req, res, next) {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "MISSING_TOKEN",
          message: "Authorization header with Bearer token is required",
        },
      });
    }

    try {
      const token = authHeader.slice(7);
      jwt.verify(token, secret);
      next();
    } catch (err) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message:
            err.name === "TokenExpiredError"
              ? "Session expired, please refresh the page"
              : "Invalid session token",
        },
      });
    }
  }

  const upload = multer({ storage: multer.memoryStorage() });

  app.use("/api/transcription/hybrid", createRouter(requireSession, upload, deps));

  return app;
}

describe("routes/hybrid", () => {
  let app;

  beforeEach(() => {
    app = buildHybridApp({ secret: TEST_SECRET });
  });

  it("POST /api/transcription/hybrid without Authorization header returns 401 MISSING_TOKEN", async () => {
    const res = await request(app)
      .post("/api/transcription/hybrid")
      .expect(401);

    assert.equal(res.body.error.type, "AuthenticationError");
    assert.equal(res.body.error.code, "MISSING_TOKEN");
  });

  it("POST /api/transcription/hybrid with valid token but no file returns 400 MISSING_INPUT", async () => {
    // Send multipart/form-data (so multer parses req.body) but with no file.
    const res = await request(app)
      .post("/api/transcription/hybrid")
      .set("Authorization", "Bearer " + signValidToken(TEST_SECRET))
      .field("language", "tw")
      .expect(400);

    assert.equal(res.body.error.type, "ValidationError");
    assert.equal(res.body.error.code, "MISSING_INPUT");
  });

  it("POST /api/transcription/hybrid with valid token and file returns 200 with unified body", async () => {
    const res = await request(app)
      .post("/api/transcription/hybrid")
      .set("Authorization", "Bearer " + signValidToken(TEST_SECRET))
      .attach("file", audio.mp3.buffer, {
        filename: "sample.mp3",
        contentType: audio.mp3.mimetype,
      })
      .expect(200);

    // Unified body shape.
    assert.ok(Array.isArray(res.body.segments), "segments should be an array");
    assert.ok(Array.isArray(res.body.words), "words should be an array");
    assert.ok(
      res.body.metadata && typeof res.body.metadata === "object",
      "metadata should be an object"
    );

    const stats = res.body.metadata.correctionStats;
    assert.ok(
      stats && typeof stats === "object",
      "metadata.correctionStats should be an object"
    );
    assert.equal(typeof stats.segmentsDetected, "number");
    assert.equal(typeof stats.corrected, "boolean");
    assert.equal(typeof stats.correctionSkipped, "boolean");
  });
});
