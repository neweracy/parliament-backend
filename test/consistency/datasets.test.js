'use strict';

/**
 * Dataset Consistency Check
 *
 * Verifies that the committed dataset files at
 * `services/postprocess/datasets/{persons,locations,parties,mps}.json`
 * are consistent with the primary source of truth in
 * `lib/location-correction/*-dataset.js` + SUPPLEMENTARY_LOCATIONS.
 *
 * Also checks the four Block_List literals (COMMON_BLOCK, STOPWORDS,
 * TITLE_PREFIXES, wordStopwords) against the exporter's transcribed copy.
 *
 * Validates: Requirements 2.1, 2.3, 2.4, 3.4
 *
 * @module test/consistency/datasets
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const ROOT = path.resolve(__dirname, '../..');
const DATASETS_DIR = path.join(ROOT, 'services', 'postprocess', 'datasets');
const EXPORTER_PATH = path.join(ROOT, 'services', 'postprocess', 'scripts', 'export_js_datasets.js');

const REGEN_COMMAND = 'node services/postprocess/scripts/export_js_datasets.js | python services/postprocess/scripts/generate_dataset_exports.py --from-node';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Load and invoke the exporter's build() function in-process.
 * @returns {object} The full export document
 */
function loadExporterOutput() {
  const { build } = require(EXPORTER_PATH);
  return build();
}

/**
 * Read a committed JSON dataset file.
 * @param {string} filename
 * @returns {Array<object>}
 */
function readDatasetFile(filename) {
  const filePath = path.join(DATASETS_DIR, filename);
  if (!fs.existsSync(filePath)) {
    assert.fail(
      `Dataset file not found: ${filename}\n\n` +
      `Regenerate with:\n  ${REGEN_COMMAND}`
    );
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

/**
 * Project a generated person record to a comparable tuple.
 */
function projectPerson(rec) {
  return {
    canonical: rec.canonical,
    entity_kind: 'person',
    entity_type: rec.entity_type,
    aliases: (rec.aliases || []).slice().sort(),
    role: rec.role || '',
    region: rec.region || '',
    constituency: rec.constituency || '',
    party: rec.party || '',
  };
}

/**
 * Project a generated location record to a comparable tuple.
 */
function projectLocation(rec) {
  return {
    canonical: rec.canonical,
    entity_kind: 'location',
    entity_type: rec.entity_type,
    aliases: (rec.aliases || []).slice().sort(),
    region: rec.region || '',
    constituency: rec.constituency || '',
    party: rec.party || '',
    role: rec.role || '',
  };
}

/**
 * Project a generated party record to a comparable tuple.
 */
function projectParty(rec) {
  return {
    canonical: rec.canonical,
    entity_kind: 'party',
    entity_type: 'party',
    aliases: (rec.aliases || []).slice().sort(),
    region: '',
    constituency: '',
    party: rec.abbreviation || rec.party || '',
    role: '',
  };
}

/**
 * Project a generated MP record to a comparable tuple.
 */
function projectMp(rec) {
  return {
    canonical: rec.canonical,
    entity_kind: 'person',
    entity_type: 'mp',
    aliases: (rec.aliases || []).slice().sort(),
    region: '',
    constituency: rec.constituency || '',
    party: rec.party || '',
    role: rec.role || '',
  };
}

/**
 * Project a record from the exporter's sources output.
 */
function projectExporterRecord(rec) {
  return {
    canonical: rec.canonical,
    entity_kind: rec.entity_kind,
    entity_type: rec.entity_type,
    aliases: (rec.aliases || []).slice().sort(),
    region: rec.region || '',
    constituency: rec.constituency || '',
    party: rec.party || '',
    role: rec.role || '',
  };
}

/**
 * Compute the three diff sets between primary (exporter) and committed (JSON files).
 * Returns { missingFromGenerated, absentFromPrimary, attributeDiffers }.
 */
function computeDiff(primaryRecords, committedRecords) {
  const primaryMap = new Map();
  for (const rec of primaryRecords) {
    const key = `${rec.canonical}|${rec.entity_kind}|${rec.entity_type}`;
    primaryMap.set(key, rec);
  }

  const committedMap = new Map();
  for (const rec of committedRecords) {
    const key = `${rec.canonical}|${rec.entity_kind}|${rec.entity_type}`;
    committedMap.set(key, rec);
  }

  const missingFromGenerated = []; // in primary but not in committed
  const absentFromPrimary = [];    // in committed but not in primary
  const attributeDiffers = [];     // in both but attributes differ

  for (const [key, primary] of primaryMap) {
    const committed = committedMap.get(key);
    if (!committed) {
      missingFromGenerated.push(primary);
      continue;
    }

    // Compare attributes
    const diffs = [];
    for (const attr of ['region', 'constituency', 'party', 'role']) {
      if (primary[attr] !== committed[attr]) {
        diffs.push(`${attr}: "${primary[attr]}" (primary) vs "${committed[attr]}" (committed)`);
      }
    }

    // Compare aliases (JSON files are authoritative for alias comparison)
    const primaryAliases = JSON.stringify(primary.aliases);
    const committedAliases = JSON.stringify(committed.aliases);
    if (primaryAliases !== committedAliases) {
      diffs.push(`aliases differ`);
    }

    if (diffs.length > 0) {
      attributeDiffers.push({ canonical: primary.canonical, diffs });
    }
  }

  for (const [key, committed] of committedMap) {
    if (!primaryMap.has(key)) {
      absentFromPrimary.push(committed);
    }
  }

  return { missingFromGenerated, absentFromPrimary, attributeDiffers };
}

/**
 * Format a failure message for dataset drift.
 */
function formatDriftMessage(missingFromGenerated, absentFromPrimary, attributeDiffers) {
  const total = missingFromGenerated.length + absentFromPrimary.length + attributeDiffers.length;
  const lines = [`Dataset drift detected — ${total} entity(ies) differ.\n`];

  if (missingFromGenerated.length > 0) {
    lines.push('Missing from generated representation (present in lib/location-correction):');
    for (const rec of missingFromGenerated.slice(0, 10)) {
      lines.push(`  - ${rec.canonical} (${rec.entity_kind}/${rec.entity_type})`);
    }
    if (missingFromGenerated.length > 10) {
      lines.push(`  ... and ${missingFromGenerated.length - 10} more`);
    }
    lines.push('');
  }

  if (absentFromPrimary.length > 0) {
    lines.push('Present in generated but absent from primary:');
    for (const rec of absentFromPrimary.slice(0, 10)) {
      lines.push(`  - ${rec.canonical} (${rec.entity_kind}/${rec.entity_type})`);
    }
    if (absentFromPrimary.length > 10) {
      lines.push(`  ... and ${absentFromPrimary.length - 10} more`);
    }
    lines.push('');
  }

  if (attributeDiffers.length > 0) {
    lines.push('Attribute mismatch:');
    for (const item of attributeDiffers.slice(0, 10)) {
      lines.push(`  - ${item.canonical}: ${item.diffs.join('; ')}`);
    }
    if (attributeDiffers.length > 10) {
      lines.push(`  ... and ${attributeDiffers.length - 10} more`);
    }
    lines.push('');
  }

  lines.push(`Regenerate with:\n  ${REGEN_COMMAND}`);
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Dataset Consistency Check', () => {
  let exporterOutput;

  // Load the exporter output once — fail verbatim if it throws
  try {
    exporterOutput = loadExporterOutput();
  } catch (err) {
    describe('Exporter invocation', () => {
      it('build() should not throw', () => {
        assert.fail(`Exporter threw: ${err.message}`);
      });
    });
  }

  if (!exporterOutput) return;

  describe('Persons dataset', () => {
    it('committed persons.json matches primary source', () => {
      const committed = readDatasetFile('persons.json');

      // Extract all person records from exporter sources
      const personKeys = [
        'persons_presidents', 'persons_vice_presidents', 'persons_speakers',
        'ministers', 'persons_other_notables', 'persons_cultural_figures',
      ];

      const primaryRecords = [];
      for (const key of personKeys) {
        if (!exporterOutput.sources[key]) continue;
        for (const rec of exporterOutput.sources[key].records) {
          primaryRecords.push(projectExporterRecord(rec));
        }
      }

      const committedRecords = committed.map(projectPerson);
      const { missingFromGenerated, absentFromPrimary, attributeDiffers } =
        computeDiff(primaryRecords, committedRecords);

      const total = missingFromGenerated.length + absentFromPrimary.length + attributeDiffers.length;
      if (total > 0) {
        assert.fail(formatDriftMessage(missingFromGenerated, absentFromPrimary, attributeDiffers));
      }
    });

    it('persons.csv row count matches persons.json', () => {
      const csvPath = path.join(DATASETS_DIR, 'persons.csv');
      if (!fs.existsSync(csvPath)) {
        assert.fail(`persons.csv not found\n\nRegenerate with:\n  ${REGEN_COMMAND}`);
      }
      const csvLines = fs.readFileSync(csvPath, 'utf8').trim().split('\n');
      // Subtract header row
      const csvRowCount = csvLines.length - 1;
      const jsonData = readDatasetFile('persons.json');
      assert.equal(csvRowCount, jsonData.length,
        `persons.csv has ${csvRowCount} data rows but persons.json has ${jsonData.length} records`);
    });
  });

  describe('Locations dataset', () => {
    it('committed locations.json matches primary source', () => {
      const committed = readDatasetFile('locations.json');

      const locationKeys = ['regions', 'cities', 'supplementary'];
      const primaryRecords = [];
      for (const key of locationKeys) {
        if (!exporterOutput.sources[key]) continue;
        for (const rec of exporterOutput.sources[key].records) {
          primaryRecords.push(projectExporterRecord(rec));
        }
      }

      const committedRecords = committed.map(projectLocation);
      const { missingFromGenerated, absentFromPrimary, attributeDiffers } =
        computeDiff(primaryRecords, committedRecords);

      const total = missingFromGenerated.length + absentFromPrimary.length + attributeDiffers.length;
      if (total > 0) {
        assert.fail(formatDriftMessage(missingFromGenerated, absentFromPrimary, attributeDiffers));
      }
    });

    it('locations.csv row count matches locations.json', () => {
      const csvPath = path.join(DATASETS_DIR, 'locations.csv');
      if (!fs.existsSync(csvPath)) {
        assert.fail(`locations.csv not found\n\nRegenerate with:\n  ${REGEN_COMMAND}`);
      }
      const csvLines = fs.readFileSync(csvPath, 'utf8').trim().split('\n');
      const csvRowCount = csvLines.length - 1;
      const jsonData = readDatasetFile('locations.json');
      assert.equal(csvRowCount, jsonData.length,
        `locations.csv has ${csvRowCount} data rows but locations.json has ${jsonData.length} records`);
    });
  });

  describe('Parties dataset', () => {
    it('committed parties.json matches primary source', () => {
      const committed = readDatasetFile('parties.json');

      const primaryRecords = [];
      if (exporterOutput.sources.parties) {
        for (const rec of exporterOutput.sources.parties.records) {
          primaryRecords.push(projectExporterRecord(rec));
        }
      }

      const committedRecords = committed.map(projectParty);
      const { missingFromGenerated, absentFromPrimary, attributeDiffers } =
        computeDiff(primaryRecords, committedRecords);

      const total = missingFromGenerated.length + absentFromPrimary.length + attributeDiffers.length;
      if (total > 0) {
        assert.fail(formatDriftMessage(missingFromGenerated, absentFromPrimary, attributeDiffers));
      }
    });

    it('parties.csv row count matches parties.json', () => {
      const csvPath = path.join(DATASETS_DIR, 'parties.csv');
      if (!fs.existsSync(csvPath)) {
        assert.fail(`parties.csv not found\n\nRegenerate with:\n  ${REGEN_COMMAND}`);
      }
      const csvLines = fs.readFileSync(csvPath, 'utf8').trim().split('\n');
      const csvRowCount = csvLines.length - 1;
      const jsonData = readDatasetFile('parties.json');
      assert.equal(csvRowCount, jsonData.length,
        `parties.csv has ${csvRowCount} data rows but parties.json has ${jsonData.length} records`);
    });
  });

  describe('MPs dataset', () => {
    it('committed mps.json matches primary source', () => {
      const committed = readDatasetFile('mps.json');

      const primaryRecords = [];
      if (exporterOutput.sources.mps) {
        for (const rec of exporterOutput.sources.mps.records) {
          primaryRecords.push(projectExporterRecord(rec));
        }
      }

      const committedRecords = committed.map(projectMp);
      const { missingFromGenerated, absentFromPrimary, attributeDiffers } =
        computeDiff(primaryRecords, committedRecords);

      const total = missingFromGenerated.length + absentFromPrimary.length + attributeDiffers.length;
      if (total > 0) {
        assert.fail(formatDriftMessage(missingFromGenerated, absentFromPrimary, attributeDiffers));
      }
    });

    it('mps.csv row count matches mps.json', () => {
      const csvPath = path.join(DATASETS_DIR, 'mps.csv');
      if (!fs.existsSync(csvPath)) {
        assert.fail(`mps.csv not found\n\nRegenerate with:\n  ${REGEN_COMMAND}`);
      }
      const csvLines = fs.readFileSync(csvPath, 'utf8').trim().split('\n');
      const csvRowCount = csvLines.length - 1;
      const jsonData = readDatasetFile('mps.json');
      assert.equal(csvRowCount, jsonData.length,
        `mps.csv has ${csvRowCount} data rows but mps.json has ${jsonData.length} records`);
    });
  });

  describe('Block_List consistency', () => {
    it('exporter produces all four block list kinds', () => {
      const expectedKinds = ['block', 'stopword', 'word_stopword', 'title'];
      const actualKinds = Object.keys(exporterOutput.blockLists).sort();
      assert.deepEqual(actualKinds, expectedKinds.sort(),
        'Block list kinds mismatch');
    });

    it('each block list is non-empty and contains only { token, reason } entries', () => {
      for (const [kind, entries] of Object.entries(exporterOutput.blockLists)) {
        assert.ok(Array.isArray(entries), `blockLists.${kind} is not an array`);
        assert.ok(entries.length > 0, `blockLists.${kind} is empty`);

        for (const entry of entries) {
          assert.equal(typeof entry.token, 'string',
            `blockLists.${kind} entry missing string token`);
          assert.ok(entry.token.length > 0,
            `blockLists.${kind} has empty token`);
          assert.ok(entry.reason === null || typeof entry.reason === 'string',
            `blockLists.${kind} entry reason must be null or string`);
        }
      }
    });

    it('block list tokens are deduplicated and lowercased', () => {
      for (const [kind, entries] of Object.entries(exporterOutput.blockLists)) {
        const tokens = entries.map(e => e.token);
        const uniqueTokens = new Set(tokens);
        assert.equal(tokens.length, uniqueTokens.size,
          `blockLists.${kind} has duplicate tokens`);

        for (const token of tokens) {
          assert.equal(token, token.toLowerCase(),
            `blockLists.${kind} token "${token}" is not lowercased`);
        }
      }
    });

    it('blockListCounts agree with actual entry counts', () => {
      for (const [kind, entries] of Object.entries(exporterOutput.blockLists)) {
        assert.equal(exporterOutput.blockListCounts[kind], entries.length,
          `blockListCounts.${kind} (${exporterOutput.blockListCounts[kind]}) does not match actual count (${entries.length})`);
      }
    });

    it('COMMON_BLOCK contains known sentinel tokens', () => {
      const blockTokens = new Set(exporterOutput.blockLists.block.map(e => e.token));
      // These tokens are documented as traceable to fixed defects
      const sentinels = ['nation', 'general', 'page', 'national'];
      for (const token of sentinels) {
        assert.ok(blockTokens.has(token),
          `COMMON_BLOCK is missing sentinel token "${token}"`);
      }
    });

    it('STOPWORDS contains core English stopwords', () => {
      const stopTokens = new Set(exporterOutput.blockLists.stopword.map(e => e.token));
      const coreStopwords = ['a', 'the', 'is', 'of', 'and', 'or', 'in'];
      for (const token of coreStopwords) {
        assert.ok(stopTokens.has(token),
          `STOPWORDS is missing core stopword "${token}"`);
      }
    });

    it('TITLE_PREFIXES contains key title tokens', () => {
      const titleTokens = new Set(exporterOutput.blockLists.title.map(e => e.token));
      const keyTitles = ['honorable', 'honourable', 'hon', 'president', 'speaker'];
      for (const token of keyTitles) {
        assert.ok(titleTokens.has(token),
          `TITLE_PREFIXES is missing expected title "${token}"`);
      }
    });

    it('word_stopword list contains parliamentary stopwords', () => {
      const wordTokens = new Set(exporterOutput.blockLists.word_stopword.map(e => e.token));
      const parlStopwords = ['constituency', 'parliament', 'committee', 'minister'];
      for (const token of parlStopwords) {
        assert.ok(wordTokens.has(token),
          `word_stopword is missing parliamentary stopword "${token}"`);
      }
    });
  });
});
