'use strict';

// Feature: hybrid-confidence-transcription, Property 7:
// Empty corrections score lowest and lose

// Feature: hybrid-confidence-transcription, Property 8:
// Winner selection is deterministic with fixed tie-break

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { scoreResult, selectWinner } = require('../../lib/hybrid/scorer');

const LANGUAGES = ['tw', 'ee', 'gaa'];

/**
 * Generator for a word-like string containing at least one Unicode letter.
 */
const wordLikeText = fc.stringMatching(/[\p{L}][\p{L}\w ]{0,30}/u);

/**
 * Generator for empty/whitespace-only strings.
 */
const emptyText = fc.oneof(
  fc.constant(''),
  fc.constant('   '),
  fc.constant('\t\n'),
  fc.constant('  \t  ')
);

/**
 * Generator for a LanguageRaceResult with a word-like transcript (positive score).
 */
const wordLikeResult = fc.record({
  language: fc.constantFrom(...LANGUAGES),
  ok: fc.constant(true),
  transcript: wordLikeText,
});

/**
 * Generator for a LanguageRaceResult with empty/whitespace transcript (zero score).
 */
const emptyResult = fc.record({
  language: fc.constantFrom(...LANGUAGES),
  ok: fc.constant(true),
  transcript: emptyText,
});

/**
 * Validates: Requirements 8.1, 8.2
 */
describe('Property 7: Empty corrections score lowest and lose', () => {
  it('selectWinner never returns an empty/whitespace-only result when a word-like result exists', () => {
    const resultsArb = fc
      .tuple(
        fc.array(emptyResult, { minLength: 0, maxLength: 3 }),
        fc.array(wordLikeResult, { minLength: 1, maxLength: 3 })
      )
      .map(([empties, words]) => fc.shuffledSubarray([...empties, ...words], { minLength: empties.length + words.length, maxLength: empties.length + words.length }))
      .chain((arb) => arb);

    fc.assert(
      fc.property(resultsArb, (results) => {
        const winner = selectWinner(results);
        // There is at least one word-like result, so winner should not be null
        assert.notEqual(winner, null, 'winner should not be null when word-like results exist');
        // The winner's transcript should not be empty/whitespace-only
        assert.ok(
          winner.transcript.trim().length > 0,
          `winner transcript should not be empty/whitespace, got: "${winner.transcript}"`
        );
        // The winner's score should be positive
        assert.ok(winner.score > 0, `winner score should be positive, got: ${winner.score}`);
      }),
      { numRuns: 100 }
    );
  });
});

/**
 * Validates: Requirements 8.1, 8.3
 */
describe('Property 8: Winner selection is deterministic with fixed tie-break', () => {
  it('selectWinner is pure — same input always produces same output', () => {
    const resultArb = fc.record({
      language: fc.constantFrom(...LANGUAGES),
      ok: fc.boolean(),
      transcript: fc.oneof(wordLikeText, emptyText, fc.constant('hello world')),
    });

    const resultsArb = fc.array(resultArb, { minLength: 1, maxLength: 5 });

    fc.assert(
      fc.property(resultsArb, (results) => {
        const winner1 = selectWinner(results);
        const winner2 = selectWinner(results);
        assert.deepEqual(winner1, winner2, 'selectWinner should be pure/deterministic');
      }),
      { numRuns: 100 }
    );
  });

  it('on tied highest scores, selectWinner returns the tw > ee > gaa earliest result', () => {
    // Generate a fixed transcript so all results have identical scores
    const fixedTranscriptArb = wordLikeText;

    // Generate a subset of 2-3 distinct languages to tie
    const languageSubsetArb = fc.shuffledSubarray(LANGUAGES, { minLength: 2, maxLength: 3 });

    fc.assert(
      fc.property(fixedTranscriptArb, languageSubsetArb, (transcript, languages) => {
        // Create results with the same transcript (same score) for each language
        const results = languages.map((language) => ({
          language,
          ok: true,
          transcript,
        }));

        const winner = selectWinner(results);
        assert.notEqual(winner, null, 'winner should not be null');

        // The winner should be the language with the lowest tie-break index
        const expectedLanguage = languages.reduce((best, lang) => {
          const bestPriority = LANGUAGES.indexOf(best);
          const langPriority = LANGUAGES.indexOf(lang);
          return langPriority < bestPriority ? lang : best;
        });

        assert.equal(
          winner.language,
          expectedLanguage,
          `Expected tie-break winner "${expectedLanguage}" but got "${winner.language}" for languages ${JSON.stringify(languages)}`
        );
      }),
      { numRuns: 100 }
    );
  });
});
