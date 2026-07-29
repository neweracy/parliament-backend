/**
 * Ghana Location Correction Engine (Backend) — Public Entry Point
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

const {
  buildDataset,
  SUPPLEMENTARY_LOCATIONS,
  getEntityTypeMap,
  getEntityKindMap,
  getPartyAbbrMap,
  getCanonicalMap,
  getFusedIndex,
} = require('./dataset-builder');

const {
  STOPWORDS,
  COMMON_BLOCK,
  TITLE_PREFIXES,
  buildPhoneticIndex,
  buildInitialsIndex,
} = require('./indexes');

const {
  matchExact,
  matchFused,
  matchJoined,
  matchPhonetic,
  matchFuzzy,
  matchSubstring,
  matchInitials,
  isTitle,
  matchTitlePerson,
} = require('./matchers');

// ---------------------------------------------------------------------------
// Main correction engine
// ---------------------------------------------------------------------------

/**
 * Attaches entity classification (kind: "location"|"person", type:
 * region/city/supplementary/person/mp) to a match result, looked up from
 * the canonical name.
 */
function attachEntityInfo(match) {
  if (!match) return null;
  buildDataset();
  const _entityTypeMap = getEntityTypeMap();
  const _entityKindMap = getEntityKindMap();
  const type = _entityTypeMap.get(match.canonical);
  const kind = _entityKindMap.get(match.canonical);
  if (type) match.entityType = type;
  if (kind) match.entityKind = kind;
  return match;
}

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
  // only tried against exact/fused matches
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
  const entitiesFound = [];
  const consumed = new Set();

  for (let i = 0; i < tokens.length; i++) {
    if (consumed.has(i)) continue;

    // --- Title-preceded person lookup ---
    if (isTitle(tokens[i].word)) {
      const titleResult = matchTitlePerson(tokens, i, { minConfidence });
      if (titleResult) {
        const { match, tokensConsumed } = titleResult;
        const nameStart = i + 1;
        const nameSlice = tokens.slice(nameStart, nameStart + tokensConsumed);
        const original = text.slice(nameSlice[0].start, nameSlice[nameSlice.length - 1].end);

        let correctedName = match.canonical;
        const titleWord = tokens[i].word.toLowerCase();
        const canonicalFirstWord = correctedName.split(/\s+/)[0].toLowerCase();
        if (titleWord === canonicalFirstWord || (titleWord === 'nana' && canonicalFirstWord === 'nana')) {
          correctedName = correctedName.replace(/^\S+\s+/, '');
        }

        const isIdentity = correctedName.toLowerCase() === original.toLowerCase();

        if (isIdentity) {
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

        consumed.add(i);
        for (let j = 0; j < tokensConsumed; j++) consumed.add(nameStart + j);
        i += tokensConsumed;
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
  const _partyAbbrMap = getPartyAbbrMap();
  return _partyAbbrMap.get(canonical) || null;
}

module.exports = {
  correctLocations,
  correctSingle,
  getPartyAbbr,
  attachEntityInfo,
  matchTitlePerson,
  isTitle,
  SUPPLEMENTARY_LOCATIONS,
};
