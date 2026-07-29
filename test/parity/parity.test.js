'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { behaviourEquivalent } = require('../baseline/behaviour-equivalent.js');

/**
 * The authoritative engine for correction algorithm decisions.
 * When a divergence is found, this engine's output is the reference.
 */
const AUTHORITATIVE_ENGINE = 'py';

const JS_DIR = path.join(__dirname, '..', '..', 'fixtures', 'golden-corpus', 'recorded', 'js');
const PY_DIR = path.join(__dirname, '..', '..', 'fixtures', 'golden-corpus', 'recorded', 'py');
const DIVERGENCES_PATH = path.join(__dirname, 'accepted-divergences.json');

/**
 * Load the accepted divergences declaration file.
 * @returns {{version: string, entries: Array}}
 */
function loadDivergences() {
  const raw = fs.readFileSync(DIVERGENCES_PATH, 'utf8');
  return JSON.parse(raw);
}

/**
 * Build a lookup key for a declared divergence.
 * @param {string} fixtureId
 * @param {string} field
 * @returns {string}
 */
function divergenceKey(fixtureId, field) {
  return `${fixtureId}::${field}`;
}

/**
 * Build a Set of declared divergence keys for quick lookup.
 * @param {Array} entries
 * @returns {Map<string, object>}
 */
function buildDivergenceIndex(entries) {
  const index = new Map();
  for (const entry of entries) {
    const key = divergenceKey(entry.fixture_id, entry.field);
    index.set(key, entry);
  }
  return index;
}

/**
 * Format a paste-ready JSON snippet for an undeclared divergence.
 * @param {string} fixtureId
 * @param {string} field
 * @param {*} jsValue
 * @param {*} pyValue
 * @returns {string}
 */
function formatDeclarationSnippet(fixtureId, field, jsValue, pyValue) {
  const entry = {
    fixture_id: fixtureId,
    field,
    js_value: typeof jsValue === 'object' ? JSON.stringify(jsValue) : String(jsValue ?? 'undefined'),
    py_value: typeof pyValue === 'object' ? JSON.stringify(pyValue) : String(pyValue ?? 'undefined'),
    reason: 'TODO: describe the divergence reason',
    authoritative: AUTHORITATIVE_ENGINE,
    resolution: 'accepted',
    recorded_at: new Date().toISOString(),
  };
  return JSON.stringify(entry, null, 2);
}

describe('Parity: JS vs Python correction engine outputs', () => {
  const divergences = loadDivergences();
  const divergenceIndex = buildDivergenceIndex(divergences.entries);

  // Discover all fixture IDs that have both a JS and PY recording
  const jsFiles = fs.readdirSync(JS_DIR).filter(f => f.endsWith('.json'));
  const pyFiles = new Set(fs.readdirSync(PY_DIR).filter(f => f.endsWith('.json')));

  const pairedFixtures = jsFiles
    .filter(f => pyFiles.has(f))
    .map(f => {
      const jsData = JSON.parse(fs.readFileSync(path.join(JS_DIR, f), 'utf8'));
      const pyData = JSON.parse(fs.readFileSync(path.join(PY_DIR, f), 'utf8'));
      return { file: f, jsData, pyData, fixtureId: jsData.fixture_id };
    });

  it('should have at least one paired fixture to compare', () => {
    assert.ok(pairedFixtures.length > 0, 'No paired JS/PY fixtures found');
  });

  for (const { file, jsData, pyData, fixtureId } of pairedFixtures) {
    it(`parity: ${fixtureId}`, () => {
      const { equivalent, differences } = behaviourEquivalent(jsData, pyData);

      if (equivalent) {
        // Engines agree — test passes
        return;
      }

      // Check each difference against declared divergences
      const undeclaredDiffs = [];
      const declaredDiffs = [];

      for (const diff of differences) {
        const key = divergenceKey(fixtureId, diff.field);
        const declaration = divergenceIndex.get(key);

        if (declaration) {
          declaredDiffs.push({ diff, declaration });
        } else {
          undeclaredDiffs.push(diff);
        }
      }

      // Log declared divergences as diagnostics
      for (const { diff, declaration } of declaredDiffs) {
        // Diagnostic output for declared divergences (visible in verbose test output)
        process.stdout.write(
          `    [declared divergence] ${fixtureId} / ${diff.field}: ${declaration.reason} (resolution: ${declaration.resolution})\n`
        );
      }

      // Fail on undeclared differences
      if (undeclaredDiffs.length > 0) {
        const messages = undeclaredDiffs.map(diff => {
          const snippet = formatDeclarationSnippet(fixtureId, diff.field, diff.actual, diff.baseline);
          return [
            `  Fixture: ${fixtureId}`,
            `  Field:   ${diff.field}`,
            `  JS:      ${JSON.stringify(diff.actual)}`,
            `  PY:      ${JSON.stringify(diff.baseline)}`,
            `  Paste this into test/parity/accepted-divergences.json "entries" array:`,
            `  ${snippet}`,
          ].join('\n');
        });

        assert.fail(
          `${undeclaredDiffs.length} undeclared parity divergence(s) in fixture "${fixtureId}":\n\n` +
          messages.join('\n\n')
        );
      }
    });
  }
});
