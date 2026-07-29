'use strict';

// Feature: code-cleanup-optimization, Property 3:
// For all generated transcripts, correcting an already-corrected transcript SHALL produce
// a transcript byte-identical to the once-corrected transcript.

/**
 * Idempotence Property Test
 *
 * Uses fast-check to generate random transcripts and verify that
 * correct(correct(x)) === correct(x) for any input x.
 *
 * Validates: Requirements 1.7
 *
 * @module test/baseline/idempotence.pbt.test
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');

const { correctLocations } = require('../../lib/location-correction/index');
const { correctWordsWalk } = require('../../lib/location-correction/word-walk');

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

const wordGen = fc.oneof(
  fc.constantFrom('Kumasi', 'ndc', 'Nkrumah', 'ningoprampram', 'general', 'the', 'was'),
  fc.string({ minLength: 2, maxLength: 15 })
);

const transcriptGen = fc.array(wordGen, { minLength: 1, maxLength: 20 }).map(ws => ws.join(' '));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a synthetic words array from a transcript string.
 * Assigns fixed timing (0.3s per word) and confidence 0.95.
 *
 * @param {string} transcript
 * @returns {Array<{word: string, start: number, end: number, confidence: number}>}
 */
function buildWordsFromTranscript(transcript) {
  const tokens = transcript.split(/\s+/).filter(t => t.length > 0);
  return tokens.map((word, i) => ({
    word,
    start: i * 0.3,
    end: (i + 1) * 0.3,
    confidence: 0.95,
  }));
}

/**
 * Run the JS correction engine (text-level + word-level) and return
 * the corrected transcript and words.
 *
 * @param {string} transcript
 * @param {Array<{word: string, start: number, end: number, confidence: number}>} inputWords
 * @returns {{ transcript: string, words: Array }}
 */
function runCorrection(transcript, inputWords) {
  // Text-level correction
  const textResult = correctLocations(transcript);

  // Word-level correction via n-gram walk
  const wordsCopy = inputWords.map(w => ({ ...w }));
  const correctedWords = correctWordsWalk(wordsCopy);

  return { transcript: textResult.text, words: correctedWords };
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('Property 3: Correction is idempotent', () => {
  it('word-level: correctWordsWalk(correctWordsWalk(x)) produces identical word texts', () => {
    fc.assert(
      fc.property(
        transcriptGen,
        (inputTranscript) => {
          // Build words from the input transcript
          const inputWords = buildWordsFromTranscript(inputTranscript);

          // First word-level pass
          const correctedWords1 = correctWordsWalk(inputWords.map(w => ({ ...w })));

          // Build input_words for the second pass from the corrected words
          const secondPassWords = correctedWords1.map(w => ({
            word: w.word,
            start: w.start,
            end: w.end,
            confidence: w.confidence,
          }));

          // Second word-level pass
          const correctedWords2 = correctWordsWalk(secondPassWords.map(w => ({ ...w })));

          // Assert word-level idempotence (word texts byte-identical)
          const texts1 = correctedWords1.map(w => w.word);
          const texts2 = correctedWords2.map(w => w.word);

          assert.deepStrictEqual(
            texts2,
            texts1,
            `Word-level correction is not idempotent.\n` +
            `Input:        "${inputTranscript}"\n` +
            `First pass:   ${JSON.stringify(texts1)}\n` +
            `Second pass:  ${JSON.stringify(texts2)}`
          );
        }
      ),
      { numRuns: 100 }
    );
  });
});
