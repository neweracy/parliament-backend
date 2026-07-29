'use strict';

// Feature: code-cleanup-optimization, Property 2:
// For any generated transcript, the output of the post-refactor JS_Correction_Engine is
// Behaviour_Equivalent to the output of the pre-refactor JS_Correction_Engine on the same transcript.

/**
 * Behaviour-Preservation Property Test
 *
 * Uses fast-check to select corpus fixtures and verify that the current
 * JS correction engine output is Behaviour_Equivalent to the recorded
 * pre-refactor baseline (the oracle).
 *
 * Validates: Requirements 1.6
 *
 * @module test/baseline/behaviour-preservation.pbt.test
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const fc = require('fast-check');

const { loadCorpus, loadCorpusMeta } = require('../../fixtures/golden-corpus/loader');
const { correctLocations } = require('../../lib/location-correction/index');
const { correctWordsWalk } = require('../../lib/location-correction/word-walk');
const { correctYears, correctYearsInText } = require('../../lib/location-correction/year-correction');
const { behaviourEquivalent } = require('./behaviour-equivalent');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const RECORDED_DIR = path.resolve(__dirname, '../../fixtures/golden-corpus/recorded/js');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build an entity summary from word-level corrections.
 * Groups by corrected name and produces { name, kind, type, mentions }.
 *
 * @param {Array} correctedWords
 * @returns {Array<{name: string, kind: string, type: string, mentions: number}>}
 */
function buildEntitySummary(correctedWords) {
  const entityMap = new Map();

  for (const w of correctedWords) {
    if (w.locationCorrected && w.entityKind) {
      const key = `${w.word}|${w.entityKind}|${w.entityType || ''}`;
      if (!entityMap.has(key)) {
        entityMap.set(key, {
          name: w.word,
          kind: w.entityKind,
          type: w.entityType || '',
          mentions: 0,
        });
      }
      entityMap.get(key).mentions += 1;
    }
  }

  return Array.from(entityMap.values());
}

/**
 * Run the full JS correction pipeline on a fixture and return the output
 * in the same shape as the recorded baseline.
 *
 * @param {{input_transcript: string, input_words: Array}} fixture
 * @returns {{transcript: string, words: Array, entities: Array}}
 */
function runEngine(fixture) {
  // 1. Text-level correction
  const textResult = correctLocations(fixture.input_transcript);

  // 2. Word-level correction via n-gram walk
  const wordsInput = fixture.input_words.map(w => ({ ...w }));
  const correctedWords = correctWordsWalk(wordsInput);

  // 3. Year corrections on transcript text
  const yearTextResult = correctYearsInText(textResult.text);

  // 4. Year corrections on word array
  const yearWordResult = correctYears(correctedWords);
  const finalWords = yearWordResult.words;

  // 5. Build the final transcript from year-corrected text
  const finalTranscript = yearTextResult.text;

  // 6. Build entity summary from the word-level output
  const entities = buildEntitySummary(finalWords);

  return { transcript: finalTranscript, words: finalWords, entities };
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('Property 2: JS_Correction_Engine behaviour is preserved across refactoring', () => {
  const { version: corpusVersion } = loadCorpusMeta();
  const corpus = loadCorpus();

  // Load all recorded baselines that exist
  const fixturesWithBaselines = corpus.filter((fixture) => {
    const filePath = path.join(RECORDED_DIR, `${fixture.id}.json`);
    return fs.existsSync(filePath);
  });

  if (fixturesWithBaselines.length === 0) {
    it('should have recorded baselines', () => {
      assert.fail('No recorded baselines found. Run: node test/baseline/record.js');
    });
    return;
  }

  // Pre-load all recorded baselines into memory for fast-check access
  const baselineMap = new Map();
  for (const fixture of fixturesWithBaselines) {
    const filePath = path.join(RECORDED_DIR, `${fixture.id}.json`);
    const content = fs.readFileSync(filePath, 'utf8');
    const recorded = JSON.parse(content);

    // Verify corpus version
    if (recorded.corpus_version !== corpusVersion) {
      it(`should have baselines matching corpus version (${fixture.id})`, () => {
        assert.fail(
          `Recorded baseline for '${fixture.id}' is for corpus v${recorded.corpus_version} but current corpus is v${corpusVersion}. Re-record with: node test/baseline/record.js --force`
        );
      });
      return;
    }

    baselineMap.set(fixture.id, {
      transcript: recorded.transcript,
      words: recorded.words,
      entities: recorded.entities,
    });
  }

  it('current engine output matches recorded baseline for any corpus fixture', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...fixturesWithBaselines),
        (fixture) => {
          const current = runEngine(fixture);
          const recorded = baselineMap.get(fixture.id);

          const result = behaviourEquivalent(current, recorded);

          if (!result.equivalent) {
            const diffReport = result.differences.slice(0, 5).map(d =>
              `  ${d.field}: baseline=${JSON.stringify(d.baseline)}, actual=${JSON.stringify(d.actual)}`
            ).join('\n');

            assert.fail(
              `Fixture '${fixture.id}' — behaviour differs from recorded baseline:\n${diffReport}`
            );
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});
