'use strict';

/**
 * Corpus Deduplication Guard
 *
 * Asserts that the former standalone regression corpus at
 * `services/postprocess/tests/fixtures/regression_corpus.json` does NOT
 * contain independent fixture cases. It must either not exist or be a
 * redirect stub (version === "RETIRED" and cases array is empty).
 *
 * This prevents the duplication from quietly returning after the corpus
 * was migrated to the shared `fixtures/golden-corpus/corpus.json`.
 *
 * Validates: Requirements 1.3, 2.1
 *
 * @module test/consistency/corpus-dedup
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const OLD_CORPUS_PATH = path.join(
  ROOT,
  'services',
  'postprocess',
  'tests',
  'fixtures',
  'regression_corpus.json'
);
const SHARED_CORPUS_PATH = path.join(
  ROOT,
  'fixtures',
  'golden-corpus',
  'corpus.json'
);

describe('Corpus Deduplication Guard', () => {
  it('shared golden corpus exists at fixtures/golden-corpus/corpus.json', () => {
    assert.ok(
      fs.existsSync(SHARED_CORPUS_PATH),
      'Shared golden corpus is missing — expected fixtures/golden-corpus/corpus.json to exist'
    );

    const data = JSON.parse(fs.readFileSync(SHARED_CORPUS_PATH, 'utf8'));
    assert.ok(
      Array.isArray(data.cases) && data.cases.length > 0,
      'Shared golden corpus must contain at least one case'
    );
  });

  it('old regression_corpus.json does not contain independent fixture cases', () => {
    // Case 1: file doesn't exist — perfectly fine, deduplication complete
    if (!fs.existsSync(OLD_CORPUS_PATH)) {
      return;
    }

    // Case 2: file exists — it must be a redirect stub
    const raw = fs.readFileSync(OLD_CORPUS_PATH, 'utf8');
    const data = JSON.parse(raw);

    // Must have the RETIRED version marker
    assert.strictEqual(
      data.version,
      'RETIRED',
      `Old regression_corpus.json still has version "${data.version}" — ` +
        'expected "RETIRED". The corpus must be retired in favour of ' +
        'fixtures/golden-corpus/corpus.json (Requirement 2.1).'
    );

    // Must have an empty cases array (no independent fixtures)
    assert.ok(
      Array.isArray(data.cases),
      'Old regression_corpus.json "cases" field must be an array'
    );
    assert.strictEqual(
      data.cases.length,
      0,
      `Old regression_corpus.json still contains ${data.cases.length} fixture case(s) — ` +
        'expected 0. All cases must live in fixtures/golden-corpus/corpus.json.'
    );
  });

  it('old regression_corpus.json contains a redirect pointer to the shared corpus', () => {
    if (!fs.existsSync(OLD_CORPUS_PATH)) {
      // File doesn't exist — acceptable, no redirect needed
      return;
    }

    const data = JSON.parse(fs.readFileSync(OLD_CORPUS_PATH, 'utf8'));

    // Should have a _redirect or _migrated_to field pointing to the shared corpus
    const hasRedirect =
      (data._redirect && data._redirect.includes('golden-corpus')) ||
      (data._migrated_to && data._migrated_to.includes('golden-corpus'));

    assert.ok(
      hasRedirect,
      'Old regression_corpus.json exists but lacks a redirect pointer to ' +
        'fixtures/golden-corpus/corpus.json. Add a "_redirect" or "_migrated_to" field.'
    );
  });
});
