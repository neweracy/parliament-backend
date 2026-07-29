/**
 * Export the JavaScript correction datasets as a single JSON document.
 *
 * `lib/location-correction/*-dataset.js` and the SUPPLEMENTARY_LOCATIONS array
 * in `lib/location-correction/index.js` stay the single source of truth for
 * Ghana entities. This script reads them through `require` and prints one JSON
 * document on stdout so `scripts/migrate_js_datasets.py` never hand-transcribes
 * a record.
 *
 * Emitted shape:
 *
 *   {
 *     "sources": {
 *       "<key>": { "rank": N, "source": "<db source label>", "records": [ ... ] }
 *     },
 *     "counts":  { "<key>": N, ... },
 *     "aliasCounts": { "<key>": N, ... },
 *     "kindCounts": { "location": N, "person": N, "party": N },
 *     "subArrayCheck": { "sum": N, "allPersons": N, "match": true, "subArrays": {...} },
 *     "blockLists": {
 *       "block":         [ { "token": "...", "reason": "..."|null }, ... ],
 *       "stopword":      [ ... ],
 *       "word_stopword": [ ... ],
 *       "title":         [ ... ]
 *     },
 *     "blockListCounts": { "block": N, "stopword": N, "word_stopword": N, "title": N }
 *   }
 *
 * Every record carries its own `source` and `source_rank` so the Python loader
 * can reproduce `buildDataset()` insertion order exactly:
 *
 *   regions → cities → supplementary → PRESIDENTS → VICE_PRESIDENTS →
 *   SPEAKERS → ALL_MINISTERS → OTHER_NOTABLES → CULTURAL_FIGURES →
 *   ALL_MPS → ALL_PARTIES
 *
 * The minister rank sits between SPEAKERS and OTHER_NOTABLES because
 * `persons-dataset.js` spreads `...MINISTERS` into `ALL_PERSONS` at that
 * position, and person aliases are last-wins: loading ministers out of order
 * would let a later source reclaim an alias that JavaScript gives to an
 * earlier one.
 *
 * Usage: node services/postprocess/scripts/export_js_datasets.js
 */

'use strict';

const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const LC = path.join(REPO_ROOT, 'lib', 'location-correction');

const { getRegions } = require('ghana-locations');
const persons = require(path.join(LC, 'persons-dataset'));
const { ALL_MINISTERS } = require(path.join(LC, 'ministers-dataset'));
const { ALL_MPS } = require(path.join(LC, 'mps-dataset'));
const { ALL_PARTIES } = require(path.join(LC, 'parties-dataset'));
const { SUPPLEMENTARY_LOCATIONS } = require(path.join(LC, 'index'));

// ---------------------------------------------------------------------------
// Source ranks — buildDataset() insertion order
// ---------------------------------------------------------------------------

const RANKS = {
  regions: 10,
  cities: 20,
  supplementary: 30,
  persons_presidents: 40,
  persons_vice_presidents: 41,
  persons_speakers: 42,
  ministers: 43,
  persons_other_notables: 44,
  persons_cultural_figures: 45,
  mps: 50,
  parties: 60,
};

/**
 * Person sub-arrays other than MINISTERS, with their entity_type and rank.
 *
 * `entityType` values come from the EntityType enum in
 * `services/postprocess/app/models/entities.py` — no new value is invented
 * here. Heads of state, VPs, and Speakers map to `president`; the remaining
 * office-holders and notables map to `minister`, which is the enum's generic
 * bucket for a non-head-of-state person who is not an MP.
 */
const PERSON_SUB_ARRAYS = [
  { key: 'persons_presidents', array: 'PRESIDENTS', entityType: 'president' },
  { key: 'persons_vice_presidents', array: 'VICE_PRESIDENTS', entityType: 'president' },
  { key: 'persons_speakers', array: 'SPEAKERS', entityType: 'president' },
  { key: 'persons_other_notables', array: 'OTHER_NOTABLES', entityType: 'minister' },
  { key: 'persons_cultural_figures', array: 'CULTURAL_FIGURES', entityType: 'minister' },
];

// ---------------------------------------------------------------------------
// Record builders
// ---------------------------------------------------------------------------

/**
 * Builds one entity_record row.
 * @returns {object}
 */
function record(sourceKey, canonical, entityKind, entityType, extra) {
  const e = extra || {};
  return {
    canonical,
    entity_kind: entityKind,
    entity_type: entityType,
    aliases: Array.isArray(e.aliases) ? e.aliases.slice() : [],
    region: e.region || null,
    constituency: e.constituency || null,
    party: e.party || null,
    role: e.role || null,
    active: true,
    source: sourceKey,
    source_rank: RANKS[sourceKey],
  };
}

/** @returns {{ regions: object[], cities: object[] }} */
function buildLocationRecords() {
  const regionRecords = [];
  const cityRecords = [];

  for (const region of getRegions()) {
    regionRecords.push(record('regions', region.name, 'location', 'region', {}));
    for (const city of region.cities || []) {
      cityRecords.push(
        record('cities', city, 'location', 'city', { region: region.name }),
      );
    }
  }

  return { regions: regionRecords, cities: cityRecords };
}

/** @returns {object[]} */
function buildSupplementaryRecords() {
  return SUPPLEMENTARY_LOCATIONS.map(loc =>
    record('supplementary', loc.canonical, 'location', 'supplementary', {
      aliases: loc.aliases,
      // `Ghana` carries region: '' — normalize the empty string to null.
      region: loc.region || null,
    }),
  );
}

/** @returns {object[]} */
function buildPersonRecords(sourceKey, entries, entityType) {
  return entries.map(person =>
    record(sourceKey, person.canonical, 'person', entityType, {
      aliases: person.aliases,
      role: person.role,
    }),
  );
}

/** @returns {object[]} */
function buildMinisterRecords() {
  return ALL_MINISTERS.map(minister =>
    record('ministers', minister.canonical, 'person', 'minister', {
      aliases: minister.aliases,
      role: minister.role,
    }),
  );
}

/** @returns {object[]} */
function buildMpRecords() {
  return ALL_MPS.map(mp =>
    record('mps', mp.name, 'person', 'mp', {
      aliases: mp.aliases,
      constituency: mp.constituency,
      party: mp.party,
      role: mp.role,
    }),
  );
}

/**
 * Party abbreviations become both an alias (so "NDC" resolves to the full
 * name) and the `party` attribute (so the display heuristic can choose
 * between abbreviation and canonical). buildDataset() inserts the
 * abbreviation before the remaining aliases, so the ordinal order matches.
 * @returns {object[]}
 */
function buildPartyRecords() {
  return ALL_PARTIES.map(party => {
    const aliases = [];
    if (party.abbr) aliases.push(party.abbr);
    for (const alias of party.aliases || []) {
      if (!aliases.includes(alias)) aliases.push(alias);
    }
    return record('parties', party.canonical, 'party', 'party', {
      aliases,
      party: party.abbr || null,
    });
  });
}

/**
 * Collapses records that share a `(canonical, entity_kind)` pair within one
 * source, because `(canonical, entity_kind, source)` is the natural key of
 * `entity_record` and a second row would silently overwrite the first.
 *
 * `ministers-dataset.js` legitimately lists the same person under more than
 * one portfolio (17 cases today), so this is expected rather than a defect.
 * The merge is first-wins on attributes — matching `addEntry()`, which keeps
 * the first claim on a canonical — and append-in-order on aliases, so the
 * alias ordinals still reflect JavaScript insertion order.
 *
 * @returns {{ records: object[], merged: number }}
 */
function dedupeWithinSource(records) {
  /** @type {Map<string, object>} */
  const byKey = new Map();
  let merged = 0;

  for (const rec of records) {
    const key = `${rec.canonical.toLowerCase()}|${rec.entity_kind}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, rec);
      continue;
    }
    merged += 1;
    for (const alias of rec.aliases) {
      if (!existing.aliases.includes(alias)) existing.aliases.push(alias);
    }
    // First-wins on attributes; fill only what the first record left empty.
    for (const field of ['region', 'constituency', 'party', 'role']) {
      if (existing[field] === null && rec[field] !== null) {
        existing[field] = rec[field];
      }
    }
  }

  return { records: Array.from(byKey.values()), merged };
}

// ---------------------------------------------------------------------------
// Block_List seeds
// ---------------------------------------------------------------------------
//
// None of the four suppression sets is reachable through `require`:
//
//   - COMMON_BLOCK  is declared INSIDE `matchFuzzy()` in
//                   lib/location-correction/index.js (~line 457), so it is
//                   function-local.
//   - wordStopwords is declared INSIDE `formatTranscriptionResponse()` in
//                   server.js (~line 277), so it is function-local.
//   - STOPWORDS     is a module-level Set in lib/location-correction/index.js
//                   (~line 730) but is NOT in that module's `module.exports`.
//   - TITLE_PREFIXES is a module-level Set in the same file (~line 634) and is
//                   likewise NOT exported.
//
// They are therefore transcribed here verbatim from those sources. `lib/` and
// `server.js` are not modified by this migration, so the literals below are the
// only way to read the current values. Keep them in sync if either source
// changes; the migration is the seed, and after seeding the Dataset_Store is
// authoritative (Requirement 4.10).

/** Source: lib/location-correction/index.js — COMMON_BLOCK, inside matchFuzzy(). */
const COMMON_BLOCK = [
  'nation', 'nations', 'national', 'natural', 'nature', 'morning',
  'market', 'master', 'matter', 'member', 'members', 'minister',
  'minute', 'mission', 'modern', 'motion', 'moving', 'number',
  'office', 'officer', 'option', 'order', 'paper', 'parent',
  'period', 'person', 'place', 'plant', 'point', 'police',
  'policy', 'position', 'power', 'present', 'president', 'process',
  'program', 'project', 'public', 'question', 'reason', 'record',
  'report', 'result', 'right', 'school', 'second', 'section',
  'service', 'session', 'south', 'space', 'speaker', 'special',
  'state', 'story', 'street', 'strong', 'student', 'system',
  'table', 'total', 'trade', 'water', 'woman', 'women', 'world',
  'written', 'young', 'winter', 'letter', 'better', 'little',
  'bottom', 'button', 'cotton', 'gotten', 'kitten', 'mitten',
  'rotten', 'sudden', 'hidden',
  // Words commonly confused with Ghana locations by ASR output
  'general', 'central', 'several', 'mineral', 'liberal', 'federal',
  'winnable', 'winneba', 'winners', 'winning',
  'abandon', 'abandoned', 'substance',
  'jiraffe', 'giraffe',
  'canton', 'nanton', 'phantom', 'mansion',
  'page', 'paper', 'pager', 'stage', 'wage',
  // Personal names commonly seen in parliamentary transcripts that
  // must NOT be fuzzy-matched into Ghana location names
  'bibi', 'kabu', 'kabo', 'kobi',
];

/** Source: lib/location-correction/index.js — module-level STOPWORDS Set. */
const STOPWORDS = [
  'a', 'an', 'the', 'in', 'on', 'at', 'to', 'of', 'is', 'are', 'was', 'were',
  'be', 'been', 'and', 'or', 'but', 'for', 'by', 'with', 'from', 'this',
  'that', 'these', 'those', 'it', 'its', 'i', 'we', 'they', 'he', 'she',
  'his', 'her', 'their', 'our', 'my', 'your', 'as', 'so', 'if', 'not',
  'also', 'about', 'after', 'again', 'all', 'any', 'before', 'being',
  'between', 'both', 'call', 'came', 'can', 'come', 'could', 'day', 'did',
  'do', 'does', 'down', 'each', 'even', 'every', 'first', 'get', 'give',
  'going', 'good', 'had', 'has', 'have', 'here', 'him', 'how', 'into',
  'just', 'know', 'last', 'like', 'made', 'make', 'many', 'may', 'might',
  'more', 'most', 'much', 'must', 'never', 'new', 'next', 'no', 'now',
  'off', 'old', 'once', 'one', 'only', 'other', 'out', 'over', 'own',
  'people', 'said', 'same', 'see', 'shall', 'should', 'since', 'some',
  'still', 'summer', 'take', 'than', 'them', 'then', 'there', 'thing',
  'think', 'through', 'time', 'today', 'together', 'too', 'under', 'until',
  'up', 'us', 'very', 'want', 'way', 'week', 'well', 'went', 'what', 'when',
  'where', 'which', 'while', 'who', 'why', 'will', 'without', 'would',
  'year', 'years', 'yet', 'you',
  // Common verbs that follow person names — prevent title-person greedy consumption
  'led', 'served', 'took', 'left', 'won', 'lost', 'saw', 'passed',
  'died', 'ruled', 'governed', 'visited', 'announced', 'declared',
  'became', 'returned', 'started', 'began', 'ended', 'signed',
  // Common words that must not be fuzzy-matched to entities
  'general', 'attorney', 'justice', 'deputy', 'minister', 'leader',
];

/** Source: server.js — wordStopwords Set, inside formatTranscriptionResponse(). */
const WORD_STOPWORDS = [
  'a', 'an', 'the', 'in', 'on', 'at', 'to', 'of', 'is', 'are', 'was', 'were',
  'be', 'and', 'or', 'but', 'for', 'by', 'with', 'from', 'this', 'that', 'it',
  'he', 'she', 'they', 'we',
  'his', 'her', 'their', 'our', 'my', 'your', 'as', 'so', 'if', 'not',
  'through', 'has', 'had', 'have',
  'constituency', 'traditional', 'area', 'alongside', 'among', 'these',
  'those', 'region',
  'district', 'municipal', 'metropolitan', 'assembly', 'parliament', 'bill',
  'motion',
  'committee', 'minister', 'speaker', 'members', 'distinguished',
  'general', 'attorney', 'justice', 'deputy', 'leader', 'majority', 'minority',
  'page', 'paper', 'order', 'number', 'same',
];

/** Source: lib/location-correction/index.js — module-level TITLE_PREFIXES Set. */
const TITLE_PREFIXES = [
  'honorable', 'honourable', 'hon', 'hon.', 'rt', 'rt.',
  'minister', 'speaker', 'president', 'vice',
  'dr', 'dr.', 'prof', 'prof.', 'justice', 'chief',
  'madam', 'mr', 'mr.', 'mrs', 'mrs.', 'alhaji', 'nana',
];

/**
 * Tokens whose presence in a suppression set traces to a specific fixed
 * defect. The `reason` rides into `block_list.reason` so a later reader can
 * see why removing the token would regress a known case.
 */
const BLOCK_REASONS = {
  general: 'fixed defect: over-corrected to a Ghana location',
  page: 'fixed defect: over-corrected to a Ghana location',
  nation: 'fixed defect: over-corrected to a Ghana location',
  national: 'fixed defect: over-corrected to a Ghana location',
};

/**
 * Deduplicates a seed list, lowercases every token, and attaches the defect
 * reason where one is recorded. Order is preserved so `block_list` seeding is
 * reproducible.
 * @returns {Array<{ token: string, reason: string|null }>}
 */
function buildBlockListEntries(tokens) {
  const seen = new Set();
  const entries = [];
  for (const raw of tokens) {
    const token = String(raw).toLowerCase();
    if (seen.has(token)) continue;
    seen.add(token);
    entries.push({ token, reason: BLOCK_REASONS[token] || null });
  }
  return entries;
}

/** @returns {Record<string, Array<{ token: string, reason: string|null }>>} */
function buildBlockLists() {
  return {
    block: buildBlockListEntries(COMMON_BLOCK),
    stopword: buildBlockListEntries(STOPWORDS),
    word_stopword: buildBlockListEntries(WORD_STOPWORDS),
    title: buildBlockListEntries(TITLE_PREFIXES),
  };
}

// ---------------------------------------------------------------------------
// Assembly
// ---------------------------------------------------------------------------

function build() {
  const { regions, cities } = buildLocationRecords();

  /** @type {Record<string, object[]>} */
  const recordsByKey = {
    regions,
    cities,
    supplementary: buildSupplementaryRecords(),
  };

  for (const sub of PERSON_SUB_ARRAYS) {
    const entries = persons[sub.array];
    if (!Array.isArray(entries)) {
      throw new Error(
        `persons-dataset.js no longer exports ${sub.array} as an array`,
      );
    }
    recordsByKey[sub.key] = buildPersonRecords(sub.key, entries, sub.entityType);
  }

  recordsByKey.ministers = buildMinisterRecords();
  recordsByKey.mps = buildMpRecords();
  recordsByKey.parties = buildPartyRecords();

  // --- sub-array integrity check -----------------------------------------
  // `MINISTERS` in persons-dataset.js IS `ALL_MINISTERS` — the sum below must
  // equal len(ALL_PERSONS) or the datasets have been restructured and the
  // migration must stop rather than double-load ministers.
  const subArrays = {
    PRESIDENTS: persons.PRESIDENTS.length,
    VICE_PRESIDENTS: persons.VICE_PRESIDENTS.length,
    SPEAKERS: persons.SPEAKERS.length,
    MINISTERS: ALL_MINISTERS.length,
    OTHER_NOTABLES: persons.OTHER_NOTABLES.length,
    CULTURAL_FIGURES: persons.CULTURAL_FIGURES.length,
  };
  const sum = Object.values(subArrays).reduce((a, b) => a + b, 0);
  const allPersons = persons.ALL_PERSONS.length;

  // --- counts -------------------------------------------------------------
  const sources = {};
  const counts = {};
  const aliasCounts = {};
  const kindCounts = { location: 0, person: 0, party: 0 };

  const mergedCounts = {};

  const orderedKeys = Object.keys(RANKS);
  for (const key of orderedKeys) {
    const { records, merged } = dedupeWithinSource(recordsByKey[key] || []);
    sources[key] = { rank: RANKS[key], source: key, records };
    counts[key] = records.length;
    mergedCounts[key] = merged;
    aliasCounts[key] = records.reduce((n, r) => n + r.aliases.length, 0);
    for (const r of records) {
      kindCounts[r.entity_kind] = (kindCounts[r.entity_kind] || 0) + 1;
    }
  }

  // --- Block_List seeds ---------------------------------------------------
  const blockLists = buildBlockLists();
  const blockListCounts = {};
  for (const [listKind, entries] of Object.entries(blockLists)) {
    blockListCounts[listKind] = entries.length;
  }

  return {
    sources,
    counts,
    aliasCounts,
    mergedCounts,
    kindCounts,
    blockLists,
    blockListCounts,
    subArrayCheck: {
      sum,
      allPersons,
      match: sum === allPersons,
      subArrays,
    },
  };
}

module.exports = { build };

if (require.main === module) {
  process.stdout.write(JSON.stringify(build()));
}
