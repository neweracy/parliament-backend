/**
 * Bug Condition Exploration Test — Honorific Person Recognition
 *
 * Property 1: Title-Preceded Person Names Not Corrected
 *
 * This test encodes the EXPECTED behavior: when a title prefix precedes a
 * known person name, correctLocations() should recognize and correct the
 * person name while preserving the title unchanged.
 *
 * On UNFIXED code, this test is EXPECTED TO FAIL — failure confirms the
 * bug exists (title-preceded names are not recognized/corrected).
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4
 */

'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { correctLocations } = require('../../lib/location-correction');
const { ALL_PERSONS } = require('../../lib/location-correction/persons-dataset');
const { ALL_MPS } = require('../../lib/location-correction/mps-dataset');

// ---------------------------------------------------------------------------
// Title variants to test (subset of TITLE_PREFIXES most common in parliament)
// ---------------------------------------------------------------------------

const TITLE_VARIANTS = ['Honourable', 'Hon', 'Hon.', 'Rt'];

// ---------------------------------------------------------------------------
// Build a pool of known person surnames from datasets
// ---------------------------------------------------------------------------

/**
 * Extract surnames (last word of canonical name) from the datasets.
 * These are names that should be recognized when preceded by a title.
 */
function extractSurnames(entries) {
  const surnames = new Set();
  for (const entry of entries) {
    const canonical = entry.canonical || entry.name;
    const parts = canonical.split(/\s+/);
    if (parts.length >= 2) {
      const surname = parts[parts.length - 1];
      // Only include surnames that are reasonably distinctive (4+ chars)
      if (surname.length >= 4) {
        surnames.add(surname);
      }
    }
  }
  return [...surnames];
}

const PERSON_SURNAMES = extractSurnames([...ALL_PERSONS, ...ALL_MPS]);

// ---------------------------------------------------------------------------
// Build pool of multi-word name suffixes (last 2 words)
// ---------------------------------------------------------------------------

function extractMultiWordSuffixes(entries) {
  const suffixes = [];
  for (const entry of entries) {
    const canonical = entry.canonical || entry.name;
    const parts = canonical.split(/\s+/);
    if (parts.length >= 3) {
      // Take last 2 parts as multi-word suffix
      const suffix = parts.slice(-2).join(' ');
      suffixes.push({ suffix, canonical });
    }
  }
  return suffixes;
}

// eslint-disable-next-line no-unused-vars
const MULTI_WORD_NAMES = extractMultiWordSuffixes([...ALL_PERSONS, ...ALL_MPS]);

// ---------------------------------------------------------------------------
// Property 1: Bug Condition — Title + Known Person Surname
// ---------------------------------------------------------------------------

describe('Property 1: Bug Condition — Title-Preceded Person Names Not Corrected', () => {
  it('correctLocations(title + " " + surname) SHALL return a correction with entityKind "person"', () => {
    /**
     * Validates: Requirements 1.1, 1.3
     *
     * For any combination of title variant × known person surname,
     * correctLocations should produce a non-empty corrections array
     * with at least one entry having entityKind === "person".
     */
    fc.assert(
      fc.property(
        fc.constantFrom(...TITLE_VARIANTS),
        fc.constantFrom(...PERSON_SURNAMES),
        (title, surname) => {
          const input = `${title} ${surname}`;
          const result = correctLocations(input);

          // The corrections or entitiesFound should contain a person match
          const hasPersonCorrection = result.corrections.some(
            c => c.entityKind === 'person'
          );
          const hasPersonEntity = (result.entitiesFound || []).some(
            e => e.entityKind === 'person'
          );

          assert.ok(
            hasPersonCorrection || hasPersonEntity,
            `Expected "${input}" to produce a person correction or entity recognition, ` +
            `but got corrections: ${JSON.stringify(result.corrections)}, ` +
            `entitiesFound: ${JSON.stringify(result.entitiesFound || [])}`
          );
        }
      ),
      { numRuns: 200 }
    );
  });

  it('title word is preserved unchanged in output text', () => {
    /**
     * Validates: Requirements 1.1
     *
     * When a title precedes a person name, the title itself should
     * remain unchanged in the output — only the name gets corrected.
     */
    fc.assert(
      fc.property(
        fc.constantFrom(...TITLE_VARIANTS),
        fc.constantFrom(...PERSON_SURNAMES),
        (title, surname) => {
          const input = `${title} ${surname}`;
          const result = correctLocations(input);

          // Title must be preserved at the start of the output
          assert.ok(
            result.text.startsWith(title),
            `Expected output to start with "${title}", got "${result.text}"`
          );
        }
      ),
      { numRuns: 100 }
    );
  });

  it('misspelled surnames (1-2 char edits) after title are corrected at lowered threshold', () => {
    /**
     * Validates: Requirements 1.2
     *
     * When a title precedes a slightly misspelled person surname,
     * correctLocations should still correct it using a lowered threshold.
     */
    // Concrete misspelled cases from the design doc
    const misspelledCases = [
      { input: 'Honourable Ablakwah', expectedName: 'Ablakwa' },
      { input: 'Honourable Jinapor', expectedName: 'Jinapor' },
      { input: 'Hon Bagben', expectedName: 'Bagbin' },
      { input: 'Honourable Muntakah', expectedName: 'Muntaka' },
    ];

    for (const { input, expectedName } of misspelledCases) {
      const result = correctLocations(input, { minConfidence: 0.65 });

      const hasPersonMatch = result.corrections.some(
        c => c.entityKind === 'person'
      ) || (result.entitiesFound || []).some(
        e => e.entityKind === 'person'
      );

      assert.ok(
        hasPersonMatch,
        `Expected "${input}" to correct misspelled "${expectedName}" as person, ` +
        `but got corrections: ${JSON.stringify(result.corrections)}`
      );
    }
  });

  it('multi-word names after titles are matched as person entities', () => {
    /**
     * Validates: Requirements 1.4
     *
     * When a title precedes a multi-word person name, the engine should
     * strip the title and match the remaining tokens as a person name.
     */
    // Use a subset of multi-word names for concrete testing
    const multiWordCases = [
      'Honourable Okudzeto Ablakwa',
      'Hon. Mubarak Muntaka',
      'Honourable Ofori-Atta',
      'Honourable Opoku Prempeh',
      'Rt Kyei-Mensah-Bonsu',
    ];

    for (const input of multiWordCases) {
      const result = correctLocations(input);

      const hasPersonMatch = result.corrections.some(
        c => c.entityKind === 'person'
      ) || (result.entitiesFound || []).some(
        e => e.entityKind === 'person'
      );

      assert.ok(
        hasPersonMatch,
        `Expected "${input}" to match a multi-word person name, ` +
        `but got corrections: ${JSON.stringify(result.corrections)}, ` +
        `entitiesFound: ${JSON.stringify(result.entitiesFound || [])}`
      );
    }
  });
});
