/**
 * Bedrock system prompt construction.
 *
 * Exports: getSystemPrompt, buildDatasetReference
 */

'use strict';

const { ALL_PERSONS } = require('../persons-dataset');
const { ALL_MPS } = require('../mps-dataset');
const { ALL_PARTIES } = require('../parties-dataset');
const { getRegions } = require('ghana-locations');
const { SUPPLEMENTARY_LOCATIONS } = require('../dataset-builder');

// ---------------------------------------------------------------------------
// Build compact dataset reference for the system prompt (cached)
// ---------------------------------------------------------------------------

let _datasetReference = null;

function buildDatasetReference() {
  if (_datasetReference) return _datasetReference;

  // Regions (16)
  const regions = getRegions().map(r => r.name);

  // Key cities (top cities per region, max ~80 total)
  const cities = [];
  for (const r of getRegions()) {
    cities.push(...r.cities.slice(0, 5));
  }

  // Supplementary locations (constituencies, districts)
  const suppl = SUPPLEMENTARY_LOCATIONS.map(l => l.canonical);

  // Presidents & key officials (compact: "Name (Role)")
  const officials = ALL_PERSONS.map(p => `${p.canonical} [${p.role.split('(')[0].trim()}]`);

  // Current MPs (just names — compact list, ~80 entries)
  const mps = ALL_MPS.slice(0, 80).map(mp => `${mp.name} (${mp.constituency})`);

  // Parties (name + abbreviation)
  const parties = ALL_PARTIES.map(p => `${p.canonical} (${p.abbr})`);

  _datasetReference = `
<REFERENCE_DATA>
<REGIONS>
${regions.join(', ')}
</REGIONS>

<KEY_CITIES>
${cities.join(', ')}
</KEY_CITIES>

<CONSTITUENCIES_DISTRICTS>
${suppl.join(', ')}
</CONSTITUENCIES_DISTRICTS>

<OFFICIALS>
${officials.join('\n')}
</OFFICIALS>

<CURRENT_MPS>
${mps.join('\n')}
</CURRENT_MPS>

<POLITICAL_PARTIES>
${parties.join('\n')}
</POLITICAL_PARTIES>
</REFERENCE_DATA>`;

  return _datasetReference;
}

// ---------------------------------------------------------------------------
// System prompt with dataset context
// ---------------------------------------------------------------------------

function getSystemPrompt() {
  const reference = buildDatasetReference();

  return `You are a post-processing assistant for Ghanaian parliamentary transcripts (Hansard).

Your job: Fix proper nouns in ASR output using the reference data below. The transcript has already been partially corrected by a rule-based system, but some low-confidence words remain incorrect.

${reference}

CORRECTION RULES:
1. Use the reference data above as your source of truth for valid names, locations, parties
2. Fix misspelled proper nouns to their correct form from the reference data
3. Apply proper capitalization to all names, titles, places, and party names
4. If a word sounds phonetically similar to a name in the reference data, correct it — BUT only when context strongly suggests a proper noun was intended
5. "honorable" or "hon" before a name = MP title, capitalize: "Honorable"
6. Party abbreviations (NDC, NPP, CPP) should stay as abbreviations, properly capitalized
7. Do NOT change words that are already correct
8. Do NOT add or remove words — only fix spelling/capitalization
9. Do NOT add punctuation or restructure sentences
10. Do NOT convert common English words into location/entity names. For example: "general" must NOT become "Central", "nation" must NOT become "Nanton", "several" must NOT become a location. Words like "attorney general", "general election", "in general" are everyday English phrases — leave them unchanged.
11. Do NOT convert spoken numbers into numeric year format. For example: "twenty six" must NOT become "2006", "nineteen ninety two" must NOT become "1992". Year conversion is handled by a separate system — leave all number words as-is.
12. Do NOT replace parts of a person's name with a location name. For example: "Dankwa" in "Nana Addo Dankwa Akufo-Addo" must NOT become "Tarkwa". "Dramani" in "John Dramani Mahama" must NOT become "Damongo" or "Shama". Names already corrected by the rule-based system are authoritative — preserve them.
13. Return ONLY the corrected text with [Segment N]: labels matching the input format`;
}

module.exports = { getSystemPrompt, buildDatasetReference };
