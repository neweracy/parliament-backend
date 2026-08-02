"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fc = require("fast-check");
const { mapAskResponse } = require("../../routes/ask.js");

// Generator for an arbitrary raw response from the Python service
const rawRelatedRecord = fc.record({
  transcript_id: fc.integer(),
  label: fc.oneof(fc.string(), fc.constant(null), fc.constant(undefined)),
  chunk_id: fc.integer(),
  speaker: fc.oneof(fc.string(), fc.constant(null)),
  sitting_title: fc.oneof(fc.string(), fc.constant(null)),
  record_title: fc.oneof(fc.string(), fc.constant(null)),
  date: fc.oneof(fc.string(), fc.constant(null)),
  start_s: fc.oneof(fc.float(), fc.constant(null)),
  sitting_id: fc.oneof(fc.integer(), fc.constant(null)),
  record_id: fc.oneof(fc.integer(), fc.constant(null)),
}, { requiredKeys: [] });

const rawResponse = fc.record({
  answer: fc.oneof(fc.string(), fc.constant(""), fc.constant(null), fc.constant(undefined)),
  citations: fc.oneof(fc.array(fc.record({
    transcript_id: fc.integer(),
    chunk_id: fc.integer(),
    speaker: fc.oneof(fc.string(), fc.constant(null)),
    start_s: fc.oneof(fc.float(), fc.constant(null)),
    end_s: fc.oneof(fc.float(), fc.constant(null)),
    excerpt: fc.oneof(fc.string(), fc.constant("")),
    sitting_id: fc.oneof(fc.integer(), fc.constant(null)),
    record_id: fc.oneof(fc.integer(), fc.constant(null)),
  }, { requiredKeys: [] })), fc.constant(null), fc.constant(undefined)),
  source_chunks: fc.oneof(fc.array(fc.anything()), fc.constant(null), fc.constant(undefined)),
  recommendations: fc.oneof(fc.array(fc.record({
    text: fc.oneof(fc.string(), fc.constant(null)),
    reason: fc.oneof(fc.string(), fc.constant(null)),
  }, { requiredKeys: [] })), fc.constant(null), fc.constant(undefined)),
  related_records: fc.oneof(fc.array(rawRelatedRecord), fc.constant(null), fc.constant(undefined)),
  latency_ms: fc.oneof(fc.float(), fc.constant(null), fc.constant(undefined)),
  latencyMs: fc.oneof(fc.float(), fc.constant(null), fc.constant(undefined)),
}, { requiredKeys: [] });

describe("mapAskResponse — property tests", () => {
  it("Property 12: mapping is total and collections default to empty", () => {
    fc.assert(
      fc.property(rawResponse, (raw) => {
        const result = mapAskResponse(raw);

        // Top-level fields are always present and correct type
        assert.equal(typeof result.answer, "string");
        assert.ok(Array.isArray(result.citations));
        assert.ok(Array.isArray(result.sourceChunks));
        assert.ok(Array.isArray(result.recommendations));
        assert.ok(Array.isArray(result.relatedRecords));
        assert.equal(typeof result.latencyMs, "number");
        assert.ok(!Number.isNaN(result.latencyMs));

        // No top-level field is undefined
        for (const key of Object.keys(result)) {
          assert.notEqual(result[key], undefined, `${key} should not be undefined`);
        }

        // Every relatedRecord has required keys
        for (const rec of result.relatedRecords) {
          assert.equal(typeof rec.transcriptId, "number");
          assert.equal(typeof rec.label, "string");
          assert.equal(typeof rec.chunkId, "number");
          // Optional fields are null or correct type
          assert.ok(rec.speaker === null || typeof rec.speaker === "string");
          assert.ok(rec.sittingTitle === null || typeof rec.sittingTitle === "string");
          assert.ok(rec.recordTitle === null || typeof rec.recordTitle === "string");
          assert.ok(rec.date === null || typeof rec.date === "string");
          assert.ok(rec.startS === null || typeof rec.startS === "number");
          // Navigation ids are either absent (null) or a real id — never
          // undefined, since the frontend keys "not navigable" off null.
          assert.ok(rec.sittingId === null || typeof rec.sittingId === "number");
          assert.ok(rec.recordId === null || typeof rec.recordId === "number");
        }

        // Citations carry the same navigation contract
        for (const citation of result.citations) {
          assert.ok(citation.sittingId === null || typeof citation.sittingId === "number");
          assert.ok(citation.recordId === null || typeof citation.recordId === "number");
        }
      }),
      { numRuns: 200 }
    );
  });
});
