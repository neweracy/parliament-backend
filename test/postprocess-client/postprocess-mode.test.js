const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const {
  degradedResponse,
  mergeSuccess,
  logDegraded,
} = require("../../lib/postprocess-mode");

// ---------------------------------------------------------------------------
// degradedResponse
// ---------------------------------------------------------------------------

describe("degradedResponse", () => {
  const rawTranscript = "the honorable bb kabo";
  const rawWords = [
    { word: "the", start: 0.08, end: 0.24, confidence: 0.99 },
    { word: "honorable", start: 0.24, end: 0.71, confidence: 0.91 },
    { word: "bb", start: 0.71, end: 0.90, confidence: 0.62 },
    { word: "kabo", start: 0.90, end: 1.20, confidence: 0.55 },
  ];
  const meta = { model_uuid: "uuid-1", request_id: "req-1", model_name: "nova-3" };

  it("returns the raw transcript unchanged", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal(res.transcript, rawTranscript);
  });

  it("returns shallow-copied words", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal(res.words.length, rawWords.length);
    assert.deepEqual(res.words[0], rawWords[0]);
    // Verify it's a copy, not the same reference
    res.words[0].word = "mutated";
    assert.equal(rawWords[0].word, "the");
  });

  it("returns an empty entities array", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.deepEqual(res.entities, []);
  });

  it("sets metadata._version to v6-python", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal(res.metadata._version, "v6-python");
  });

  it("sets metadata.postprocessing_status to 'skipped'", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal(res.metadata.postprocessing_status, "skipped");
  });

  it("sets metadata.postprocessing_status to 'disabled'", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "disabled");
    assert.equal(res.metadata.postprocessing_status, "disabled");
  });

  it("preserves Gateway meta fields (model_uuid, request_id, model_name)", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal(res.metadata.model_uuid, "uuid-1");
    assert.equal(res.metadata.request_id, "req-1");
    assert.equal(res.metadata.model_name, "nova-3");
  });

  it("includes raw with copied words", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal(res.raw.transcript, rawTranscript);
    assert.equal(res.raw.words.length, rawWords.length);
    // raw.words is also a copy
    res.raw.words[0].word = "mutated";
    assert.equal(rawWords[0].word, "the");
  });

  it("includes duration at top level when present", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, 12.5, "skipped");
    assert.equal(res.duration, 12.5);
  });

  it("omits duration when undefined", () => {
    const res = degradedResponse(rawTranscript, rawWords, meta, undefined, "skipped");
    assert.equal("duration" in res, false);
  });
});

// ---------------------------------------------------------------------------
// mergeSuccess
// ---------------------------------------------------------------------------

describe("mergeSuccess", () => {
  const rawTranscript = "the honorable bb kabo";
  const rawWords = [
    { word: "the", start: 0.08, end: 0.24, confidence: 0.99 },
    { word: "honorable", start: 0.24, end: 0.71, confidence: 0.91 },
  ];
  const meta = { model_uuid: "uuid-1", request_id: "req-1", model_name: "nova-3" };

  const serviceData = {
    transcript: "the Honorable B.B. Carboo",
    words: [
      { word: "the", start: 0.08, end: 0.24, confidence: 0.99 },
      { word: "Honorable", start: 0.24, end: 0.71, confidence: 0.91 },
      { word: "B.B. Carboo", start: 0.71, end: 1.34, confidence: 0.62, locationCorrected: true },
    ],
    entities: [{ name: "B.B. Carboo", kind: "person", type: "mp", mentions: 1 }],
    metadata: {
      location_corrections: 1,
      llm_status: "ok",
      dataset_version: "2026-07-09T10:12:03Z",
      correlationId: "corr-123",
    },
    corrections: [{ original: "bb kabo", corrected: "B.B. Carboo" }],
  };

  it("passes through service transcript, words, and entities", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.equal(res.transcript, serviceData.transcript);
    assert.deepEqual(res.words, serviceData.words);
    assert.deepEqual(res.entities, serviceData.entities);
  });

  it("passes through corrections array", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.deepEqual(res.corrections, serviceData.corrections);
  });

  it("lets Gateway meta win for model_uuid, request_id, model_name", () => {
    const conflicting = {
      ...serviceData,
      metadata: { ...serviceData.metadata, model_uuid: "wrong", request_id: "wrong", model_name: "wrong" },
    };
    const res = mergeSuccess(conflicting, rawTranscript, rawWords, meta, undefined);
    assert.equal(res.metadata.model_uuid, "uuid-1");
    assert.equal(res.metadata.request_id, "req-1");
    assert.equal(res.metadata.model_name, "nova-3");
  });

  it("passes through service counters and llm_status", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.equal(res.metadata.location_corrections, 1);
    assert.equal(res.metadata.llm_status, "ok");
    assert.equal(res.metadata.dataset_version, "2026-07-09T10:12:03Z");
    assert.equal(res.metadata.correlationId, "corr-123");
  });

  it("sets _version to v6-python", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.equal(res.metadata._version, "v6-python");
  });

  it("does not add zero counters that are absent from service data", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.equal("year_corrections" in res.metadata, false);
    assert.equal("bedrock_corrections" in res.metadata, false);
  });

  it("constructs raw from Gateway data", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.equal(res.raw.transcript, rawTranscript);
    assert.deepEqual(res.raw.words, rawWords);
  });

  it("includes duration at top level when present", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, 15.3);
    assert.equal(res.duration, 15.3);
  });

  it("omits duration when undefined", () => {
    const res = mergeSuccess(serviceData, rawTranscript, rawWords, meta, undefined);
    assert.equal("duration" in res, false);
  });
});

// ---------------------------------------------------------------------------
// logDegraded
// ---------------------------------------------------------------------------

describe("logDegraded", () => {
  it("calls console.error with a JSON object containing reason, elapsedMs, correlationId", (t) => {
    const logged = [];
    t.mock.method(console, "error", (msg) => logged.push(msg));

    logDegraded({ reason: "timeout", elapsedMs: 21000 }, "corr-abc");

    assert.equal(logged.length, 1);
    const record = JSON.parse(logged[0]);
    assert.equal(record.level, "error");
    assert.equal(record.event, "postprocess.degraded");
    assert.equal(record.reason, "timeout");
    assert.equal(record.elapsedMs, 21000);
    assert.equal(record.correlationId, "corr-abc");
  });
});
