/**
 * Ghana Location Correction Engine (Backend)
 *
 * A multi-strategy correction model that handles:
 * - Fused words ("ningoprampram" → "Ningo-Prampram")
 * - Split words ("pram pram" → "Prampram")
 * - Hyphenated variants ("ningo-prampram" → "Ningo-Prampram")
 * - Spelling mistakes ("Kumase" → "Kumasi", "Accara" → "Accra")
 * - Case normalization ("GREATER ACCRA" → "Greater Accra")
 * - Phonetic similarity ("Koumasi" → "Kumasi")
 *
 * Uses the ghana-locations npm package as base dataset, extended with a
 * supplementary list of constituencies, districts, and commonly referenced
 * sub-localities missing from the base package.
 *
 * Target: ≥95% accuracy on Ghana location name correction in ASR output.
 */

'use strict';

const { getRegions } = require('ghana-locations');
const { ALL_PERSONS } = require('./persons-dataset');
const { ALL_MPS } = require('./mps-dataset');
const { ALL_PARTIES } = require('./parties-dataset');

// ---------------------------------------------------------------------------
// Supplementary locations not in the base package
// ---------------------------------------------------------------------------

/**
 * Supplementary Ghana locations: constituencies, districts, and notable
 * towns/villages frequently mentioned in parliamentary/media transcripts
 * but missing from the ghana-locations npm package.
 *
 * Format: { canonical: string, region: string, aliases?: string[] }
 * Aliases are alternative spellings/forms that should all resolve to canonical.
 */
const SUPPLEMENTARY_LOCATIONS = [
  // Country
  { canonical: 'Ghana', region: '', aliases: ['Gana', 'Ghanna', 'Ghanah'] },
  // Greater Accra
  { canonical: 'Ningo-Prampram', region: 'Greater Accra', aliases: ['Ningo Prampram', 'Ningoprampram', 'Nyungoprampram', 'Ningo Pram Pram', 'Ninggu Pram Pram'] },
  { canonical: 'Ningo', region: 'Greater Accra', aliases: ['New Ningo', 'Old Ningo', 'Ninggu', 'Ningu'] },
  { canonical: 'Tema', region: 'Greater Accra', aliases: ['Tema New Town', 'Tema Manhean'] },
  { canonical: 'Teshie', region: 'Greater Accra', aliases: ['Teshie Nungua'] },
  { canonical: 'Ashaiman', region: 'Greater Accra', aliases: ['Ashiaman', 'Ashaiman Municipal'] },
  { canonical: 'Weija-Gbawe', region: 'Greater Accra', aliases: ['Weija', 'Gbawe'] },
  { canonical: 'Ledzokuku', region: 'Greater Accra', aliases: ['Ledzokuku-Krowor'] },
  { canonical: 'Okaikwei', region: 'Greater Accra', aliases: ['Okaikoi', 'Okaikwei North'] },
  { canonical: 'Ayawaso', region: 'Greater Accra', aliases: ['Ayawaso West', 'Ayawaso East', 'Ayawaso Central', 'Ayawaso North'] },
  { canonical: 'Ablekuma', region: 'Greater Accra', aliases: ['Ablekuma West', 'Ablekuma Central', 'Ablekuma North'] },
  { canonical: 'Korle Klottey', region: 'Greater Accra', aliases: ['Korle-Klottey'] },
  { canonical: 'Kpone Katamanso', region: 'Greater Accra', aliases: ['Kpone-Katamanso', 'Katamanso'] },
  { canonical: 'Ada', region: 'Greater Accra', aliases: ['Ada West', 'Ada East', 'Big Ada', 'Ada Foah'] },
  { canonical: 'Shai Osudoku', region: 'Greater Accra', aliases: ['Shai-Osudoku', 'Dodowa'] },
  // Ashanti
  { canonical: 'Obuasi', region: 'Ashanti', aliases: ['Obuase'] },
  { canonical: 'Ejisu', region: 'Ashanti', aliases: ['Ejisu-Juaben', 'Juaben'] },
  { canonical: 'Bekwai', region: 'Ashanti', aliases: ['Bekwai Municipal'] },
  { canonical: 'Atwima Nwabiagya', region: 'Ashanti', aliases: ['Atwima-Nwabiagya', 'Nwabiagya'] },
  { canonical: 'Asante Akim', region: 'Ashanti', aliases: ['Asante Akyem', 'Asante Akim Central', 'Asante Akim North', 'Asante Akim South'] },
  { canonical: 'Mampong', region: 'Ashanti', aliases: ['Mampong Municipal', 'Asante Mampong'] },
  { canonical: 'Offinso', region: 'Ashanti', aliases: ['Offinso Municipal', 'Offinso North', 'Offinso South'] },
  { canonical: 'Ahafo Ano', region: 'Ashanti', aliases: ['Ahafo Ano North', 'Ahafo Ano South'] },
  { canonical: 'Bosomtwe', region: 'Ashanti', aliases: ['Bosomtwi'] },
  // Central
  { canonical: 'Cape Coast', region: 'Central', aliases: ['Cabo Corso'] },
  { canonical: 'Mankessim', region: 'Central', aliases: ['Manksessim'] },
  { canonical: 'Elmina', region: 'Central', aliases: ['Edina'] },
  { canonical: 'Winneba', region: 'Central', aliases: ['Simpa'] },
  { canonical: 'Kasoa', region: 'Central', aliases: ['Cassoa', 'Kasoa-Ofaakor'] },
  { canonical: 'Agona', region: 'Central', aliases: ['Agona West', 'Agona East', 'Agona Swedru'] },
  { canonical: 'Awutu Senya', region: 'Central', aliases: ['Awutu-Senya', 'Awutu Senya East', 'Awutu Senya West'] },
  // Eastern
  { canonical: 'Koforidua', region: 'Eastern', aliases: ['Koforidua Municipal'] },
  { canonical: 'Nsawam', region: 'Eastern', aliases: ['Nsawam-Adoagyiri', 'Nsawam Adoagyiri'] },
  { canonical: 'Akim Oda', region: 'Eastern', aliases: ['Akim-Oda', 'Oda'] },
  { canonical: 'Nkawkaw', region: 'Eastern', aliases: ['Nkawkaw Municipal'] },
  { canonical: 'Akuapem', region: 'Eastern', aliases: ['Akuapem North', 'Akuapem South', 'Akwapim'] },
  { canonical: 'Suhum', region: 'Eastern', aliases: ['Suhum Municipal'] },
  { canonical: 'Abuakwa', region: 'Eastern', aliases: ['Abuakwa North', 'Abuakwa South'] },
  // Western
  { canonical: 'Sekondi-Takoradi', region: 'Western', aliases: ['Sekondi Takoradi', 'Sekondi', 'Takoradi'] },
  { canonical: 'Tarkwa', region: 'Western', aliases: ['Tarkwa Nsuaem', 'Tarkwa-Nsuaem'] },
  { canonical: 'Axim', region: 'Western', aliases: ['Axim Municipal'] },
  { canonical: 'Prestea', region: 'Western', aliases: ['Prestea Huni-Valley', 'Prestea Huni Valley'] },
  // Northern
  { canonical: 'Tamale', region: 'Northern', aliases: ['Tamale Metropolitan', 'Tamale Metro'] },
  { canonical: 'Yendi', region: 'Northern', aliases: ['Yendi Municipal'] },
  { canonical: 'Tolon', region: 'Northern', aliases: ['Tolon-Kumbungu'] },
  { canonical: 'Savelugu', region: 'Northern', aliases: ['Savelugu Municipal', 'Savelugu-Nanton'] },
  { canonical: 'Sagnarigu', region: 'Northern', aliases: ['Sagnerigu'] },
  // Volta
  { canonical: 'Ho', region: 'Volta', aliases: ['Ho Municipal', 'Ho West'] },
  { canonical: 'Keta', region: 'Volta', aliases: ['Keta Municipal'] },
  { canonical: 'Hohoe', region: 'Volta', aliases: ['Hohoe Municipal'] },
  { canonical: 'Kpando', region: 'Volta', aliases: ['Kpando Municipal'] },
  // Upper East
  { canonical: 'Bolgatanga', region: 'Upper East', aliases: ['Bolga', 'Bolgatanga Municipal'] },
  { canonical: 'Bawku', region: 'Upper East', aliases: ['Bawku Municipal', 'Bawku West'] },
  { canonical: 'Navrongo', region: 'Upper East', aliases: ['Navrongo Municipal', 'Kassena Nankana'] },
  // Upper West
  { canonical: 'Wa', region: 'Upper West', aliases: ['Wa Municipal', 'Wa West', 'Wa East'] },
  { canonical: 'Lawra', region: 'Upper West', aliases: ['Lawra Municipal'] },
  { canonical: 'Jirapa', region: 'Upper West', aliases: ['Jirapa Municipal'] },
  // Bono
  { canonical: 'Sunyani', region: 'Bono', aliases: ['Sunyani Municipal', 'Sunyani West'] },
  { canonical: 'Berekum', region: 'Bono', aliases: ['Berekum Municipal', 'Berekum East'] },
  { canonical: 'Dormaa', region: 'Bono', aliases: ['Dormaa Ahenkro', 'Dormaa Central', 'Dormaa East', 'Dormaa West'] },
  // Bono East
  { canonical: 'Techiman', region: 'Bono East', aliases: ['Techiman Municipal', 'Techiman North', 'Techiman South'] },
  { canonical: 'Kintampo', region: 'Bono East', aliases: ['Kintampo Municipal', 'Kintampo North', 'Kintampo South'] },
  // Savannah
  { canonical: 'Damongo', region: 'Savannah', aliases: ['Damongo District'] },
  { canonical: 'Bole', region: 'Savannah', aliases: ['Bole District', 'Bole Bamboi'] },
  // North East
  { canonical: 'Nalerigu', region: 'North East', aliases: ['Nalerigu-Gambaga'] },
  { canonical: 'Walewale', region: 'North East', aliases: ['Walewale Municipal'] },
  // Oti
  { canonical: 'Dambai', region: 'Oti', aliases: ['Dambai Municipal'] },
  { canonical: 'Nkwanta', region: 'Oti', aliases: ['Nkwanta South', 'Nkwanta North'] },
  // Ahafo
  { canonical: 'Goaso', region: 'Ahafo', aliases: ['Goaso Municipal'] },
  { canonical: 'Bechem', region: 'Ahafo', aliases: ['Bechem Municipal'] },
];

// ---------------------------------------------------------------------------
// Dataset construction — merged, normalized, indexed
// ---------------------------------------------------------------------------

/** @type {Map<string, string>} lowercase normalized form → canonical name */
let _canonicalMap = null;
/** @type {string[]} all canonical names (for iteration) */
let _allCanonicals = null;
/** @type {Map<string, string>} stripped form (no spaces/hyphens) → canonical */
let _fusedIndex = null;
/** @type {Map<string, string>} canonical name → entity category (region/city/supplementary/person/mp) */
let _entityTypeMap = null;
/** @type {Map<string, string>} canonical name → entity kind ("location" | "person" | "party") */
let _entityKindMap = null;
/** @type {Map<string, string>} canonical party name → abbreviation */
let _partyAbbrMap = null;

function buildDataset() {
  if (_canonicalMap) return;

  _canonicalMap = new Map();
  _allCanonicals = [];
  _fusedIndex = new Map();
  _entityTypeMap = new Map();
  _entityKindMap = new Map();
  _partyAbbrMap = new Map();

  const regions = getRegions();

  // Add regions
  for (const region of regions) {
    addEntry(region.name, 'region');
    // Add cities
    for (const city of region.cities) {
      addEntry(city, 'city');
    }
  }

  // Add supplementary locations
  for (const loc of SUPPLEMENTARY_LOCATIONS) {
    addEntry(loc.canonical, 'supplementary');
    if (loc.aliases) {
      for (const alias of loc.aliases) {
        const key = alias.toLowerCase();
        _canonicalMap.set(key, loc.canonical);
        _fusedIndex.set(stripAll(key), loc.canonical);
      }
    }
  }

  // Add persons (presidents, ministers, speakers)
  for (const person of ALL_PERSONS) {
    addEntry(person.canonical, 'person');
    if (person.aliases) {
      for (const alias of person.aliases) {
        const key = alias.toLowerCase();
        _canonicalMap.set(key, person.canonical);
        _fusedIndex.set(stripAll(key), person.canonical);
      }
    }
  }

  // Add Members of Parliament
  for (const mp of ALL_MPS) {
    addEntry(mp.name, 'mp');
    if (mp.aliases) {
      for (const alias of mp.aliases) {
        const key = alias.toLowerCase();
        if (!_canonicalMap.has(key)) {
          _canonicalMap.set(key, mp.name);
          _fusedIndex.set(stripAll(key), mp.name);
        }
      }
    }
  }

  // Add political parties (full name is canonical; abbreviation is an alias)
  for (const party of ALL_PARTIES) {
    addEntry(party.canonical, 'party');
    // Map canonical name to its abbreviation for display purposes
    if (party.abbr) {
      _partyAbbrMap.set(party.canonical, party.abbr);
    }
    // The abbreviation resolves to the full name (e.g. "NDC" → "National
    // Democratic Congress") so transcripts read naturally either way.
    if (party.abbr) {
      const abbrKey = party.abbr.toLowerCase();
      if (!_canonicalMap.has(abbrKey)) {
        _canonicalMap.set(abbrKey, party.canonical);
        _fusedIndex.set(stripAll(abbrKey), party.canonical);
      }
    }
    if (party.aliases) {
      for (const alias of party.aliases) {
        const key = alias.toLowerCase();
        if (!_canonicalMap.has(key)) {
          _canonicalMap.set(key, party.canonical);
          _fusedIndex.set(stripAll(key), party.canonical);
        }
      }
    }
  }
}

function addEntry(name, type) {
  const lower = name.toLowerCase();
  if (!_canonicalMap.has(lower)) {
    _canonicalMap.set(lower, name);
    _allCanonicals.push(name);
    _fusedIndex.set(stripAll(lower), name);
  }
  if (!_entityTypeMap.has(name)) {
    _entityTypeMap.set(name, type);
    let kind = 'location';
    if (type === 'person' || type === 'mp') kind = 'person';
    else if (type === 'party') kind = 'party';
    _entityKindMap.set(name, kind);
  }
}

/**
 * Strips all spaces, hyphens, and apostrophes — for fused-word matching.
 * "Ningo-Prampram" → "ningoprampram"
 * "Cape Coast" → "capecoast"
 */
function stripAll(s) {
  return s.replace(/[\s\-']/g, '').toLowerCase();
}

// ---------------------------------------------------------------------------
// Levenshtein distance
// ---------------------------------------------------------------------------

function levenshtein(a, b) {
  const al = a.length, bl = b.length;
  if (al === 0) return bl;
  if (bl === 0) return al;
  const row = Array.from({ length: bl + 1 }, (_, i) => i);
  for (let i = 1; i <= al; i++) {
    let prev = i;
    for (let j = 1; j <= bl; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const val = Math.min(row[j] + 1, prev + 1, row[j - 1] + cost);
      row[j - 1] = prev;
      prev = val;
    }
    row[bl] = prev;
  }
  return row[bl];
}

// ---------------------------------------------------------------------------
// Phonetic encoding (simplified Soundex-like for Ghana location names)
// ---------------------------------------------------------------------------

/**
 * Generates a phonetic key optimized for West African/Ghanaian place names.
 * Collapses common ASR substitution patterns:
 * - double consonants → single (kk → k, ss → s)
 * - 'ph' → 'f', 'gh' → 'g' (except trailing)
 * - trailing vowels are less significant
 * - 'ei'/'ey' → 'e', 'ou'/'oo' → 'u'
 */
function phoneticKey(str) {
  let s = str.toLowerCase().replace(/[\s\-']/g, '');
  // Common substitutions in ASR
  s = s.replace(/ph/g, 'f');
  s = s.replace(/gh(?!$)/g, 'g'); // 'gh' not at end
  s = s.replace(/ck/g, 'k');
  s = s.replace(/ei|ey/g, 'e');
  s = s.replace(/ou|oo/g, 'u');
  s = s.replace(/aa/g, 'a');
  s = s.replace(/ee/g, 'e');
  s = s.replace(/ii/g, 'i');
  // Collapse double consonants
  s = s.replace(/(.)\1+/g, '$1');
  return s;
}

/** @type {Map<string, string[]>|null} phonetic key → canonical names */
let _phoneticIndex = null;

function buildPhoneticIndex() {
  if (_phoneticIndex) return;
  buildDataset();
  _phoneticIndex = new Map();
  for (const canonical of _allCanonicals) {
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

// ---------------------------------------------------------------------------
// Matching strategies (ordered by confidence)
// ---------------------------------------------------------------------------

/**
 * Strategy 1: Exact match (case-insensitive) — 100% confidence.
 */
function matchExact(text) {
  buildDataset();
  const lower = text.toLowerCase();
  if (_canonicalMap.has(lower)) {
    return { canonical: _canonicalMap.get(lower), confidence: 1.0, strategy: 'exact' };
  }
  return null;
}

/**
 * Strategy 2: Fused match — strips all spaces/hyphens and checks.
 * Handles: "ningoprampram" → "Ningo-Prampram", "capecoast" → "Cape Coast"
 */
function matchFused(text) {
  buildDataset();
  const stripped = stripAll(text);
  if (stripped.length < 4) return null; // too short to be meaningful
  if (_fusedIndex.has(stripped)) {
    return { canonical: _fusedIndex.get(stripped), confidence: 0.98, strategy: 'fused' };
  }
  return null;
}

/**
 * Strategy 3: Split-word joining — concatenates neighboring tokens and checks
 * the fused index. Handles: "pram pram" → "Prampram", "cape coast" → "Cape Coast"
 * Called externally on n-grams.
 */
function matchJoined(tokens) {
  buildDataset();
  const joined = tokens.join('').toLowerCase();
  if (_fusedIndex.has(joined)) {
    return { canonical: _fusedIndex.get(joined), confidence: 0.97, strategy: 'joined' };
  }
  return null;
}

/**
 * Strategy 4: Phonetic match — uses phonetic encoding to find candidates.
 * Handles: "Koumasi" → "Kumasi", "nyungo" → "Ningo"
 */
function matchPhonetic(text) {
  buildPhoneticIndex();
  const key = phoneticKey(text);
  if (key.length < 4) return null;
  if (_phoneticIndex.has(key)) {
    const candidates = _phoneticIndex.get(key);
    // Pick the one with shortest Levenshtein distance to original
    let best = null, bestDist = Infinity;
    for (const c of candidates) {
      const d = levenshtein(text.toLowerCase(), c.toLowerCase());
      if (d < bestDist) { bestDist = d; best = c; }
    }
    if (best && bestDist <= Math.ceil(best.length * 0.4)) {
      return { canonical: best, confidence: 0.90, strategy: 'phonetic' };
    }
  }
  return null;
}

/**
 * Strategy 5: Fuzzy Levenshtein — find closest match within adaptive threshold.
 * More aggressive than frontend version — targets 95%+ recall.
 */
function matchFuzzy(text) {
  buildDataset();
  const lower = text.toLowerCase();
  const len = lower.length;
  if (len < 4) return null;

  // Block common English words from fuzzy-matching into location names
  if (STOPWORDS.has(lower)) return null;

  // Additional common words that are close to Ghana locations
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
  if (COMMON_BLOCK.has(lower)) return null;

  // Adaptive threshold: allow more edits for longer strings
  const maxDist = len <= 4 ? 0 : len <= 5 ? 1 : len <= 8 ? 2 : len <= 12 ? 3 : 4;

  let bestCanonical = null, bestDist = Infinity;

  for (const [key, canonical] of _canonicalMap) {
    if (Math.abs(key.length - len) > maxDist) continue;
    const d = levenshtein(lower, key);
    if (d > 0 && d <= maxDist && d < bestDist) {
      bestDist = d;
      bestCanonical = canonical;
    }
  }

  if (bestCanonical) {
    // Confidence decreases with edit distance
    const conf = Math.max(0.70, 1.0 - (bestDist * 0.12));
    return { canonical: bestCanonical, confidence: conf, strategy: 'fuzzy', distance: bestDist };
  }
  return null;
}

/**
 * Strategy 6: Subsequence/prefix match for very long fused words.
 * If a word of 10+ chars contains a known location as a substring,
 * extract it. Handles "ningoprampram" when neither the fused form
 * nor the phonetic form match.
 */
function matchSubstring(text) {
  buildDataset();
  const lower = text.toLowerCase();
  if (lower.length < 8) return null;

  let bestCanonical = null, bestLen = 0;

  for (const [key, canonical] of _canonicalMap) {
    if (key.length < 4) continue; // skip very short entries
    if (lower.includes(key) && key.length > bestLen) {
      bestLen = key.length;
      bestCanonical = canonical;
    }
  }

  if (bestCanonical && bestLen >= 5 && bestLen >= lower.length * 0.6) {
    return { canonical: bestCanonical, confidence: 0.80, strategy: 'substring' };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Strategy 7: Initials + surname matching
// Matches patterns like "A. Tetteh", "K. Ofori-Atta", "J.J. Rawlings"
// Also handles "honorable Tetteh" or "hon Ablakwa" (title + surname).
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

/**
 * Strategy 7: Initials matching.
 * Matches "A. Tetteh", "K. Ofori-Atta", "J.J. Rawlings", "J. Mahama"
 * Returns the full canonical name.
 */
function matchInitials(text) {
  buildInitialsIndex();

  // Normalize: "A. Tetteh" → "a.tetteh", "J.J. Rawlings" → "j.j.rawlings"
  const normalized = text.replace(/\.\s*/g, '.').replace(/\s+/g, '.').toLowerCase().replace(/\.$/, '');

  if (_initialSurnameIndex.has(normalized)) {
    const candidates = _initialSurnameIndex.get(normalized);
    return { canonical: candidates[0], confidence: 0.95, strategy: 'initials' };
  }

  // Try just the surname portion if the pattern has a single letter + surname
  const initialMatch = text.match(/^([A-Za-z])\.\s*(.+)$/);
  if (initialMatch) {
    const initial = initialMatch[1].toLowerCase();
    const surname = initialMatch[2].toLowerCase().replace(/\s+/g, '-');
    const key = initial + '.' + surname;
    if (_initialSurnameIndex.has(key)) {
      return { canonical: _initialSurnameIndex.get(key)[0], confidence: 0.95, strategy: 'initials' };
    }
    // Try surname alone
    const surnameClean = initialMatch[2].toLowerCase().replace(/-/g, '').trim();
    // Check in surname index and verify initial matches
    if (_surnameIndex.has(initialMatch[2].toLowerCase())) {
      const candidates = _surnameIndex.get(initialMatch[2].toLowerCase());
      const match = candidates.find(c => c[0].toLowerCase() === initial);
      if (match) return { canonical: match, confidence: 0.93, strategy: 'initials' };
    }
  }

  return null;
}

/**
 * Checks if a word is a title/honorific prefix that precedes politician names.
 * Used to avoid treating "honorable" as needing correction itself, and to
 * trigger person-name lookup on the following word(s).
 */
const TITLE_PREFIXES = new Set([
  'honorable', 'honourable', 'hon', 'hon.', 'rt', 'rt.',
  'minister', 'speaker', 'president', 'vice',
  'dr', 'dr.', 'prof', 'prof.', 'justice', 'chief',
  'madam', 'mr', 'mr.', 'mrs', 'mrs.', 'alhaji', 'nana',
]);

function isTitle(word) {
  return TITLE_PREFIXES.has(word.toLowerCase().replace(/\.$/, ''));
}

// ---------------------------------------------------------------------------
// Strategy 8: Title-preceded person matching
// When a title prefix is detected, strip it and try to match the following
// 1–3 tokens as a person name with a lowered confidence threshold.
// ---------------------------------------------------------------------------

/**
 * Attempts to match person name tokens following a title prefix.
 * Looks ahead at tokens[titleIndex+1] through tokens[titleIndex+3],
 * trying window sizes from largest to smallest (3, 2, 1).
 *
 * @param {Array<{word: string, start: number, end: number}>} tokens
 * @param {number} titleIndex - Index of the title token
 * @param {object} [options]
 * @param {number} [options.minConfidence=0.75] - Caller's min confidence
 * @returns {{ match: object, tokensConsumed: number }|null}
 */
function matchTitlePerson(tokens, titleIndex, options = {}) {
  buildInitialsIndex();
  buildDataset();

  const threshold = Math.min(options.minConfidence || 0.75, 0.65);
  const maxLookahead = Math.min(3, tokens.length - titleIndex - 1);

  if (maxLookahead < 1) return null;

  // Try window sizes from largest to smallest
  for (let winSize = maxLookahead; winSize >= 1; winSize--) {
    const nameTokens = tokens.slice(titleIndex + 1, titleIndex + 1 + winSize);
    const phrase = nameTokens.map(t => t.word).join(' ');

    // Skip if the phrase is a stopword or common non-person token
    if (winSize === 1 && STOPWORDS.has(phrase.toLowerCase())) continue;

    // Skip if the last token in the window is a stopword — avoids greedy
    // consumption of trailing filler (e.g. "rawlings was" matching as 2 tokens)
    if (winSize > 1 && STOPWORDS.has(nameTokens[winSize - 1].word.toLowerCase())) continue;

    const match = correctSingle(phrase);
    if (match && match.entityKind === 'person' && match.confidence >= threshold) {
      return { match, tokensConsumed: winSize };
    }
  }

  // Surname-only fallback: try last token in the max window via _surnameIndex
  const lastToken = tokens[titleIndex + 1]; // single token after title
  if (lastToken) {
    const surname = lastToken.word.toLowerCase();
    if (_surnameIndex && _surnameIndex.has(surname)) {
      const candidates = _surnameIndex.get(surname);
      if (candidates.length > 0) {
        const canonical = candidates[0];
        const kind = _entityKindMap.get(canonical);
        if (kind === 'person') {
          return {
            match: {
              canonical,
              confidence: 0.90,
              strategy: 'surname',
              entityKind: 'person',
              entityType: _entityTypeMap.get(canonical) || 'person',
            },
            tokensConsumed: 1,
          };
        }
      }
    }

    // Also try fuzzy on the single token after title at the lowered threshold
    const fuzzyMatch = matchPhonetic(lastToken.word) || matchFuzzy(lastToken.word);
    if (fuzzyMatch) {
      const attached = attachEntityInfo(fuzzyMatch);
      if (attached && attached.entityKind === 'person' && attached.confidence >= threshold) {
        return { match: attached, tokensConsumed: 1 };
      }
    }
  }

  return null;
}

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
// Main correction engine
// ---------------------------------------------------------------------------

/**
 * Attempts to correct a single word or phrase as a Ghana location.
 * Tries all strategies in priority order and returns the best match,
 * or null if no confident correction exists.
 *
 * @param {string} text - A word or short phrase (1–3 tokens)
 * @returns {{ canonical: string, confidence: number, strategy: string }|null}
 */
function correctSingle(text) {
  if (!text) return null;

  // Skip stopwords
  if (STOPWORDS.has(text.toLowerCase())) return null;

  // Short strings (e.g. party abbreviations like "NDC", "NPP", "PNC") are
  // only tried against exact/fused matches — fuzzy/phonetic/substring
  // strategies on very short strings risk too many false positives.
  if (text.length < 4) {
    const shortMatch = matchExact(text) || matchFused(text) || null;
    return attachEntityInfo(shortMatch);
  }

  // Try strategies in order of confidence
  const match = matchExact(text)
    || matchFused(text)
    || matchInitials(text)
    || matchPhonetic(text)
    || matchFuzzy(text)
    || matchSubstring(text)
    || null;

  return attachEntityInfo(match);
}

/**
 * Attaches entity classification (kind: "location"|"person", type:
 * region/city/supplementary/person/mp) to a match result, looked up from
 * the canonical name. Leaves the match untouched if no classification is
 * found (should not normally happen once buildDataset has run).
 */
function attachEntityInfo(match) {
  if (!match) return null;
  buildDataset();
  const type = _entityTypeMap.get(match.canonical);
  const kind = _entityKindMap.get(match.canonical);
  if (type) match.entityType = type;
  if (kind) match.entityKind = kind;
  return match;
}

/**
 * Corrects all Ghana location references in a transcript text.
 *
 * Scans 1–4 word n-grams at each position, tries all correction strategies
 * on each candidate, and applies the highest-confidence correction found.
 *
 * Returns the corrected text and a list of corrections applied.
 *
 * @param {string} text - Full transcript text
 * @param {object} [options] - Options
 * @param {number} [options.minConfidence=0.75] - Minimum confidence to apply a correction
 * @returns {{ text: string, corrections: Array<{ original: string, corrected: string, confidence: number, strategy: string, index: number }> }}
 */
function correctLocations(text, options = {}) {
  buildDataset();
  buildPhoneticIndex();

  const minConfidence = options.minConfidence ?? 0.75;

  if (!text || !text.trim()) {
    return { text, corrections: [] };
  }

  // Tokenize with positions
  const tokenRegex = /[A-Za-zÀ-ÿ'-]+/g;
  const tokens = [];
  let m;
  while ((m = tokenRegex.exec(text)) !== null) {
    tokens.push({ word: m[0], start: m.index, end: m.index + m[0].length });
  }

  const corrections = [];
  // Tracks every recognized entity mention (both corrected AND already
  // correctly-spelled) so callers can build a complete "entities found in
  // this transcript" list rather than only entities that needed fixing.
  const entitiesFound = [];
  const consumed = new Set();

  for (let i = 0; i < tokens.length; i++) {
    if (consumed.has(i)) continue;

    // --- Title-preceded person lookup ---
    // When a title is detected, try to match the following tokens as a person
    // name with a lowered confidence threshold. The title token itself is NOT
    // consumed/corrected — only the name tokens after it are.
    if (isTitle(tokens[i].word)) {
      const titleResult = matchTitlePerson(tokens, i, { minConfidence });
      if (titleResult) {
        const { match, tokensConsumed } = titleResult;
        // Mark name tokens (after the title) as consumed
        const nameStart = i + 1;
        const nameSlice = tokens.slice(nameStart, nameStart + tokensConsumed);
        const original = text.slice(nameSlice[0].start, nameSlice[nameSlice.length - 1].end);

        // If the canonical starts with the same title token (e.g. title="Nana",
        // canonical="Nana Addo Dankwa Akufo-Addo"), strip the leading title from
        // the replacement to avoid duplication ("Nana Nana Addo...").
        let correctedName = match.canonical;
        const titleWord = tokens[i].word.toLowerCase();
        const canonicalFirstWord = correctedName.split(/\s+/)[0].toLowerCase();
        if (titleWord === canonicalFirstWord || (titleWord === 'nana' && canonicalFirstWord === 'nana')) {
          correctedName = correctedName.replace(/^\S+\s+/, '');
        }

        const isIdentity = correctedName.toLowerCase() === original.toLowerCase();

        if (isIdentity) {
          // Name is already canonical — record as recognized entity only
          entitiesFound.push({
            original,
            corrected: match.canonical,
            confidence: match.confidence,
            strategy: 'identity',
            index: nameSlice[0].start,
            entityKind: match.entityKind || 'person',
            entityType: match.entityType || 'person',
          });
        } else {
          // Name needs correction
          const correctionEntry = {
            original,
            corrected: correctedName,
            confidence: match.confidence,
            strategy: match.strategy,
            index: nameSlice[0].start,
            entityKind: match.entityKind || 'person',
            entityType: match.entityType || 'person',
          };
          corrections.push(correctionEntry);
          entitiesFound.push(correctionEntry);
        }

        // Mark title token as consumed (so it's not re-processed) but NOT corrected
        consumed.add(i);
        for (let j = 0; j < tokensConsumed; j++) consumed.add(nameStart + j);
        // Skip past consumed tokens
        i += tokensConsumed; // loop will i++ to move past the last consumed token
        continue;
      }
    }

    let bestMatch = null;
    let bestNgramSize = 0;
    let bestConfidence = 0;

    // Try n-grams from longest (4) to shortest (1)
    for (let n = Math.min(4, tokens.length - i); n >= 1; n--) {
      const slice = tokens.slice(i, i + n);
      const phrase = slice.map(t => t.word).join(' ');

      // Skip phrases starting/ending with stopwords (unless trying fused detection)
      // Exception: single-letter initials like "A." or "J." are not stopwords
      if (n > 1) {
        const first = slice[0].word.toLowerCase().replace(/\.$/, '');
        const last = slice[slice.length - 1].word.toLowerCase().replace(/\.$/, '');
        const firstIsInitial = slice[0].word.length <= 2 && /^[a-z]\.?$/i.test(slice[0].word);
        const firstIsTitle = isTitle(slice[0].word);
        if (!firstIsInitial && !firstIsTitle && STOPWORDS.has(first)) continue;
        if (STOPWORDS.has(last)) continue;
      }

      // Strategy A: try the phrase as-is
      let match = correctSingle(phrase);

      // Strategy B: try joining tokens (for split words like "pram pram")
      if (!match && n > 1) {
        match = attachEntityInfo(matchJoined(slice.map(t => t.word)));
      }

      // Accept if it meets the confidence threshold
      if (match && match.confidence >= minConfidence) {
        const isIdentity = match.canonical.toLowerCase() === phrase.toLowerCase();

        if (isIdentity) {
          // Already correctly spelled — record it as a recognized entity
          // (so it surfaces in entity lists) but don't rewrite the text.
          entitiesFound.push({
            original: phrase,
            corrected: match.canonical,
            confidence: match.confidence,
            strategy: 'identity',
            index: slice[0].start,
            entityKind: match.entityKind || 'location',
            entityType: match.entityType || 'unknown',
          });
          for (let j = 0; j < n; j++) consumed.add(i + j);
          bestMatch = null;
          break;
        }

        if (match.confidence > bestConfidence) {
          bestMatch = match;
          bestNgramSize = n;
          bestConfidence = match.confidence;
        }
      }
    }

    if (bestMatch) {
      const slice = tokens.slice(i, i + bestNgramSize);
      const original = text.slice(slice[0].start, slice[slice.length - 1].end);
      const correctionEntry = {
        original,
        corrected: bestMatch.canonical,
        confidence: bestMatch.confidence,
        strategy: bestMatch.strategy,
        index: slice[0].start,
        entityKind: bestMatch.entityKind || 'location',
        entityType: bestMatch.entityType || 'unknown',
      };
      corrections.push(correctionEntry);
      entitiesFound.push(correctionEntry);
      for (let j = 0; j < bestNgramSize; j++) consumed.add(i + j);
    } else if (!consumed.has(i)) {
      consumed.add(i);
    }
  }

  // Apply corrections from end to start to preserve indices
  let result = text;
  const sorted = [...corrections].sort((a, b) => b.index - a.index);
  for (const c of sorted) {
    const end = c.index + c.original.length;
    result = result.slice(0, c.index) + c.corrected + result.slice(end);
  }

  // Sort entitiesFound by position for stable, readable ordering
  entitiesFound.sort((a, b) => a.index - b.index);

  return { text: result, corrections, entitiesFound };
}

/**
 * Returns the proper abbreviation for a party canonical name (e.g.
 * "National Democratic Congress" → "NDC"). Returns null if not a party.
 */
function getPartyAbbr(canonical) {
  buildDataset();
  return _partyAbbrMap.get(canonical) || null;
}

module.exports = {
  correctLocations,
  correctSingle,
  getPartyAbbr,
  matchTitlePerson,
  isTitle,
  SUPPLEMENTARY_LOCATIONS,
};
