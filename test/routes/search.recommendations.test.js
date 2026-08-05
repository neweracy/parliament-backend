"use strict";

const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const express = require("express");
const request = require("supertest");
const searchRoutes = require("../../routes/search");

const {
  mapRecommendationResponse,
  DEFAULT_RECOMMENDATION_LIMIT,
  MAX_RECOMMENDATION_LIMIT,
} = searchRoutes;

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

/** Stub upstream returning `body`, recording the request it received. */
function stubUpstream(body, { status = 200 } = {}) {
  const calls = [];
  global.fetch = (url, options) => {
    calls.push({ url: String(url), body: JSON.parse(options.body) });
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  };
  return calls;
}

let originalFetch;

beforeEach(() => {
  originalFetch = global.fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe("POST /api/search/recommendations", () => {
  it("proxies to /rag/recommendations, not /rag/ask", async () => {
    const calls = stubUpstream({ recommendations: [], latency_ms: 0 });

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({ query: "health" });

    assert.equal(res.status, 200);
    assert.equal(calls.length, 1);
    assert.ok(
      calls[0].url.endsWith("/rag/recommendations"),
      `expected /rag/recommendations, got ${calls[0].url}`
    );
  });

  it("maps snake_case upstream fields to camelCase", async () => {
    stubUpstream({
      recommendations: [
        { text: "NHIS funding", reason: "Health financing debates" },
        { text: "teacher allowance arrears", reason: "Recurring education grievance" },
      ],
      latency_ms: 321.5,
      source: "mixed",
      model_used: true,
    });

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({ query: "health" });

    assert.equal(res.status, 200);
    assert.deepEqual(res.body.recommendations, [
      { text: "NHIS funding", reason: "Health financing debates" },
      { text: "teacher allowance arrears", reason: "Recurring education grievance" },
    ]);
    assert.equal(res.body.latencyMs, 321.5);
    assert.equal(res.body.source, "mixed");
    assert.equal(res.body.modelUsed, true);
  });

  it("passes plain text through untouched — no bold markers required", async () => {
    // The previous implementation kept only text inside ** ** and dropped the
    // rest, which silently emptied the list whenever the model did not bold.
    stubUpstream({
      recommendations: [
        { text: "budget allocation", reason: "Appropriation debates" },
        { text: "committee report", reason: "Tabled often" },
        { text: "standing orders procedure", reason: "Rules and process" },
      ],
      latency_ms: 10,
    });

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({ query: "budget" });

    assert.equal(res.status, 200);
    assert.equal(res.body.recommendations.length, 3);
    assert.deepEqual(
      res.body.recommendations.map((r) => r.text),
      ["budget allocation", "committee report", "standing orders procedure"]
    );
  });

  it("forwards the query and filters upstream in snake_case", async () => {
    const calls = stubUpstream({ recommendations: [], latency_ms: 0 });

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({
      query: "  energy  ",
      speaker: "Hon. Ama Mensah",
      entityFilter: ["Ministry of Energy"],
      dateFrom: "2026-01-01",
      dateTo: "2026-12-31",
    });

    assert.equal(res.status, 200);
    const sent = calls[0].body;
    assert.equal(sent.query, "energy", "query should be trimmed");
    assert.equal(sent.speaker, "Hon. Ama Mensah");
    assert.deepEqual(sent.entity_filter, ["Ministry of Energy"]);
    assert.equal(sent.date_from, "2026-01-01");
    assert.equal(sent.date_to, "2026-12-31");
    // The old implementation wrapped the query in a prompt sentence instead.
    assert.equal(sent.question, undefined);
  });

  it("forwards limit upstream and clamps it to the shared band", async () => {
    const app = buildApp();

    let calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({ query: "x", limit: 3 });
    assert.equal(calls[0].body.limit, 3);

    calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({ query: "x" });
    assert.equal(calls[0].body.limit, DEFAULT_RECOMMENDATION_LIMIT);

    calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({ query: "x", limit: 99 });
    assert.equal(
      calls[0].body.limit,
      MAX_RECOMMENDATION_LIMIT,
      "an over-band limit must be clamped, not passed through as a 422"
    );

    calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({ query: "x", limit: 0 });
    assert.equal(calls[0].body.limit, DEFAULT_RECOMMENDATION_LIMIT);

    calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({ query: "x", limit: "abc" });
    assert.equal(calls[0].body.limit, DEFAULT_RECOMMENDATION_LIMIT);
  });

  it("omits query entirely when blank so upstream returns corpus entry points", async () => {
    const app = buildApp();

    let calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({});
    assert.equal(calls[0].body.query, undefined);

    calls = stubUpstream({ recommendations: [], latency_ms: 0 });
    await request(app).post("/api/search/recommendations").send({ query: "   " });
    assert.equal(calls[0].body.query, undefined);
  });

  it("reports an upstream failure as 502", async () => {
    stubUpstream({ detail: "boom" }, { status: 500 });

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({ query: "health" });

    assert.equal(res.status, 502);
    assert.equal(res.body.error.code, "RAG_RECOMMENDATIONS_FAILED");
  });

  it("reports an unreachable service as 503", async () => {
    global.fetch = () => {
      const err = new Error("connect ECONNREFUSED");
      err.cause = { code: "ECONNREFUSED" };
      return Promise.reject(err);
    };

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({ query: "health" });

    assert.equal(res.status, 503);
    assert.equal(res.body.error.code, "RAG_UNAVAILABLE");
  });

  it("reports an aborted request as 504", async () => {
    global.fetch = () => {
      const err = new Error("aborted");
      err.name = "AbortError";
      return Promise.reject(err);
    };

    const app = buildApp();
    const res = await request(app).post("/api/search/recommendations").send({ query: "health" });

    assert.equal(res.status, 504);
    assert.equal(res.body.error.code, "RAG_TIMEOUT");
  });
});

describe("mapRecommendationResponse", () => {
  it("drops items with no usable text and trims the rest", () => {
    const mapped = mapRecommendationResponse(
      {
        recommendations: [
          { text: "  spending allocation  ", reason: "  Funds distribution  " },
          { text: "   ", reason: "blank text is unusable" },
          { text: null, reason: "missing text" },
          null,
        ],
        latency_ms: 5,
      },
      8
    );

    assert.deepEqual(mapped.recommendations, [
      { text: "spending allocation", reason: "Funds distribution" },
    ]);
  });

  it("caps at the requested limit", () => {
    const raw = {
      recommendations: Array.from({ length: 8 }, (_, i) => ({
        text: `term ${i}`,
        reason: "why",
      })),
    };

    assert.equal(mapRecommendationResponse(raw, 3).recommendations.length, 3);
    assert.equal(mapRecommendationResponse(raw, 8).recommendations.length, 8);
  });

  it("defaults source and modelUsed when upstream omits them", () => {
    const mapped = mapRecommendationResponse({ recommendations: [] }, 5);

    assert.equal(mapped.source, "deterministic");
    assert.equal(mapped.modelUsed, false);
    assert.equal(mapped.latencyMs, 0);
  });

  it("tolerates a missing or malformed body", () => {
    for (const raw of [undefined, null, {}, { recommendations: null }]) {
      const mapped = mapRecommendationResponse(raw, 5);
      assert.deepEqual(mapped.recommendations, []);
      assert.equal(mapped.latencyMs, 0);
    }
  });
});
