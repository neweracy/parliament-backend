/**
 * Ghana Political Parties Dataset
 *
 * Currently registered parties (Electoral Commission of Ghana, as of 2025)
 * plus notable historical parties referenced in parliamentary/media
 * transcripts. Used by the post-processing correction engine to fix ASR
 * misspellings and recognize parties by full name or abbreviation.
 *
 * Format: { canonical: string, abbr: string, aliases?: string[] }
 * Aliases include common ASR misspellings and spoken-out abbreviations.
 */

'use strict';

// ---------------------------------------------------------------------------
// Currently registered parties (Fourth Republic, as of 2025)
// Source: Electoral Commission of Ghana / Wikipedia (Jan 2025 — 15 parties)
// ---------------------------------------------------------------------------

const CURRENT_PARTIES = [
  {
    canonical: 'National Democratic Congress',
    abbr: 'NDC',
    aliases: ['N.D.C.', 'National Democratic Congress Party'],
  },
  {
    canonical: 'New Patriotic Party',
    abbr: 'NPP',
    aliases: ['N.P.P.', 'New Patriotic Party Ghana'],
  },
  {
    canonical: "All People's Congress",
    abbr: 'APC',
    aliases: ['A.P.C.', 'All Peoples Congress'],
  },
  {
    canonical: "Convention People's Party",
    abbr: 'CPP',
    aliases: ['C.P.P.', 'Convention Peoples Party'],
  },
  {
    canonical: 'Great Consolidated Popular Party',
    abbr: 'GCPP',
    aliases: ['G.C.P.P.'],
  },
  {
    canonical: 'Ghana Freedom Party',
    abbr: 'GFP',
    aliases: ['G.F.P.'],
  },
  {
    canonical: 'Ghana Union Movement',
    abbr: 'GUM',
    aliases: ['G.U.M.'],
  },
  {
    canonical: 'Liberal Party of Ghana',
    abbr: 'LPG',
    aliases: ['L.P.G.'],
  },
  {
    canonical: 'National Democratic Party',
    abbr: 'NDP',
    aliases: ['N.D.P.'],
  },
  {
    canonical: "People's National Convention",
    abbr: 'PNC',
    aliases: ['P.N.C.', 'Peoples National Convention'],
  },
  {
    canonical: 'Progressive Alliance for Ghana',
    abbr: 'PAG',
    aliases: ['P.A.G.'],
  },
  {
    canonical: "Progressive People's Party",
    abbr: 'PPP',
    aliases: ['P.P.P.', 'Progressive Peoples Party'],
  },
];

// ---------------------------------------------------------------------------
// Notable historical parties (deregistered but referenced in older
// transcripts, Hansard records, and historical political commentary)
// ---------------------------------------------------------------------------

const HISTORICAL_PARTIES = [
  { canonical: 'Progress Party', abbr: 'PP', aliases: ['P.P.'] },
  { canonical: "People's National Party", abbr: 'PNP', aliases: ['P.N.P.', 'Peoples National Party'] },
  { canonical: 'Popular Front Party', abbr: 'PFP', aliases: ['P.F.P.'] },
  { canonical: 'United Party', abbr: 'UP', aliases: ['U.P.'] },
  { canonical: 'United Gold Coast Convention', abbr: 'UGCC', aliases: ['U.G.C.C.'] },
  { canonical: 'National Liberation Movement', abbr: 'NLM', aliases: ['N.L.M.'] },
  { canonical: 'National Alliance of Liberals', abbr: 'NAL', aliases: ['N.A.L.'] },
  { canonical: 'Democratic Freedom Party', abbr: 'DFP', aliases: ['D.F.P.'] },
  { canonical: "Democratic People's Party", abbr: 'DPP', aliases: ['D.P.P.', 'Democratic Peoples Party'] },
  { canonical: 'United Ghana Movement', abbr: 'UGM', aliases: ['U.G.M.'] },
  { canonical: 'National Reform Party', abbr: 'NRP', aliases: ['N.R.P.'] },
  { canonical: "People's Heritage Party", abbr: 'PHP', aliases: ['P.H.P.', 'Peoples Heritage Party'] },
  { canonical: 'National Independence Party', abbr: 'NIP', aliases: ['N.I.P.'] },
  { canonical: "People's Convention Party", abbr: 'PCP', aliases: ['P.C.P.', 'Peoples Convention Party'] },
];

// ---------------------------------------------------------------------------
// Combined and export
// ---------------------------------------------------------------------------

const ALL_PARTIES = [...CURRENT_PARTIES, ...HISTORICAL_PARTIES];

module.exports = {
  CURRENT_PARTIES,
  HISTORICAL_PARTIES,
  ALL_PARTIES,
};
