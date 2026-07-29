'use strict';

/**
 * The seven word-level fields compared for Behaviour_Equivalence.
 * @type {string[]}
 */
const COMPARED_WORD_FIELDS = [
  'word', 'start', 'end', 'confidence',
  'locationCorrected', 'entityKind', 'entityType',
];

/**
 * The three boolean flags where absent (undefined/null) is treated as equal to false.
 * @type {Set<string>}
 */
const BOOLEAN_FLAGS = new Set(['locationCorrected', 'entityKind', 'entityType']);

/**
 * Normalise a boolean-flag field value.
 * absent / undefined / null / false all normalise to false.
 * Any other value is returned as-is.
 *
 * @param {*} value
 * @returns {*}
 */
function normaliseFlagValue(value) {
  if (value === undefined || value === null || value === false) {
    return false;
  }
  return value;
}

/**
 * Compare two word objects on the COMPARED_WORD_FIELDS.
 * Returns an array of difference objects for fields that differ.
 *
 * @param {object} actualWord
 * @param {object} expectedWord
 * @param {number} index - word index for field path reporting
 * @returns {Array<{field: string, baseline: *, actual: *}>}
 */
function compareWords(actualWord, expectedWord, index) {
  const diffs = [];

  for (const field of COMPARED_WORD_FIELDS) {
    let actualVal = actualWord[field];
    let expectedVal = expectedWord[field];

    // Normalisation rule 1: absent equals false for boolean flags
    if (BOOLEAN_FLAGS.has(field)) {
      actualVal = normaliseFlagValue(actualVal);
      expectedVal = normaliseFlagValue(expectedVal);
    }

    // Normalisation rule 2: float comparison is exact for start/end/confidence
    // (both engines pass provider values through untouched)
    // No epsilon — values must match exactly.

    if (actualVal !== expectedVal) {
      diffs.push({
        field: `words[${index}].${field}`,
        baseline: expectedVal,
        actual: actualVal,
      });
    }
  }

  return diffs;
}

/**
 * Serialise an entity for multiset comparison.
 * Produces a stable string key from (name, kind, type, mentions).
 *
 * @param {object} entity
 * @returns {string}
 */
function entityKey(entity) {
  return JSON.stringify([
    entity.name ?? '',
    entity.kind ?? '',
    entity.type ?? '',
    entity.mentions ?? 0,
  ]);
}

/**
 * Compare two entity arrays as multisets.
 * Normalisation rule 3: order does not matter — sort both by (name, kind, type, mentions).
 * Returns differences when the multisets are not equal.
 *
 * @param {Array} actualEntities
 * @param {Array} expectedEntities
 * @returns {Array<{field: string, baseline: *, actual: *}>}
 */
function compareEntities(actualEntities, expectedEntities) {
  const diffs = [];

  const actual = (actualEntities || []).slice();
  const expected = (expectedEntities || []).slice();

  // Sort both arrays by (name, kind, type, mentions) for stable multiset comparison
  const sorter = (a, b) => {
    const aName = a.name ?? '';
    const bName = b.name ?? '';
    if (aName !== bName) return aName < bName ? -1 : 1;

    const aKind = a.kind ?? '';
    const bKind = b.kind ?? '';
    if (aKind !== bKind) return aKind < bKind ? -1 : 1;

    const aType = a.type ?? '';
    const bType = b.type ?? '';
    if (aType !== bType) return aType < bType ? -1 : 1;

    const aMentions = a.mentions ?? 0;
    const bMentions = b.mentions ?? 0;
    return aMentions - bMentions;
  };

  actual.sort(sorter);
  expected.sort(sorter);

  // If lengths differ, report that
  if (actual.length !== expected.length) {
    diffs.push({
      field: 'entities.length',
      baseline: expected.length,
      actual: actual.length,
    });
  }

  // Compare element by element after sorting
  const len = Math.max(actual.length, expected.length);
  for (let i = 0; i < len; i++) {
    const a = actual[i];
    const e = expected[i];

    if (!a && e) {
      diffs.push({
        field: `entities[${i}]`,
        baseline: e,
        actual: undefined,
      });
      continue;
    }

    if (a && !e) {
      diffs.push({
        field: `entities[${i}]`,
        baseline: undefined,
        actual: a,
      });
      continue;
    }

    if (entityKey(a) !== entityKey(e)) {
      diffs.push({
        field: `entities[${i}]`,
        baseline: e,
        actual: a,
      });
    }
  }

  return diffs;
}

/**
 * Compares two pipeline outputs for Behaviour_Equivalence.
 *
 * Two outputs are Behaviour_Equivalent when:
 * - Their corrected transcript strings are byte-identical
 * - Their corrected word arrays are equal element-wise on COMPARED_WORD_FIELDS
 * - Their entity summaries are equal as multisets of (name, kind, type, mentions)
 *
 * Normalisation rules applied:
 * 1. absent/undefined/null equals false for locationCorrected, entityKind, entityType
 * 2. start/end/confidence compare exactly (no epsilon)
 * 3. Entity arrays compared as multisets (order-independent)
 *
 * @param {{transcript: string, words: Array, entities: Array}} actual
 * @param {{transcript: string, words: Array, entities: Array}} expected
 * @returns {{ equivalent: boolean, differences: Array<{field: string, baseline: *, actual: *}> }}
 */
function behaviourEquivalent(actual, expected) {
  const differences = [];

  // Compare transcripts byte-identically
  if (actual.transcript !== expected.transcript) {
    differences.push({
      field: 'transcript',
      baseline: expected.transcript,
      actual: actual.transcript,
    });
  }

  // Compare word arrays
  const actualWords = actual.words || [];
  const expectedWords = expected.words || [];

  // Report length mismatch
  if (actualWords.length !== expectedWords.length) {
    differences.push({
      field: 'words.length',
      baseline: expectedWords.length,
      actual: actualWords.length,
    });
  }

  // Compare element-wise up to the shorter length
  const wordLen = Math.min(actualWords.length, expectedWords.length);
  for (let i = 0; i < wordLen; i++) {
    const wordDiffs = compareWords(actualWords[i], expectedWords[i], i);
    differences.push(...wordDiffs);
  }

  // Compare entities as multisets
  const entityDiffs = compareEntities(actual.entities, expected.entities);
  differences.push(...entityDiffs);

  return {
    equivalent: differences.length === 0,
    differences,
  };
}

module.exports = { behaviourEquivalent, COMPARED_WORD_FIELDS };
