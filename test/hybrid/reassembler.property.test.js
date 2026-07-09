'use strict';

// Feature: hybrid-confidence-transcription, Property 9:
// Reassembly preserves high-confidence words and order

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { reassemble } = require('../../lib/hybrid/reassembler');

/**
 * Generates an arbitrary Word list with monotonic non-overlapping start/end
 * pairs, confidence in [0,1], and word text.
 */
const wordsArb = (minLength = 1, maxLength = 20) =>
  fc.array(
    fc.record({
      word: fc.string({ minLength: 1, maxLength: 10 }),
      gap: fc.double({ min: 0, max: 2, noNaN: true }),
      duration: fc.double({ min: 0.1, max: 2, noNaN: true }),
      confidence: fc.double({ min: 0, max: 1, noNaN: true }),
    }),
    { minLength, maxLength }
  ).map((items) => {
    let cursor = 0;
    return items.map((item) => {
      const start = cursor + item.gap;
      const end = start + item.duration;
      cursor = end;
      return {
        word: item.word,
        start,
        end,
        confidence: item.confidence,
      };
    });
  });

/**
 * Given a word list length, generates non-overlapping corrections (index ranges
 * that don't overlap each other). Each correction has valid start/end timestamps
 * and a language/text.
 */
const nonOverlappingCorrectionsArb = (words) => {
  if (words.length === 0) return fc.constant([]);

  // Generate a sorted subset of indices to use as range boundaries
  return fc.array(
    fc.record({
      rangeStart: fc.integer({ min: 0, max: words.length - 1 }),
      rangeLen: fc.integer({ min: 1, max: 3 }),
      language: fc.constantFrom('tw', 'ee', 'gaa'),
      text: fc.string({ minLength: 1, maxLength: 20 }),
    }),
    { minLength: 0, maxLength: Math.min(5, Math.floor(words.length / 2)) }
  ).map((rawCorrections) => {
    // Build non-overlapping corrections by greedily assigning ranges
    const corrections = [];
    const usedIndices = new Set();

    for (const raw of rawCorrections) {
      const first = raw.rangeStart;
      const last = Math.min(first + raw.rangeLen - 1, words.length - 1);

      // Check no overlap with already-used indices
      let overlaps = false;
      for (let i = first; i <= last; i++) {
        if (usedIndices.has(i)) {
          overlaps = true;
          break;
        }
      }
      if (overlaps) continue;

      // Mark indices as used
      for (let i = first; i <= last; i++) {
        usedIndices.add(i);
      }

      corrections.push({
        wordIndexRange: [first, last],
        start: words[first].start,
        end: words[last].end,
        language: raw.language,
        text: raw.text,
      });
    }

    return corrections;
  });
};

/**
 * Validates: Requirements 9.1, 9.3
 */
describe('Property 9: Reassembly preserves high-confidence words and order', () => {
  it('every index outside all correction ranges appears once, unchanged, in original relative order', () => {
    fc.assert(
      fc.property(
        wordsArb(1, 20).chain((words) =>
          nonOverlappingCorrectionsArb(words).map((corrections) => ({ words, corrections }))
        ),
        ({ words, corrections }) => {
          const result = reassemble(words, corrections);

          // Determine covered indices
          const coveredIndices = new Set();
          for (const correction of corrections) {
            const [first, last] = correction.wordIndexRange;
            for (let i = first; i <= last; i++) {
              coveredIndices.add(i);
            }
          }

          // Collect uncovered word indices in original order
          const uncoveredWords = [];
          for (let i = 0; i < words.length; i++) {
            if (!coveredIndices.has(i)) {
              uncoveredWords.push(words[i]);
            }
          }

          // Collect uncorrected segments from result (those with corrected === false)
          const preservedSegments = result.filter((seg) => seg.corrected === false);

          // Each uncovered word must appear exactly once
          assert.equal(
            preservedSegments.length,
            uncoveredWords.length,
            `Expected ${uncoveredWords.length} preserved segments, got ${preservedSegments.length}`
          );

          // Each preserved segment must match the corresponding uncovered word
          // (text, start, end, confidence) and maintain original relative order
          for (let i = 0; i < uncoveredWords.length; i++) {
            const original = uncoveredWords[i];
            const segment = preservedSegments[i];

            assert.equal(segment.text, original.word,
              `Word text mismatch at position ${i}: expected "${original.word}", got "${segment.text}"`);
            assert.equal(segment.start, original.start,
              `Start mismatch at position ${i}: expected ${original.start}, got ${segment.start}`);
            assert.equal(segment.end, original.end,
              `End mismatch at position ${i}: expected ${original.end}, got ${segment.end}`);
            assert.equal(segment.confidence, original.confidence,
              `Confidence mismatch at position ${i}: expected ${original.confidence}, got ${segment.confidence}`);
          }

          // Verify relative order is preserved: preserved segments should appear
          // in the same relative order as the original words (by start timestamp)
          for (let i = 1; i < preservedSegments.length; i++) {
            assert.ok(
              preservedSegments[i].start >= preservedSegments[i - 1].start,
              `Order violated: segment at index ${i} has start ${preservedSegments[i].start} < previous ${preservedSegments[i - 1].start}`
            );
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});


// Feature: hybrid-confidence-transcription, Property 10:
// Corrected segments keep original timestamps and language

/**
 * Validates: Requirements 9.2, 9.5
 */
describe('Property 10: Corrected segments keep original timestamps and language', () => {
  it('each corrected segment start/end equals the original correction timestamps and language equals the chosen candidate', () => {
    fc.assert(
      fc.property(
        wordsArb(1, 20).chain((words) =>
          nonOverlappingCorrectionsArb(words).map((corrections) => ({ words, corrections }))
        ),
        ({ words, corrections }) => {
          const result = reassemble(words, corrections);

          // Collect corrected segments from result
          const correctedSegments = result.filter((seg) => seg.corrected === true);

          // There should be exactly one corrected segment per correction
          assert.equal(
            correctedSegments.length,
            corrections.length,
            `Expected ${corrections.length} corrected segments, got ${correctedSegments.length}`
          );

          // Sort corrections by start to match the output order (reassemble sorts by ascending start)
          const sortedCorrections = [...corrections].sort((a, b) => a.start - b.start);

          // Each corrected segment must have start/end matching the original correction
          // and language matching the chosen candidate
          for (let i = 0; i < sortedCorrections.length; i++) {
            const correction = sortedCorrections[i];
            const segment = correctedSegments[i];

            assert.equal(segment.start, correction.start,
              `Corrected segment ${i} start mismatch: expected ${correction.start}, got ${segment.start}`);
            assert.equal(segment.end, correction.end,
              `Corrected segment ${i} end mismatch: expected ${correction.end}, got ${segment.end}`);
            assert.equal(segment.language, correction.language,
              `Corrected segment ${i} language mismatch: expected "${correction.language}", got "${segment.language}"`);
            assert.equal(segment.corrected, true,
              `Corrected segment ${i} should have corrected === true`);
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});


// Feature: hybrid-confidence-transcription, Property 11:
// Unified transcript is ordered by start

/**
 * Validates: Requirements 9.4
 */
describe('Property 11: Unified transcript is ordered by start', () => {
  it('reassembled segments are ordered by non-decreasing start', () => {
    fc.assert(
      fc.property(
        wordsArb(1, 20).chain((words) =>
          nonOverlappingCorrectionsArb(words).map((corrections) => ({ words, corrections }))
        ),
        ({ words, corrections }) => {
          const result = reassemble(words, corrections);

          // Assert segments are ordered by non-decreasing start
          for (let i = 1; i < result.length; i++) {
            assert.ok(
              result[i].start >= result[i - 1].start,
              `Ordering violated: segment ${i} has start ${result[i].start} < previous segment start ${result[i - 1].start}`
            );
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});
