'use strict';

/**
 * Entity Propagation Test
 *
 * Verifies that entities defined in the primary JS Dataset_Source propagate
 * correctly through the JS_Correction_Engine AND appear in the generated
 * dataset files under `services/postprocess/datasets/`.
 *
 * Three focused cases (not a generated sweep) because the Python half needs
 * a snapshot rebuild for full end-to-end coverage.
 *
 * Validates: Requirements 2.6
 *
 * @module test/consistency/entity-propagation
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { correctLocations } = require('../../lib/location-correction');
const { correctWordsWalk } = require('../../lib/location-correction/word-walk');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const ROOT = path.resolve(__dirname, '../..');
const DATASETS_DIR = path.join(ROOT, 'services', 'postprocess', 'datasets');

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Entity propagation (Requirement 2.6)', () => {
  it('location alias "Kumase" corrects to canonical "Kumasi" in JS engine', () => {
    const result = correctLocations('Kumase');
    assert.equal(result.text, 'Kumasi',
      `Expected "Kumase" → "Kumasi", got "${result.text}"`);
    assert.ok(result.corrections.length > 0,
      'Expected at least one correction for "Kumase"');
    assert.equal(result.corrections[0].corrected, 'Kumasi');
  });

  it('person alias "Ala Adjetey" corrects to canonical "Peter Ala Adjetey" in JS engine', () => {
    const result = correctLocations('Ala Adjetey');
    assert.equal(result.text, 'Peter Ala Adjetey',
      `Expected "Ala Adjetey" → "Peter Ala Adjetey", got "${result.text}"`);
    assert.ok(result.corrections.length > 0,
      'Expected at least one correction for "Ala Adjetey"');
    assert.equal(result.corrections[0].corrected, 'Peter Ala Adjetey');
  });

  it('party abbreviation "ndc" corrects to "NDC" in JS word-level engine', () => {
    const words = [
      { word: 'ndc', start: 0.0, end: 0.3, confidence: 0.92 },
    ];
    const corrected = correctWordsWalk(words);
    assert.equal(corrected.length, 1, 'Should produce exactly one word');
    assert.equal(corrected[0].word, 'NDC',
      `Expected "ndc" → "NDC", got "${corrected[0].word}"`);
    assert.equal(corrected[0].locationCorrected, true);
    assert.equal(corrected[0].entityKind, 'party');
  });

  it('all three entities exist in the generated dataset files', () => {
    // Load generated datasets
    const locationsPath = path.join(DATASETS_DIR, 'locations.json');
    const personsPath = path.join(DATASETS_DIR, 'persons.json');
    const partiesPath = path.join(DATASETS_DIR, 'parties.json');

    assert.ok(fs.existsSync(locationsPath), 'locations.json must exist');
    assert.ok(fs.existsSync(personsPath), 'persons.json must exist');
    assert.ok(fs.existsSync(partiesPath), 'parties.json must exist');

    const locations = JSON.parse(fs.readFileSync(locationsPath, 'utf8'));
    const persons = JSON.parse(fs.readFileSync(personsPath, 'utf8'));
    const parties = JSON.parse(fs.readFileSync(partiesPath, 'utf8'));

    // Verify Kumasi exists in locations
    const kumasi = locations.find(r => r.canonical === 'Kumasi');
    assert.ok(kumasi, 'Kumasi must be present in locations.json');
    assert.equal(kumasi.entity_type, 'city');

    // Verify Peter Ala Adjetey exists in persons
    const adjetey = persons.find(r => r.canonical === 'Peter Ala Adjetey');
    assert.ok(adjetey, 'Peter Ala Adjetey must be present in persons.json');
    assert.ok(adjetey.aliases.includes('Ala Adjetey'),
      'Peter Ala Adjetey must have "Ala Adjetey" alias in persons.json');

    // Verify NDC exists in parties
    const ndc = parties.find(r => r.abbreviation === 'NDC');
    assert.ok(ndc, 'NDC must be present in parties.json');
    assert.equal(ndc.canonical, 'National Democratic Congress');
  });
});
