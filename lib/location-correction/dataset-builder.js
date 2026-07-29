/**
 * Dataset construction for the Ghana Location Correction Engine.
 *
 * Exports: buildDataset, addEntry, getCanonicalMap, getAllCanonicals,
 *          getFusedIndex, getEntityTypeMap, getEntityKindMap, getPartyAbbrMap,
 *          SUPPLEMENTARY_LOCATIONS
 */

'use strict';

const { getRegions } = require('ghana-locations');
const { ALL_PERSONS } = require('./persons-dataset');
const { ALL_MPS } = require('./mps-dataset');
const { ALL_PARTIES } = require('./parties-dataset');
const { stripAll } = require('./normalize');

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

// Accessors for the internal maps (used by indexes.js and matchers.js)
function getCanonicalMap() { buildDataset(); return _canonicalMap; }
function getAllCanonicals() { buildDataset(); return _allCanonicals; }
function getFusedIndex() { buildDataset(); return _fusedIndex; }
function getEntityTypeMap() { buildDataset(); return _entityTypeMap; }
function getEntityKindMap() { buildDataset(); return _entityKindMap; }
function getPartyAbbrMap() { buildDataset(); return _partyAbbrMap; }

module.exports = {
  SUPPLEMENTARY_LOCATIONS,
  buildDataset,
  addEntry,
  getCanonicalMap,
  getAllCanonicals,
  getFusedIndex,
  getEntityTypeMap,
  getEntityKindMap,
  getPartyAbbrMap,
};
