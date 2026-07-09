'use strict';

/**
 * @typedef {Object} Correction
 * @property {[number, number]} wordIndexRange  Inclusive replaced range.
 * @property {number} start        Original segment start timestamp.
 * @property {number} end          Original segment end timestamp.
 * @property {string} language     Chosen Candidate_Language.
 * @property {string} text         Winning corrected text.
 */

/**
 * @typedef {Object} UnifiedSegment
 * @property {string} text
 * @property {number} start
 * @property {number} end
 * @property {boolean} corrected            True for reinserted segments.
 * @property {string} [language]            Chosen Candidate_Language (corrected only).
 * @property {number} [confidence]          Original word confidence (uncorrected words).
 */

/**
 * Produces the ordered Unified_Transcript segments. High-confidence Words are
 * preserved verbatim in original order; each Correction replaces its recorded
 * index range with a single corrected segment carrying the original segment
 * start/end and chosen language. Output is sorted by ascending start.
 *
 * @param {import('./deepgram-words').Word[]} words  Full ordered Primary_Engine word list.
 * @param {Correction[]} corrections
 * @returns {UnifiedSegment[]}
 */
function reassemble(words, corrections) {
  const segments = [];

  // Build a set of indices covered by corrections for fast lookup
  const coveredIndices = new Set();
  for (const correction of corrections) {
    const [first, last] = correction.wordIndexRange;
    for (let i = first; i <= last; i++) {
      coveredIndices.add(i);
    }
  }

  // Emit high-confidence word segments for uncovered indices
  for (let i = 0; i < words.length; i++) {
    if (!coveredIndices.has(i)) {
      const word = words[i];
      segments.push({
        text: word.word,
        start: word.start,
        end: word.end,
        corrected: false,
        confidence: word.confidence,
      });
    }
  }

  // Emit corrected segments
  for (const correction of corrections) {
    segments.push({
      text: correction.text,
      start: correction.start,
      end: correction.end,
      corrected: true,
      language: correction.language,
    });
  }

  // Sort by ascending start
  segments.sort((a, b) => a.start - b.start);

  return segments;
}

module.exports = { reassemble };
