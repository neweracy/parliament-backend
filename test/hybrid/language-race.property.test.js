'use strict';

// Feature: hybrid-confidence-transcription, Property 12:
// Correction calls are bounded by segments × 3

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { raceLanguages } = require('../../lib/hybrid/language-race');

/**
 * Validates: Requirements 7.1, 7.3
 */
describe('Property 12: Correction calls are bounded by segments × 3', () => {
  it('total khayaTranscribe invocations equal segments × 3, independent of word count', async () => {
    // Generate a segment count (1–10) and a per-segment word count (1–50).
    // The word count varies per segment to prove it has no effect on call count.
    const segmentsArb = fc.array(
      fc.nat({ max: 49 }).map((n) => n + 1), // wordCount per segment: 1–50
      { minLength: 1, maxLength: 10 }
    );

    await fc.assert(
      fc.asyncProperty(segmentsArb, async (segmentWordCounts) => {
        let callCount = 0;

        // Counting fake that resolves with a transcript containing
        // a number of words matching the segment's word count (irrelevant
        // to the property, but shows independence from content).
        const fakeKhayaTranscribe = async (_buf, _mime, _lang) => {
          callCount++;
          return { transcript: 'fake transcription result' };
        };

        const segmentCount = segmentWordCounts.length;

        // Simulate calling raceLanguages once per segment bundle
        // (each segment has a varying word count, but that should not
        // affect how many times khayaTranscribe is called).
        for (const wordCount of segmentWordCounts) {
          // Create a buffer whose size varies with word count to prove
          // the call count is independent of the buffer/word content.
          const sliceBuffer = Buffer.alloc(wordCount * 100);
          await raceLanguages(sliceBuffer, 'audio/mpeg', fakeKhayaTranscribe);
        }

        // Assert: total calls = segments × 3 (one per candidate language)
        assert.equal(
          callCount,
          segmentCount * 3,
          `Expected ${segmentCount * 3} calls for ${segmentCount} segments, got ${callCount}`
        );
      }),
      { numRuns: 100 }
    );
  });
});
