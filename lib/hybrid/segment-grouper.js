'use strict';

/**
 * @typedef {import('./deepgram-words').Word} Word
 * @typedef {import('./confidence-detector').ClassifiedWord} ClassifiedWord
 */

/**
 * @typedef {Object} LowConfidenceSegment
 * @property {number} start           Start of earliest Word (seconds).
 * @property {number} end             End of latest Word (seconds).
 * @property {[number, number]} wordIndexRange  Inclusive [firstIndex, lastIndex].
 * @property {Word[]} words           The grouped low-confidence Words.
 */

/**
 * Groups contiguous low-confidence Words into segments. Two adjacent
 * low-confidence Words join the same segment when the time gap between the
 * earlier word's end and the later word's start is <= gapTolerance. A gap
 * greater than gapTolerance, or an intervening high-confidence word, starts a
 * new segment.
 *
 * @param {ClassifiedWord[]} classified
 * @param {number} gapTolerance  seconds (>= 0)
 * @returns {LowConfidenceSegment[]}  Ordered by ascending start.
 */
function groupLowConfidence(classified, gapTolerance) {
  const segments = [];
  let current = null;

  for (const item of classified) {
    if (!item.isLow) {
      // High-confidence word closes any open segment
      if (current) {
        segments.push(current);
        current = null;
      }
      continue;
    }

    // Low-confidence word
    if (!current) {
      // Start a new segment
      current = {
        start: item.word.start,
        end: item.word.end,
        wordIndexRange: [item.index, item.index],
        words: [item.word],
      };
    } else {
      // Check gap from previous low-confidence word's end to this word's start
      const gap = item.word.start - current.end;
      if (gap <= gapTolerance) {
        // Append to current segment
        current.end = item.word.end;
        current.wordIndexRange[1] = item.index;
        current.words.push(item.word);
      } else {
        // Gap too large: close current, start new
        segments.push(current);
        current = {
          start: item.word.start,
          end: item.word.end,
          wordIndexRange: [item.index, item.index],
          words: [item.word],
        };
      }
    }
  }

  // Close any open segment at end of list
  if (current) {
    segments.push(current);
  }

  return segments;
}

/**
 * @typedef {Object} SegmentBundle
 * @property {number} originalStart   Segment start before padding (seconds).
 * @property {number} originalEnd     Segment end before padding (seconds).
 * @property {number} paddedStart     max(0, originalStart - padding).
 * @property {number} paddedEnd       min(duration, originalEnd + padding).
 * @property {[number, number]} wordIndexRange  Inclusive replaced range.
 * @property {Word[]} words           The original low-confidence Words.
 */

/**
 * Wraps each segment with padded, clamped slice boundaries and the
 * reinsertion metadata needed by the reassembler.
 *
 * @param {LowConfidenceSegment[]} segments
 * @param {number} duration  Total audio duration (seconds).
 * @param {number} padding   Padding (seconds, >= 0).
 * @returns {SegmentBundle[]}
 */
function buildBundles(segments, duration, padding) {
  return segments.map((seg) => ({
    originalStart: seg.start,
    originalEnd: seg.end,
    paddedStart: Math.max(0, seg.start - padding),
    paddedEnd: Math.min(duration, seg.end + padding),
    wordIndexRange: seg.wordIndexRange,
    words: seg.words,
  }));
}

module.exports = { groupLowConfidence, buildBundles };
