/**
 * Index builders for the Ghana Location Correction Engine.
 *
 * Exports: buildPhoneticIndex, buildInitialsIndex,
 *          STOPWORDS, TITLE_PREFIXES, COMMON_BLOCK
 */

'use strict';

const { ALL_PERSONS } = require('./persons-dataset');
const { ALL_MPS } = require('./mps-dataset');
const { ALL_PARTIES } = require('./parties-dataset');
const { phoneticKey } = require('./normalize');
const {
  buildDataset,
  SUPPLEMENTARY_LOCATIONS,
  getAllCanonicals,
} = require('./dataset-builder');

// ---------------------------------------------------------------------------
// Stopwords — never correct these even if they fuzzy-match a location
// ---------------------------------------------------------------------------

const STOPWORDS = new Set([
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
]);

// ---------------------------------------------------------------------------
// COMMON_BLOCK — additional common words that are close to Ghana locations
// (module scope, as required by Task 7.4)
// ---------------------------------------------------------------------------

const COMMON_BLOCK = new Set([
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
]);

// ---------------------------------------------------------------------------
// Title prefixes
// ---------------------------------------------------------------------------

const TITLE_PREFIXES = new Set([
  'honorable', 'honourable', 'hon', 'hon.', 'rt', 'rt.',
  'minister', 'speaker', 'president', 'vice',
  'dr', 'dr.', 'prof', 'prof.', 'justice', 'chief',
  'madam', 'mr', 'mr.', 'mrs', 'mrs.', 'alhaji', 'nana',
]);

// ---------------------------------------------------------------------------
// Phonetic index
// ---------------------------------------------------------------------------

/** @type {Map<string, string[]>|null} phonetic key → canonical names */
let _phoneticIndex = null;

function buildPhoneticIndex() {
  if (_phoneticIndex) return;
  buildDataset();
  _phoneticIndex = new Map();
  for (const canonical of getAllCanonicals()) {
    const key = phoneticKey(canonical);
    if (!_phoneticIndex.has(key)) _phoneticIndex.set(key, []);
    _phoneticIndex.get(key).push(canonical);
  }
  // Also index all aliases from supplementary locations
  for (const loc of SUPPLEMENTARY_LOCATIONS) {
    if (loc.aliases) {
      for (const alias of loc.aliases) {
        const key = phoneticKey(alias);
        if (!_phoneticIndex.has(key)) _phoneticIndex.set(key, []);
        const canonical = loc.canonical;
        if (!_phoneticIndex.get(key).includes(canonical)) {
          _phoneticIndex.get(key).push(canonical);
        }
      }
    }
  }
  // Index aliases from persons (presidents, ministers, speakers)
  for (const person of ALL_PERSONS) {
    if (person.aliases) {
      for (const alias of person.aliases) {
        const key = phoneticKey(alias);
        if (!_phoneticIndex.has(key)) _phoneticIndex.set(key, []);
        if (!_phoneticIndex.get(key).includes(person.canonical)) {
          _phoneticIndex.get(key).push(person.canonical);
        }
      }
    }
  }
  // Index aliases from MPs
  for (const mp of ALL_MPS) {
    if (mp.aliases) {
      for (const alias of mp.aliases) {
        const key = phoneticKey(alias);
        if (!_phoneticIndex.has(key)) _phoneticIndex.set(key, []);
        if (!_phoneticIndex.get(key).includes(mp.name)) {
          _phoneticIndex.get(key).push(mp.name);
        }
      }
    }
  }
  // Index aliases from parties
  for (const party of ALL_PARTIES) {
    if (party.aliases) {
      for (const alias of party.aliases) {
        const key = phoneticKey(alias);
        if (!_phoneticIndex.has(key)) _phoneticIndex.set(key, []);
        if (!_phoneticIndex.get(key).includes(party.canonical)) {
          _phoneticIndex.get(key).push(party.canonical);
        }
      }
    }
    if (party.abbr) {
      const key = phoneticKey(party.abbr);
      if (!_phoneticIndex.has(key)) _phoneticIndex.set(key, []);
      if (!_phoneticIndex.get(key).includes(party.canonical)) {
        _phoneticIndex.get(key).push(party.canonical);
      }
    }
  }
}

function getPhoneticIndex() { buildPhoneticIndex(); return _phoneticIndex; }

// ---------------------------------------------------------------------------
// Initials + surname indexes
// ---------------------------------------------------------------------------

/** @type {Map<string, string[]>|null} surname (lowercase) → full canonical names */
let _surnameIndex = null;
/** @type {Map<string, string[]>|null} "initial.surname" → full canonical names */
let _initialSurnameIndex = null;

function buildInitialsIndex() {
  if (_surnameIndex) return;
  buildDataset();
  _surnameIndex = new Map();
  _initialSurnameIndex = new Map();

  const allEntries = [...ALL_PERSONS, ...ALL_MPS];
  for (const entry of allEntries) {
    const canonical = entry.canonical || entry.name;
    const parts = canonical.split(/\s+/);
    if (parts.length < 2) continue;

    // Surname = last part (handling hyphenated like "Ofori-Atta")
    const surname = parts[parts.length - 1].toLowerCase();
    if (!_surnameIndex.has(surname)) _surnameIndex.set(surname, []);
    _surnameIndex.get(surname).push(canonical);

    // Also index hyphenated full surnames e.g. "Afenyo-Markin"
    for (const part of parts) {
      if (part.includes('-')) {
        const hyph = part.toLowerCase();
        if (!_surnameIndex.has(hyph)) _surnameIndex.set(hyph, []);
        if (!_surnameIndex.get(hyph).includes(canonical)) {
          _surnameIndex.get(hyph).push(canonical);
        }
      }
    }

    // Build initial+surname keys: "j.rawlings", "j.j.rawlings", "k.ofori-atta"
    const initials = parts.slice(0, -1).map(p => p[0].toLowerCase());
    const surnameKey = surname;
    // Single initial + surname
    for (const ini of initials) {
      const key = ini + '.' + surnameKey;
      if (!_initialSurnameIndex.has(key)) _initialSurnameIndex.set(key, []);
      _initialSurnameIndex.get(key).push(canonical);
    }
    // Multi-initial + surname (e.g. "j.j.rawlings", "j.e.a.mills")
    if (initials.length >= 2) {
      const multiKey = initials.join('.') + '.' + surnameKey;
      if (!_initialSurnameIndex.has(multiKey)) _initialSurnameIndex.set(multiKey, []);
      _initialSurnameIndex.get(multiKey).push(canonical);
    }
  }
}

function getSurnameIndex() { buildInitialsIndex(); return _surnameIndex; }
function getInitialSurnameIndex() { buildInitialsIndex(); return _initialSurnameIndex; }

module.exports = {
  STOPWORDS,
  COMMON_BLOCK,
  TITLE_PREFIXES,
  buildPhoneticIndex,
  getPhoneticIndex,
  buildInitialsIndex,
  getSurnameIndex,
  getInitialSurnameIndex,
};
