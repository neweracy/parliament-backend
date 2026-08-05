"use strict";

/**
 * Tests for the snake_case → camelCase mapping in POST /api/search.
 *
 * The Python service returns `sitting_id` and `record_id` — the real navigation
 * target for a result card. `transcript.id`, `hansard_record.id`, and
 * `sitting.id` are three distinct identifier spaces, so a result that arrives
 * without those two ids cannot be navigated to and the frontend must be told so
 * explicitly (null) rather than being handed a transcript id to guess with.
 *
 * @module test/routes/search.mapping
 */

const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const express = require("express");
const request = require("supertest");
const searchRoutes = require("../../routes/search");

function passThrough(req, res, next) {
  req.user = {
    userId: "test-user",
    role: "Admin",
    permissions: ["search_hansard"],
  };
  next();
}

const mockDb = {
  query() {
    return Promise.resolve({ rows: [] });
  },
};

/** Builds an app whose upstream /rag/search returns exactly `results`. */
function appReturning(results) {
  const app = express();
  app.use(express.json());
  app.use(searchRoutes(passThrough, mockDb));
  global.fetch = (url) => {
    if (String(url).includes("/rag/search")) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({ results, total_matched: results.length, latency_ms: 12 }),
      });
    }
    throw new Error(`Unexpected fetch to ${url}`);
  };
  return app;
}

let originalFetch;

beforeEach(() => {
  originalFetch = global.fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("POST /api/search — navigation id mapping", () => {
  it("maps sitting_id and record_id to sittingId and recordId", async () => {
    // transcript 2 lives inside record 1 of sitting 1 — all three differ, so a
    // regression to "transcriptId for everything" cannot pass this.
    const app = appReturning([
      {
        chunk_id: 63,
        chunk_text: "The education budget rose.",
        relevance_score: 0.9,
        transcript_id: 2,
        speaker: "Hon. Ama Mensah",
        start_s: 754,
        end_s: 812,
        matched_entities: [],
        record_title: "Budget Debate 2026",
        sitting_title: "First Sitting",
        date: "2026-02-01",
        sitting_id: 1,
        record_id: 1,
      },
    ]);

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.strictEqual(res.status, 200);
    assert.strictEqual(res.body.results[0].transcriptId, 2);
    assert.strictEqual(res.body.results[0].sittingId, 1);
    assert.strictEqual(res.body.results[0].recordId, 1);
  });

  it("maps absent sitting_id and record_id to null", async () => {
    const app = appReturning([
      {
        chunk_id: 63,
        chunk_text: "text",
        relevance_score: 0.9,
        transcript_id: 2,
        speaker: null,
        start_s: 0,
        end_s: 1,
        matched_entities: [],
      },
    ]);

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.strictEqual(res.status, 200);
    assert.strictEqual(res.body.results[0].sittingId, null);
    assert.strictEqual(res.body.results[0].recordId, null);
  });

  it("preserves an id of 0 rather than coercing it to null", async () => {
    const app = appReturning([
      {
        chunk_id: 1,
        chunk_text: "text",
        relevance_score: 0.5,
        transcript_id: 1,
        speaker: null,
        start_s: 0,
        end_s: 1,
        matched_entities: [],
        sitting_id: 0,
        record_id: 0,
      },
    ]);

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.strictEqual(res.body.results[0].sittingId, 0);
    assert.strictEqual(res.body.results[0].recordId, 0);
  });
});
