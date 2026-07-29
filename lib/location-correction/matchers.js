/**
 * Matching strategies for the Ghana Location Correction Engine.
 *
 * Exports: matchExact, matchFused, matchJoined, matchPhonetic, matchFuzzy,
 *          matchSubstring, matchInitials, isTitle, matchTitlePerson
 */

'use strict';

const { levenshtein, phoneticKey, stripAll } = require('./normalize');
const {
  buildDataset,
  getCanonicalMap,
  getFusedIndex,
  getEntityTypeMap,
  getEntityKindMap,
} = require('./dataset-builder');
const {
  STOPWORDS,
  COMMON_BLOCK,
  TITLE_PREFIXES,
  buildPhoneticIndex,
  getPhoneticIndex,
  buildInitialsIndex,
  getSurnameIndex,
  getInitialSurnameIndex,
} = require('./indexes');

// ---------------------------------------------------------------------------
// Matching strategies (ordered by confidence)
// ---------------------------------------------------------------------------

/**
 * Strategy 1: Exact match (case-insensitive) — 100% confidence.
 */
function matchExact(text) {
  buildDataset();
  const lower = text.toLowerCase();
  const _canonicalMap = getCanonicalMap();
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
  const _fusedIndex = getFusedIndex();
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
  const _fusedIndex = getFusedIndex();
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
  const _phoneticIndex = getPhoneticIndex();
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

  if (COMMON_BLOCK.has(lower)) return null;

  // Adaptive threshold: allow more edits for longer strings
  const maxDist = len <= 4 ? 0 : len <= 5 ? 1 : len <= 8 ? 2 : len <= 12 ? 3 : 4;

  let bestCanonical = null, bestDist = Infinity;

  const _canonicalMap = getCanonicalMap();
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
 * extract it.
 */
function matchSubstring(text) {
  buildDataset();
  const lower = text.toLowerCase();
  if (lower.length < 8) return null;

  let bestCanonical = null, bestLen = 0;

  const _canonicalMap = getCanonicalMap();
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

/**
 * Strategy 7: Initials matching.
 * Matches "A. Tetteh", "K. Ofori-Atta", "J.J. Rawlings", "J. Mahama"
 * Returns the full canonical name.
 */
function matchInitials(text) {
  buildInitialsIndex();
  const _initialSurnameIndex = getInitialSurnameIndex();
  const _surnameIndex = getSurnameIndex();

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
 */
function isTitle(word) {
  return TITLE_PREFIXES.has(word.toLowerCase().replace(/\.$/, ''));
}

// ---------------------------------------------------------------------------
// Strategy 8: Title-preceded person matching
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

  // Import correctSingle lazily to avoid circular dependency
  const { correctSingle } = require('./index');
  const _surnameIndex = getSurnameIndex();
  const _entityKindMap = getEntityKindMap();
  const _entityTypeMap = getEntityTypeMap();

  const threshold = Math.min(options.minConfidence || 0.75, 0.65);
  const maxLookahead = Math.min(3, tokens.length - titleIndex - 1);

  if (maxLookahead < 1) return null;

  // Try window sizes from largest to smallest
  for (let winSize = maxLookahead; winSize >= 1; winSize--) {
    const nameTokens = tokens.slice(titleIndex + 1, titleIndex + 1 + winSize);
    const phrase = nameTokens.map(t => t.word).join(' ');

    // Skip if the phrase is a stopword or common non-person token
    if (winSize === 1 && STOPWORDS.has(phrase.toLowerCase())) continue;

    // Skip if the last token in the window is a stopword
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
      const { attachEntityInfo } = require('./index');
      const attached = attachEntityInfo(fuzzyMatch);
      if (attached && attached.entityKind === 'person' && attached.confidence >= threshold) {
        return { match: attached, tokensConsumed: 1 };
      }
    }
  }

  return null;
}

module.exports = {
  matchExact,
  matchFused,
  matchJoined,
  matchPhonetic,
  matchFuzzy,
  matchSubstring,
  matchInitials,
  isTitle,
  matchTitlePerson,
};
