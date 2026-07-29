'use strict';

/**
 * Baseline Harness — JS Record Mode
 *
 * Records the pre-refactor output of the JS correction engine for every
 * fixture in the Golden Corpus. Invoked as:
 *
 *   node test/baseline/record.js [--force]
 *
 * This file intentionally does NOT match the test glob, so pnpm test
 * cannot execute it.
 *
 * Outputs:
 *   fixtures/golden-corpus/recorded/js/<fixture-id>.json   — one per fixture
 *   fixtures/golden-corpus/recorded/MANIFEST.json          — sha256 map
 *
 * @module test/baseline/record
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const { loadCorpus, loadCorpusMeta } = require('../../fixtures/golden-corpus/loader');
const { correctLocations } = require('../../lib/location-correction/index');
const { correctWordsWalk } = require('../../lib/location-correction/word-walk');
const { correctYears, correctYearsInText } = require('../../lib/location-correction/year-correction');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const RECORDED_DIR = path.resolve(__dirname, '../../fixtures/golden-corpus/recorded/js');
const MANIFEST_PATH = path.resolve(__dirname, '../../fixtures/golden-corpus/recorded/MANIFEST.json');

// ---------------------------------------------------------------------------
// CLI flag parsing
// ---------------------------------------------------------------------------

const args = process.argv.slice(2);
const forceFlag = args.includes('--force');

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
 * @param {Array} correctedWords - The corrected words array from correctWordsWalk
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
 * Build corrections summary from the text-level and word-level processing.
 *
 * @param {object} textResult - Result from correctLocations (text-level)
 * @param {Array} correctedWords - Result from correctWordsWalk (word-level)
 * @param {object} yearTextResult - Result from correctYearsInText
 * @param {object} yearWordResult - Result from correctYears (word-level)
 * @returns {Array<{original: string, corrected: string, strategy: string, confidence: number}>}
 */
function buildCorrections(textResult, correctedWords, yearTextResult, yearWordResult) {
  const corrections = [];
  const seen = new Set();

  // Location/person/party corrections from text-level
  for (const c of textResult.corrections) {
    const key = `${c.original}→${c.corrected}`;
    if (!seen.has(key)) {
      seen.add(key);
      corrections.push({
        original: c.original,
        corrected: c.corrected,
        strategy: c.strategy,
        confidence: c.confidence,
      });
    }
  }

  // Year corrections from text-level
  for (const c of yearTextResult.corrections) {
    const key = `${c.original}→${c.corrected}`;
    if (!seen.has(key)) {
      seen.add(key);
      corrections.push({
        original: c.original,
        corrected: c.corrected,
        strategy: 'year',
        confidence: 1.0,
      });
    }
  }

  // Year corrections from word-level
  for (const c of yearWordResult.corrections) {
    const key = `${c.original}→${c.corrected}`;
    if (!seen.has(key)) {
      seen.add(key);
      corrections.push({
        original: c.original,
        corrected: c.corrected,
        strategy: 'year',
        confidence: 1.0,
      });
    }
  }

  return corrections;
}

// ---------------------------------------------------------------------------
// Main recording logic
// ---------------------------------------------------------------------------

function record() {
  const { version: corpusVersion } = loadCorpusMeta();
  const cases = loadCorpus();

  console.log(`Recording JS baseline for corpus v${corpusVersion} (${cases.length} fixtures)`);

  // Check for existing files unless --force
  if (!forceFlag) {
    const existingIds = [];
    for (const fixture of cases) {
      const filePath = path.join(RECORDED_DIR, `${fixture.id}.json`);
      if (fs.existsSync(filePath)) {
        // Check if it's the same corpus version
        try {
          const existing = JSON.parse(fs.readFileSync(filePath, 'utf8'));
          if (existing.corpus_version === corpusVersion) {
            existingIds.push(fixture.id);
          }
        } catch {
          // Malformed file — treat as needing overwrite
        }
      }
    }

    if (existingIds.length > 0) {
      console.error(`\nERROR: Recorded files already exist for corpus v${corpusVersion}.`);
      console.error('Use --force to overwrite.\n');
      console.error('Fixture IDs that would be overwritten:');
      for (const id of existingIds) {
        console.error(`  - ${id}`);
      }
      process.exit(1);
    }
  }

  // Ensure output directory exists
  fs.mkdirSync(RECORDED_DIR, { recursive: true });

  const manifestEntries = {};
  const recordedAt = new Date().toISOString();

  for (const fixture of cases) {
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

    // 7. Build corrections summary
    const corrections = buildCorrections(textResult, correctedWords, yearTextResult, yearWordResult);

    // 8. Assemble output
    const output = {
      fixture_id: fixture.id,
      engine: 'js',
      corpus_version: corpusVersion,
      recorded_at: recordedAt,
      transcript: finalTranscript,
      words: finalWords,
      entities,
      corrections,
    };

    // 9. Write to file
    const content = JSON.stringify(output, null, 2) + '\n';
    const filePath = path.join(RECORDED_DIR, `${fixture.id}.json`);
    fs.writeFileSync(filePath, content, 'utf8');

    // 10. Track sha256 for manifest
    manifestEntries[fixture.id] = sha256(content);

    console.log(`  ✓ ${fixture.id}`);
  }

  // Write MANIFEST.json
  const manifest = {
    corpus_version: corpusVersion,
    recorded_at: recordedAt,
    entries: manifestEntries,
  };
  const manifestContent = JSON.stringify(manifest, null, 2) + '\n';
  fs.writeFileSync(MANIFEST_PATH, manifestContent, 'utf8');

  console.log(`\nDone. Recorded ${cases.length} fixtures.`);
  console.log(`Manifest: ${path.relative(process.cwd(), MANIFEST_PATH)}`);
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

record();
