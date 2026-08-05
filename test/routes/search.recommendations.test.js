"use strict";

const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const express = require("express");
const request = require("supertest");
const searchRoutes = require("../../routes/search");

function passThrough(req, _res, next) {
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

function buildApp() {
  const app = express();
  app.use(express.json());
  app.use(searchRoutes(passThrough, mockDb));
  return app;
}

let originalFetch;

beforeEach(() => {
  originalFetch = global.fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("POST /api/search/recommendations", () => {
  it("maps and limits recommendation items from /rag/ask", async () => {
    global.fetch = (url) => {
      if (!String(url).includes("/rag/ask")) {
        throw new Error(`Unexpected fetch to ${url}`);
      }

      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          recommendations: [
            { text: "Try **NHIS funding**", reason: "Frequent topic in recent sittings" },
            { text: "Explore **healthcare reform debate**", reason: "Multiple members discussed health policy" },
            { text: "What decisions were made on infrastructure spending?", reason: "non-bold should be ignored" },
            { text: "  ", reason: "invalid and should be removed" },
          ],
          latency_ms: 321,
        }),
      });
    };

    const app = buildApp();
    const res = await request(app)
      .post("/api/search/recommendations")
      .send({ query: "health", limit: 2 });

    assert.equal(res.status, 200);
    assert.equal(res.body.recommendations.length, 2);
    assert.equal(res.body.recommendations[0].text, "NHIS funding");
    assert.equal(res.body.recommendations[1].text, "healthcare reform debate");
    assert.equal(res.body.latencyMs, 321);
  });

  it("forwards query context and filters to /rag/ask", async () => {
    let capturedBody = null;

    global.fetch = (_url, options) => {
      capturedBody = JSON.parse(options.body);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ recommendations: [], latency_ms: 0 }),
      });
    };

    const app = buildApp();
    const res = await request(app)
      .post("/api/search/recommendations")
      .send({
        query: "energy",
        speaker: "Hon. Ama Mensah",
        entityFilter: ["Ministry of Energy"],
        dateFrom: "2026-01-01",
        dateTo: "2026-12-31",
      });

    assert.equal(res.status, 200);
    assert.ok(capturedBody);
    assert.equal(typeof capturedBody.question, "string");
    assert.ok(capturedBody.question.includes("energy"));
    assert.equal(capturedBody.speaker, "Hon. Ama Mensah");
    assert.deepEqual(capturedBody.entity_filter, ["Ministry of Energy"]);
    assert.equal(capturedBody.date_from, "2026-01-01");
    assert.equal(capturedBody.date_to, "2026-12-31");
  });
});
