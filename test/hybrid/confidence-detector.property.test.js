'use strict';

// Feature: hybrid-confidence-transcription, Property 1:
// Every word is classified exactly once

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { classifyWords } = require('../../lib/hybrid/confidence-detector');

/**
 * Generates an arbitrary Word list with monotonic non-overlapping
 * start/end pairs and confidence in [0,1].
 */
const wordArb = fc.array(
  fc.record({
    word: fc.string({ minLength: 1, maxLength: 20 }),
    start: fc.double({ min: 0, max: 1000, noNaN: true }),
    end: fc.double({ min: 0, max: 1000, noNaN: true }),
    confidence: fc.double({ min: 0, max: 1, noNaN: true }),
  }),
  { minLength: 0, maxLength: 50 }
).map((words) => {
  // Ensure monotonic non-overlapping start/end pairs
  let cursor = 0;
  return words.map((w) => {
    const start = cursor + Math.abs(w.start % 5);
    const end = start + 0.1 + Math.abs(w.end % 3);
    cursor = end;
    return { word: w.word, start, end, confidence: w.confidence };
  });
});

const thresholdArb = fc.double({ min: 0, max: 1, noNaN: true });

/**
 * Validates: Requirements 3.1, 3.4
 */
describe('Property 1: Every word is classified exactly once', () => {
  it('output length and order match input, and each isLow === (confidence < threshold)', () => {
    fc.assert(
      fc.property(wordArb, thresholdArb, (words, threshold) => {
        const result = classifyWords(words, threshold);

        // Same length as input
        assert.equal(result.length, words.length,
          `Expected ${words.length} classified words, got ${result.length}`);

        // Each word classified exactly once, in order, with correct isLow
        for (let i = 0; i < words.length; i++) {
          const classified = result[i];

          // Same order: index matches position
          assert.equal(classified.index, i,
            `Expected index ${i}, got ${classified.index}`);

          // Word reference preserved
          assert.equal(classified.word, words[i],
            `Word at index ${i} does not match input`);

          // isLow classification is correct (strict less-than)
          const expectedIsLow = words[i].confidence < threshold;
          assert.equal(classified.isLow, expectedIsLow,
            `Word at index ${i}: confidence=${words[i].confidence}, threshold=${threshold}, ` +
            `expected isLow=${expectedIsLow}, got ${classified.isLow}`);
        }
      }),
      { numRuns: 100 }
    );
  });
});
