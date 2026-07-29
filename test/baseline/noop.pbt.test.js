'use strict';

// Feature: code-cleanup-optimization, Property 4:
// For all generated transcripts containing no entity from any Dataset_Source
// and no spoken year, the corrected transcript SHALL be byte-identical to the
// input transcript.

/**
 * No-Op Property Test
 *
 * Uses fast-check to generate transcripts from a safe word pool that contains
 * NO entity names from any Ghana dataset and NO year-related words. Verifies
 * that the correction engine leaves such transcripts completely unchanged.
 *
 * Validates: Requirements 1.8
 *
 * @module test/baseline/noop.pbt.test
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');

const { correctLocations } = require('../../lib/location-correction/index');
const { correctWordsWalk } = require('../../lib/location-correction/word-walk');

// ---------------------------------------------------------------------------
// Safe word pool — common English words that will NOT trigger any corrections
// ---------------------------------------------------------------------------

const SAFE_WORDS = [
  'hello', 'world', 'today', 'weather', 'happy', 'cloud',
  'building', 'morning', 'evening',
  'water', 'bread', 'chair', 'window', 'door', 'floor',
  'computer', 'keyboard', 'science', 'library',
];

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

const safeWordGen = fc.constantFrom(...SAFE_WORDS);

const transcriptGen = fc.array(safeWordGen, { minLength: 1, maxLength: 20 })
  .map(ws => ws.join(' '));

const wordsArrayGen = fc.array(safeWordGen, { minLength: 1, maxLength: 20 });

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a synthetic words array from a list of word strings.
 * Assigns fixed timing (0.3s per word) and confidence 0.95.
 *
 * @param {string[]} wordList
 * @returns {Array<{word: string, start: number, end: number, confidence: number}>}
 */
function buildWordsFromList(wordList) {
  return wordList.map((word, i) => ({
    word,
    start: i * 0.3,
    end: (i + 1) * 0.3,
    confidence: 0.95,
  }));
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('Property 4: Correction is a no-op on entity-free, year-free input', () => {
  it('text-level: correctLocations produces byte-identical output for entity-free transcripts', () => {
    fc.assert(
      fc.property(
        transcriptGen,
        (inputTranscript) => {
          const result = correctLocations(inputTranscript);

          assert.strictEqual(
            result.text,
            inputTranscript,
            `Text-level correction altered an entity-free transcript.\n` +
            `Input:  "${inputTranscript}"\n` +
            `Output: "${result.text}"\n` +
            `Corrections: ${JSON.stringify(result.corrections)}`
          );

          // No corrections should be produced
          assert.strictEqual(
            result.corrections.length,
            0,
            `Text-level correction produced corrections on entity-free input.\n` +
            `Input: "${inputTranscript}"\n` +
            `Corrections: ${JSON.stringify(result.corrections)}`
          );
        }
      ),
      { numRuns: 100 }
    );
  });

  it('word-level: correctWordsWalk produces byte-identical words for entity-free input', () => {
    fc.assert(
      fc.property(
        wordsArrayGen,
        (wordList) => {
          const inputWords = buildWordsFromList(wordList);

          // Run word-level correction
          const correctedWords = correctWordsWalk(inputWords.map(w => ({ ...w })));

          // Word count must be identical
          assert.strictEqual(
            correctedWords.length,
            inputWords.length,
            `Word-level correction changed word count on entity-free input.\n` +
            `Input count:  ${inputWords.length}\n` +
            `Output count: ${correctedWords.length}\n` +
            `Input words:  ${JSON.stringify(wordList)}\n` +
            `Output words: ${JSON.stringify(correctedWords.map(w => w.word))}`
          );

          // Each word text must be byte-identical
          for (let i = 0; i < inputWords.length; i++) {
            assert.strictEqual(
              correctedWords[i].word,
              inputWords[i].word,
              `Word-level correction altered word at index ${i}.\n` +
              `Input:  "${inputWords[i].word}"\n` +
              `Output: "${correctedWords[i].word}"\n` +
              `Full input: ${JSON.stringify(wordList)}`
            );
          }

          // No entity annotations should be present
          for (let i = 0; i < correctedWords.length; i++) {
            assert.strictEqual(
              correctedWords[i].locationCorrected,
              undefined,
              `Word at index ${i} was annotated as corrected on entity-free input.\n` +
              `Word: "${correctedWords[i].word}"\n` +
              `Full input: ${JSON.stringify(wordList)}`
            );
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});
