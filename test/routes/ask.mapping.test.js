"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { mapAskResponse } = require("../../routes/ask.js");

describe("mapAskResponse", () => {
  it("maps related_records absent to empty relatedRecords", () => {
    const result = mapAskResponse({ answer: "test" });
    assert.deepStrictEqual(result.relatedRecords, []);
  });

  it("maps related_records null to empty relatedRecords", () => {
    const result = mapAskResponse({ answer: "test", related_records: null });
    assert.deepStrictEqual(result.relatedRecords, []);
  });

  it("maps related_records empty array to empty relatedRecords", () => {
    const result = mapAskResponse({ answer: "test", related_records: [] });
    assert.deepStrictEqual(result.relatedRecords, []);
  });

  it("maps records with start_s: null to startS: null", () => {
    const result = mapAskResponse({
      answer: "test",
      related_records: [
        { transcript_id: "t1", label: "Test", chunk_id: "c1", start_s: null },
      ],
    });
    assert.strictEqual(result.relatedRecords[0].startS, null);
  });

  it("maps records with speaker: null to speaker: null", () => {
    const result = mapAskResponse({
      answer: "test",
      related_records: [
        { transcript_id: "t1", label: "Test", chunk_id: "c1", speaker: null },
      ],
    });
    assert.strictEqual(result.relatedRecords[0].speaker, null);
  });

  it("falls back latency_ms → latencyMs → 0 when body missing latency_ms", () => {
    // No latency_ms and no latencyMs → 0
    const result1 = mapAskResponse({ answer: "test" });
    assert.strictEqual(result1.latencyMs, 0);

    // latencyMs present but no latency_ms → uses latencyMs
    const result2 = mapAskResponse({ answer: "test", latencyMs: 42 });
    assert.strictEqual(result2.latencyMs, 42);

    // latency_ms present → uses latency_ms
    const result3 = mapAskResponse({ answer: "test", latency_ms: 99 });
    assert.strictEqual(result3.latencyMs, 99);
  });

  it("maps a full response with all fields correctly", () => {
    const raw = {
      answer: "The minister stated...",
      latency_ms: 250,
      citations: [
        {
          transcript_id: "t1",
          chunk_id: "c1",
          speaker: "Hon. Doe",
          start_s: 10.5,
          end_s: 20.3,
          excerpt: "We propose...",
        },
      ],
      source_chunks: [
        {
          chunk_id: "c1",
          text: "We propose a new approach.",
          relevance_score: 0.92,
          transcript_id: "t1",
          speaker: "Hon. Doe",
          start_s: 10.5,
          end_s: 20.3,
          matched_entities: ["Ministry of Finance"],
          record_title: "Budget Statement",
          sitting_title: "3rd Sitting",
          date: "2024-03-15",
        },
      ],
      recommendations: [
        { text: "Ask about the budget", reason: "Related topic" },
      ],
      related_records: [
        {
          transcript_id: "t2",
          label: "Previous debate",
          chunk_id: "c2",
          speaker: "Hon. Smith",
          sitting_title: "2nd Sitting",
          record_title: "Opening Statement",
          date: "2024-03-14",
          start_s: 5.0,
        },
      ],
    };

    const result = mapAskResponse(raw);

    assert.strictEqual(result.answer, "The minister stated...");
    assert.strictEqual(result.latencyMs, 250);

    assert.strictEqual(result.citations.length, 1);
    assert.deepStrictEqual(result.citations[0], {
      transcriptId: "t1",
      chunkId: "c1",
      speaker: "Hon. Doe",
      startS: 10.5,
      endS: 20.3,
      excerpt: "We propose...",
    });

    assert.strictEqual(result.sourceChunks.length, 1);
    assert.deepStrictEqual(result.sourceChunks[0], {
      chunkId: "c1",
      text: "We propose a new approach.",
      relevanceScore: 0.92,
      transcriptId: "t1",
      speaker: "Hon. Doe",
      startS: 10.5,
      endS: 20.3,
      matchedEntities: ["Ministry of Finance"],
      recordTitle: "Budget Statement",
      sittingTitle: "3rd Sitting",
      date: "2024-03-15",
    });

    assert.strictEqual(result.recommendations.length, 1);
    assert.deepStrictEqual(result.recommendations[0], {
      text: "Ask about the budget",
      reason: "Related topic",
    });

    assert.strictEqual(result.relatedRecords.length, 1);
    assert.deepStrictEqual(result.relatedRecords[0], {
      transcriptId: "t2",
      label: "Previous debate",
      chunkId: "c2",
      speaker: "Hon. Smith",
      sittingTitle: "2nd Sitting",
      recordTitle: "Opening Statement",
      date: "2024-03-14",
      startS: 5.0,
    });
  });

  it("maps citations absent to empty array", () => {
    const result = mapAskResponse({ answer: "test" });
    assert.deepStrictEqual(result.citations, []);
  });

  it("maps source_chunks absent to empty array", () => {
    const result = mapAskResponse({ answer: "test" });
    assert.deepStrictEqual(result.sourceChunks, []);
  });

  it("maps recommendations absent to empty array", () => {
    const result = mapAskResponse({ answer: "test" });
    assert.deepStrictEqual(result.recommendations, []);
  });
});
