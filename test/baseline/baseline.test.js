'use strict';

/**
 * Baseline Harness — JS Compare Mode
 *
 * Verifies that the current JS correction engine output is Behaviour_Equivalent
 * to the recorded baseline for every fixture in the Golden Corpus.
 *
 * This file is read-only: it imports nothing from record.js and contains no
 * filesystem write path. It runs under `pnpm test` via the `test/**\/*.test.js` glob.
 *
 * Validates: Requirements 1.4, 1.5, 1.9
 *
 * @module test/baseline/baseline.test
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const { loadCorpus, loadCorpusMeta } = require('../../fixtures/golden-corpus/loader');
const { correctLocations } = require('../../lib/location-correction/index');
const { correctWordsWalk } = require('../../lib/location-correction/word-walk');
const { correctYears, correctYearsInText } = require('../../lib/location-correction/year-correction');
const { behaviourEquivalent } = require('./behaviour-equivalent');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const RECORDED_DIR = path.resolve(__dirname, '../../fixtures/golden-corpus/recorded/js');
const MANIFEST_PATH = path.resolve(__dirname, '../../fixtures/golden-corpus/recorded/MANIFEST.json');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Compute sha256 hex digest of a string.
 * @param {string} content
 * @returns {string}
 */
function sha256(content) {
  return crypto.createHash('sha256').update(content, 'utf8').digest('hex');
}

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

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('JS Baseline Compare', () => {
  // Fail fast if no manifest exists
  if (!fs.existsSync(MANIFEST_PATH)) {
    it('should have a recorded baseline', () => {
      assert.fail('No recorded baseline found. Run: node test/baseline/record.js');
    });
    return;
  }

  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  const { version: corpusVersion } = loadCorpusMeta();
  const cases = loadCorpus();

  // Verify corpus version matches manifest
  if (manifest.corpus_version !== corpusVersion) {
    it('should have a baseline recorded for the current corpus version', () => {
      assert.fail(
        `Recorded baseline is for corpus v${manifest.corpus_version} but current corpus is v${corpusVersion}. Re-record with: node test/baseline/record.js --force`
      );
    });
    return;
  }

  for (const fixture of cases) {
    it(`fixture "${fixture.id}" matches recorded baseline`, () => {
      const filePath = path.join(RECORDED_DIR, `${fixture.id}.json`);

      // Check that the recorded file exists
      if (!fs.existsSync(filePath)) {
        assert.fail(
          `No recorded baseline for fixture '${fixture.id}'. Run: node test/baseline/record.js`
        );
      }

      // Read and verify integrity
      const content = fs.readFileSync(filePath, 'utf8');
      const actualHash = sha256(content);
      const expectedHash = manifest.entries[fixture.id];

      if (actualHash !== expectedHash) {
        assert.fail(
          `Integrity check failed for fixture '${fixture.id}' — recorded file was modified outside record mode`
        );
      }

      const recorded = JSON.parse(content);

      // Verify corpus version in the recorded file
      if (recorded.corpus_version !== corpusVersion) {
        assert.fail(
          `Recorded baseline is for corpus v${recorded.corpus_version} but current corpus is v${corpusVersion}. Re-record with: node test/baseline/record.js --force`
        );
      }

      // --- Run current JS correction engine ---

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

      // --- Compare against recorded baseline ---

      const current = {
        transcript: finalTranscript,
        words: finalWords,
        entities,
      };

      const baseline = {
        transcript: recorded.transcript,
        words: recorded.words,
        entities: recorded.entities,
      };

      const result = behaviourEquivalent(current, baseline);

      if (!result.equivalent) {
        const diffReport = result.differences.map(d =>
          `  field: ${d.field}\n    baseline: ${JSON.stringify(d.baseline)}\n    current:  ${JSON.stringify(d.actual)}`
        ).join('\n');

        assert.fail(
          `Fixture '${fixture.id}' — behaviour differs from recorded baseline:\n${diffReport}`
        );
      }
    });
  }
});
