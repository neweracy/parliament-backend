"use strict";

/**
 * Tests for search results and suggestions caching in the search route.
 *
 * Validates cache-aside behavior: hits bypass upstream, misses proxy and store,
 * errors are never cached, and graceful degradation when cache is null.
 *
 * All tests mock Redis via a fake CacheUtil — no running Redis instance required (Req 9.4).
 *
 * @module test/routes/search-cache
 */

const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("crypto");
const express = require("express");
const request = require("supertest");
const searchRoutes = require("../../routes/search");

// --- Mocks ---

/** Auth middleware that passes through with Admin permissions */
function passThrough(req, res, next) {
  req.user = {
    userId: "test-user",
    role: "Admin",
    permissions: ["search_hansard"],
  };
  next();
}

/** Creates a mock cache utility that mimics lib/cache.js behavior */
function createMockCache() {
  const store = new Map();
  const setCalls = [];
  const getCalls = [];

  return {
    key: (ns, ...parts) => `parliament:${ns}:${parts.join(":")}`,
    get: async (key) => {
      getCalls.push(key);
      return store.get(key) || null;
    },
    set: async (key, value, ttl) => {
      setCalls.push({ key, value, ttl });
      store.set(key, value);
      return true;
    },
    del: async (key) => {
      store.delete(key);
      return true;
    },
    invalidatePattern: async () => 0,
    hashParams: (obj) => {
      const filtered = {};
      const sortedKeys = Object.keys(obj).sort();
      for (const k of sortedKeys) {
        if (obj[k] !== null && obj[k] !== undefined) {
          filtered[k] = obj[k];
        }
      }
      const serialized = JSON.stringify(
        Object.entries(filtered).sort(([a], [b]) => a.localeCompare(b))
      );
      return crypto.createHash("sha256").update(serialized).digest("hex").slice(0, 16);
    },
    _store: store,
    _setCalls: setCalls,
    _getCalls: getCalls,
  };
}

/** Mock DB that returns suggestions data */
function createMockDb(suggestionsData = { entities: [], speakers: [] }) {
  return {
    query: async (text) => {
      if (text.includes("entity_names")) {
        return { rows: suggestionsData.entities };
      }
      if (text.includes("speaker")) {
        return { rows: suggestionsData.speakers };
      }
      return { rows: [] };
    },
  };
}

/** Standard upstream search response (snake_case from Python) */
const UPSTREAM_SEARCH_RESPONSE = {
  results: [
    {
      chunk_id: 1,
      chunk_text: "Education budget debate results",
      relevance_score: 0.95,
      transcript_id: 10,
      speaker: "Hon. Ama Mensah",
      start_s: 100,
      end_s: 150,
      matched_entities: ["budget"],
      record_title: "Budget Debate 2025",
      sitting_title: "First Sitting",
      date: "2025-01-15",
      sitting_id: 5,
      record_id: 3,
    },
  ],
  total_matched: 1,
  latency_ms: 45,
};

/** Expected camelCase mapped response from gateway */
const EXPECTED_SEARCH_RESULT = {
  results: [
    {
      chunkId: 1,
      chunkText: "Education budget debate results",
      relevanceScore: 0.95,
      transcriptId: 10,
      speaker: "Hon. Ama Mensah",
      startS: 100,
      endS: 150,
      matchedEntities: ["budget"],
      recordTitle: "Budget Debate 2025",
      sittingTitle: "First Sitting",
      date: "2025-01-15",
      sittingId: 5,
      recordId: 3,
    },
  ],
  totalMatched: 1,
  latencyMs: 45,
};

let originalFetch;

beforeEach(() => {
  originalFetch = global.fetch;
});

afterEach(() => {
  global.fetch = originalFetch;
});

// --- Search Results Caching Tests ---

describe("POST /api/search — cache behavior", () => {
  it("returns cached response on cache hit without calling upstream", async () => {
    const cache = createMockCache();
    const db = createMockDb();
    let fetchCalled = false;

    // Pre-populate cache with the expected result
    const hash = cache.hashParams({ query: "budget", limit: 10 });
    const cacheKey = cache.key("search", hash);
    cache._store.set(cacheKey, EXPECTED_SEARCH_RESULT);

    global.fetch = () => {
      fetchCalled = true;
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(UPSTREAM_SEARCH_RESPONSE),
      });
    };

    const app = express();
    app.use(searchRoutes(passThrough, db, cache));

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, EXPECTED_SEARCH_RESULT);
    assert.equal(fetchCalled, false, "fetch should NOT be called on cache hit");
  });

  it("proxies to upstream on cache miss and stores result with 300s TTL", async () => {
    const cache = createMockCache();
    const db = createMockDb();

    global.fetch = (url) => {
      if (String(url).includes("/rag/search")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(UPSTREAM_SEARCH_RESPONSE),
        });
      }
      throw new Error(`Unexpected fetch to ${url}`);
    };

    const app = express();
    app.use(searchRoutes(passThrough, db, cache));

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, EXPECTED_SEARCH_RESULT);

    // Verify cache.set was called with 300s TTL
    assert.equal(cache._setCalls.length, 1);
    assert.equal(cache._setCalls[0].ttl, 300);
    assert.deepEqual(cache._setCalls[0].value, EXPECTED_SEARCH_RESULT);
  });

  it("does NOT cache error responses from upstream", async () => {
    const cache = createMockCache();
    const db = createMockDb();

    global.fetch = () => {
      return Promise.resolve({
        ok: false,
        status: 500,
        text: () => Promise.resolve("Internal Server Error"),
      });
    };

    const app = express();
    app.use(searchRoutes(passThrough, db, cache));

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.equal(res.status, 502);
    assert.equal(cache._setCalls.length, 0, "cache.set should NOT be called for error responses");
  });

  it("does NOT cache when upstream returns non-2xx (e.g. 422)", async () => {
    const cache = createMockCache();
    const db = createMockDb();

    global.fetch = () => {
      return Promise.resolve({
        ok: false,
        status: 422,
        text: () => Promise.resolve("Validation Error"),
      });
    };

    const app = express();
    app.use(searchRoutes(passThrough, db, cache));

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.equal(res.status, 422);
    assert.equal(cache._setCalls.length, 0, "cache.set should NOT be called for non-2xx responses");
  });

  it("works normally without caching when cache is null (graceful degradation)", async () => {
    const db = createMockDb();

    global.fetch = (url) => {
      if (String(url).includes("/rag/search")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(UPSTREAM_SEARCH_RESPONSE),
        });
      }
      throw new Error(`Unexpected fetch to ${url}`);
    };

    const app = express();
    // Pass no cache argument (undefined) to simulate disabled Redis
    app.use(searchRoutes(passThrough, db));

    const res = await request(app).post("/api/search").send({ query: "budget" });

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, EXPECTED_SEARCH_RESULT);
  });
});

// --- Suggestions Caching Tests ---

describe("GET /api/search/suggestions — cache behavior", () => {
  it("returns cached suggestions on cache hit without querying DB", async () => {
    const cache = createMockCache();
    let dbQueryCalled = false;
    const db = {
      query: async () => {
        dbQueryCalled = true;
        return { rows: [] };
      },
    };

    const cachedSuggestions = {
      entities: [{ name: "Education", kind: "entity" }],
      speakers: ["Hon. Ama Mensah"],
    };

    // Pre-populate suggestions cache
    const suggestionsKey = cache.key("suggestions", "current");
    cache._store.set(suggestionsKey, cachedSuggestions);

    const app = express();
    app.use(searchRoutes(passThrough, db, cache));

    const res = await request(app).get("/api/search/suggestions");

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, cachedSuggestions);
    assert.equal(dbQueryCalled, false, "DB should NOT be queried on cache hit");
  });

  it("queries DB on cache miss and stores result with 3600s TTL", async () => {
    const cache = createMockCache();
    const db = createMockDb({
      entities: [{ name: "Budget" }, { name: "Education" }],
      speakers: [{ speaker: "Hon. Ama Mensah" }],
    });

    const app = express();
    app.use(searchRoutes(passThrough, db, cache));

    const res = await request(app).get("/api/search/suggestions");

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, {
      entities: [
        { name: "Budget", kind: "entity" },
        { name: "Education", kind: "entity" },
      ],
      speakers: ["Hon. Ama Mensah"],
    });

    // Verify cache.set was called with 3600s TTL
    assert.equal(cache._setCalls.length, 1);
    assert.equal(cache._setCalls[0].ttl, 3600);
    assert.equal(cache._setCalls[0].key, "parliament:suggestions:current");
  });

  it("queries DB directly when cache is null (graceful degradation)", async () => {
    let dbQueryCount = 0;
    const db = {
      query: async (text) => {
        dbQueryCount++;
        if (text.includes("entity_names")) return { rows: [{ name: "Health" }] };
        if (text.includes("speaker")) return { rows: [{ speaker: "Mr. Speaker" }] };
        return { rows: [] };
      },
    };

    const app = express();
    // No cache argument — graceful degradation
    app.use(searchRoutes(passThrough, db));

    const res = await request(app).get("/api/search/suggestions");

    assert.equal(res.status, 200);
    assert.deepEqual(res.body, {
      entities: [{ name: "Health", kind: "entity" }],
      speakers: ["Mr. Speaker"],
    });
    assert.equal(dbQueryCount, 2, "DB should be queried when cache is absent");
  });
});
