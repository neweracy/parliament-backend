'use strict';

// Feature: hybrid-confidence-transcription, Property 3:
// Grouping partitions the low-confidence words
// Feature: hybrid-confidence-transcription, Property 4:
// Gap tolerance governs segment boundaries
// Feature: hybrid-confidence-transcription, Property 5:
// Segment timestamps span their words
// Feature: hybrid-confidence-transcription, Property 6:
// Padded slice boundaries stay within the audio

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { groupLowConfidence, buildBundles } = require('../../lib/hybrid/segment-grouper');

/**
 * Generates an arbitrary classified word list with monotonic non-overlapping
 * start/end pairs, confidence in [0,1], and a random isLow classification.
 */
const classifiedArb = fc.array(
  fc.record({
    word: fc.string({ minLength: 1, maxLength: 10 }),
    start: fc.double({ min: 0, max: 100, noNaN: true }),
    end: fc.double({ min: 0, max: 100, noNaN: true }),
    confidence: fc.double({ min: 0, max: 1, noNaN: true }),
    isLow: fc.boolean(),
  }),
  { minLength: 0, maxLength: 30 }
).map((items) => {
  let cursor = 0;
  return items.map((item, index) => {
    const start = cursor + Math.abs(item.start % 3);
    const end = start + 0.1 + Math.abs(item.end % 2);
    cursor = end;
    return {
      word: { word: item.word, start, end, confidence: item.confidence },
      index,
      isLow: item.isLow,
    };
  });
});

const gapToleranceArb = fc.double({ min: 0, max: 5, noNaN: true });

/**
 * Validates: Requirements 4.1, 4.5
 */
describe('Property 3: Grouping partitions the low-confidence words', () => {
  it('concatenated segment words equal exactly the low-confidence words in original order', () => {
    fc.assert(
      fc.property(classifiedArb, gapToleranceArb, (classified, gapTolerance) => {
        const segments = groupLowConfidence(classified, gapTolerance);

        // Collect all words from all segments in order
        const segmentWords = segments.flatMap((seg) => seg.words);

        // Collect all low-confidence words from the classified input in order
        const lowWords = classified
          .filter((c) => c.isLow)
          .map((c) => c.word);

        // They must be exactly equal (same length, same order, same references)
        assert.equal(segmentWords.length, lowWords.length,
          `Segment words count ${segmentWords.length} !== low-confidence words count ${lowWords.length}`);

        for (let i = 0; i < lowWords.length; i++) {
          assert.equal(segmentWords[i], lowWords[i],
            `Word mismatch at position ${i}`);
        }

        // No high-confidence word appears in any segment
        const highWords = classified
          .filter((c) => !c.isLow)
          .map((c) => c.word);

        for (const seg of segments) {
          for (const w of seg.words) {
            assert.ok(!highWords.includes(w),
              `High-confidence word "${w.word}" found in segment`);
          }
        }
      }),
      { numRuns: 100 }
    );
  });
});

/**
 * Generates exactly two adjacent low-confidence words with no words between them,
 * plus a variable gap between them to test gap tolerance boundaries.
 * Uses pre-computed timestamps to avoid floating-point absorption issues.
 */
const twoAdjacentLowArb = fc.record({
  word1Text: fc.string({ minLength: 1, maxLength: 10 }),
  word2Text: fc.string({ minLength: 1, maxLength: 10 }),
  word1Start: fc.double({ min: 1, max: 50, noNaN: true }),
  word1End: fc.double({ min: 1.1, max: 52, noNaN: true }),
  word2Start: fc.double({ min: 1.2, max: 55, noNaN: true }),
  word2End: fc.double({ min: 1.3, max: 57, noNaN: true }),
  confidence1: fc.double({ min: 0, max: 1, noNaN: true }),
  confidence2: fc.double({ min: 0, max: 1, noNaN: true }),
  gapTolerance: fc.double({ min: 0, max: 5, noNaN: true }),
}).filter((p) => p.word1End > p.word1Start && p.word2Start >= p.word1End && p.word2End > p.word2Start);

/**
 * Validates: Requirements 4.1, 4.2
 */
describe('Property 4: Gap tolerance governs segment boundaries', () => {
  it('two adjacent low-confidence words share a segment iff gap <= gapTolerance', () => {
    fc.assert(
      fc.property(twoAdjacentLowArb, (params) => {
        const {
          word1Text, word2Text,
          word1Start, word1End, word2Start, word2End,
          confidence1, confidence2, gapTolerance,
        } = params;

        const classified = [
          {
            word: { word: word1Text, start: word1Start, end: word1End, confidence: confidence1 },
            index: 0,
            isLow: true,
          },
          {
            word: { word: word2Text, start: word2Start, end: word2End, confidence: confidence2 },
            index: 1,
            isLow: true,
          },
        ];

        const segments = groupLowConfidence(classified, gapTolerance);

        // The actual gap as computed by the function
        const computedGap = word2Start - word1End;

        if (computedGap <= gapTolerance) {
          // They should share a single segment
          assert.equal(segments.length, 1,
            `Expected 1 segment for gap=${computedGap} <= tolerance=${gapTolerance}, got ${segments.length}`);
          assert.equal(segments[0].words.length, 2,
            'Expected both words in the same segment');
        } else {
          // They should be in separate segments
          assert.equal(segments.length, 2,
            `Expected 2 segments for gap=${computedGap} > tolerance=${gapTolerance}, got ${segments.length}`);
          assert.equal(segments[0].words.length, 1);
          assert.equal(segments[1].words.length, 1);
        }
      }),
      { numRuns: 100 }
    );
  });
});

/**
 * Validates: Requirement 4.3
 */
describe('Property 5: Segment timestamps span their words', () => {
  it('each segment start equals min word start and end equals max word end', () => {
    fc.assert(
      fc.property(classifiedArb, gapToleranceArb, (classified, gapTolerance) => {
        const segments = groupLowConfidence(classified, gapTolerance);

        for (const seg of segments) {
          assert.ok(seg.words.length > 0, 'Segment must have at least one word');

          const minStart = Math.min(...seg.words.map((w) => w.start));
          const maxEnd = Math.max(...seg.words.map((w) => w.end));

          assert.equal(seg.start, minStart,
            `Segment start ${seg.start} !== min word start ${minStart}`);
          assert.equal(seg.end, maxEnd,
            `Segment end ${seg.end} !== max word end ${maxEnd}`);
        }
      }),
      { numRuns: 100 }
    );
  });
});

/**
 * Generator for segments to test buildBundles.
 */
const segmentsArb = fc.array(
  fc.record({
    start: fc.double({ min: 0, max: 50, noNaN: true }),
    duration: fc.double({ min: 0.1, max: 5, noNaN: true }),
    wordCount: fc.integer({ min: 1, max: 5 }),
  }),
  { minLength: 1, maxLength: 10 }
).map((items) => {
  let cursor = 0;
  return items.map((item) => {
    const start = cursor + Math.abs(item.start % 3);
    const end = start + item.duration;
    cursor = end + 0.5;
    const words = Array.from({ length: item.wordCount }, (_, i) => ({
      word: `w${i}`,
      start: start + (i * item.duration / item.wordCount),
      end: start + ((i + 1) * item.duration / item.wordCount),
      confidence: 0.5,
    }));
    return {
      start,
      end,
      wordIndexRange: [0, item.wordCount - 1],
      words,
    };
  });
});

const durationArb = fc.double({ min: 10, max: 200, noNaN: true });
const paddingArb = fc.double({ min: 0, max: 5, noNaN: true });

/**
 * Validates: Requirements 5.1, 5.2, 5.3
 */
describe('Property 6: Padded slice boundaries stay within the audio', () => {
  it('0 <= paddedStart <= originalStart and originalEnd <= paddedEnd <= duration', () => {
    fc.assert(
      fc.property(segmentsArb, durationArb, paddingArb, (segments, duration, padding) => {
        // Ensure duration is at least as large as the last segment end
        const maxEnd = Math.max(...segments.map((s) => s.end));
        const effectiveDuration = Math.max(duration, maxEnd + 1);

        const bundles = buildBundles(segments, effectiveDuration, padding);

        assert.equal(bundles.length, segments.length,
          'Bundle count must match segment count');

        for (let i = 0; i < bundles.length; i++) {
          const bundle = bundles[i];

          // paddedStart >= 0
          assert.ok(bundle.paddedStart >= 0,
            `Bundle ${i}: paddedStart ${bundle.paddedStart} < 0`);

          // paddedStart <= originalStart
          assert.ok(bundle.paddedStart <= bundle.originalStart,
            `Bundle ${i}: paddedStart ${bundle.paddedStart} > originalStart ${bundle.originalStart}`);

          // originalEnd <= paddedEnd
          assert.ok(bundle.originalEnd <= bundle.paddedEnd,
            `Bundle ${i}: originalEnd ${bundle.originalEnd} > paddedEnd ${bundle.paddedEnd}`);

          // paddedEnd <= duration
          assert.ok(bundle.paddedEnd <= effectiveDuration,
            `Bundle ${i}: paddedEnd ${bundle.paddedEnd} > duration ${effectiveDuration}`);
        }
      }),
      { numRuns: 100 }
    );
  });
});
