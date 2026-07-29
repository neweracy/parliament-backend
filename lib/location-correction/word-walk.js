'use strict';

const { correctLocations, getPartyAbbr, isTitle } = require('./index');

// ---------------------------------------------------------------------------
// Word-level stopwords — lifted from the function-local Set in legacyPostprocess
// ---------------------------------------------------------------------------

const wordStopwords = new Set(['a','an','the','in','on','at','to','of','is','are','was','were',
  'be','and','or','but','for','by','with','from','this','that','it','he','she','they','we',
  'his','her','their','our','my','your','as','so','if','not','through','has','had','have',
  'constituency','traditional','area','alongside','among','these','those','region',
  'district','municipal','metropolitan','assembly','parliament','bill','motion',
  'committee','minister','speaker','members','distinguished',
  'general','attorney','justice','deputy','leader','majority','minority',
  'page','paper','order','number','same']);

/**
 * Word-level n-gram walk with title-aware person detection.
 *
 * Walks the raw words array applying location/person/party correction via a
 * 3→2→1 n-gram strategy, preceded by a title-prefix lookahead for person names.
 * This is the exact logic previously inlined in `legacyPostprocess` in `server.js`.
 *
 * @param {Array<{word: string, start: number, end: number, confidence: number}>} rawWords
 *   The raw words array from Deepgram (already shallow-copied by the caller).
 * @param {{ correctLocations: Function, getPartyAbbr: Function, isTitle: Function }} [deps]
 *   Optional dependency overrides for testing. Defaults to the exports from
 *   `lib/location-correction/index.js`.
 * @returns {Array<{word: string, start: number, end: number, confidence: number, locationCorrected?: boolean, entityKind?: string, entityType?: string}>}
 *   The corrected words array with entity annotations on corrected entries.
 */
function correctWordsWalk(rawWords, deps) {
  const _correctLocations = (deps && deps.correctLocations) || correctLocations;
  const _getPartyAbbr = (deps && deps.getPartyAbbr) || getPartyAbbr;
  const _isTitle = (deps && deps.isTitle) || isTitle;

  const words = [];

  for (let i = 0; i < rawWords.length; i++) {
    const w = rawWords[i];

    // Title-aware person detection: when a title prefix is found, try joining
    // it with the next 1-3 words and run correctLocations on the phrase.
    // This enables "Honorable bb kabo" → title preserved + "B.B. Carboo".
    if (_isTitle(w.word)) {
      let titleMatched = false;
      // Try windows of 3, 2, 1 words after the title
      for (let n = Math.min(3, rawWords.length - i - 1); n >= 1; n--) {
        const phraseWords = [w.word];
        for (let j = 1; j <= n; j++) {
          phraseWords.push(rawWords[i + j].word);
        }
        const phrase = phraseWords.join(' ');
        const phraseResult = _correctLocations(phrase);
        // Only accept person corrections from title-triggered lookup
        const personCorr = phraseResult.corrections.find(c => c.entityKind === 'person');
        if (personCorr) {
          // Determine how many name tokens were actually consumed by the correction
          // The correction's "original" tells us which words were matched
          const origTokens = personCorr.original.split(/\s+/).length;
          const consumed = Math.min(origTokens, n);
          // Push the title word unchanged (preserved)
          words.push(w);
          // Push the corrected name as a single merged word spanning the name tokens
          const lastNameWord = rawWords[i + consumed];
          words.push({
            ...rawWords[i + 1],
            word: personCorr.corrected,
            end: lastNameWord.end,
            locationCorrected: true,
            entityKind: 'person',
            entityType: personCorr.entityType || 'person',
          });
          i += consumed; // skip only the consumed name words
          titleMatched = true;
          break;
        }
      }
      if (titleMatched) continue;
      // No person match — push title as-is and continue normal processing
      words.push(w);
      continue;
    }

    if (wordStopwords.has(w.word?.toLowerCase())) {
      words.push(w);
      continue;
    }

    // Try 3-word join first (e.g. "ninggu pram pram" → "Ningo-Prampram")
    if (i + 2 < rawWords.length) {
      const w2 = rawWords[i + 1];
      const w3 = rawWords[i + 2];
      if (!wordStopwords.has(w2.word?.toLowerCase()) && !wordStopwords.has(w3.word?.toLowerCase()) && w2.word && w3.word) {
        const triple = w.word + ' ' + w2.word + ' ' + w3.word;
        const tripleResult = _correctLocations(triple);
        if (tripleResult.corrections.length > 0 && tripleResult.corrections[0].confidence >= 0.90) {
          const corr = tripleResult.corrections[0];
          if (corr.original.toLowerCase() === triple.toLowerCase()) {
            words.push({
              ...w,
              word: tripleResult.text,
              end: w3.end,
              locationCorrected: true,
              entityKind: corr.entityKind,
              entityType: corr.entityType,
            });
            i += 2; // skip next 2 words
            continue;
          }
        }
      }
    }

    // Try 2-word join (only if next word isn't a stopword)
    if (i + 1 < rawWords.length) {
      const next = rawWords[i + 1];
      if (!wordStopwords.has(next.word?.toLowerCase()) && next.word) {
        const pair = w.word + ' ' + next.word;
        const pairResult = _correctLocations(pair);
        if (pairResult.corrections.length > 0 && pairResult.corrections[0].confidence >= 0.90) {
          const corr = pairResult.corrections[0];
          if (corr.original.toLowerCase() === pair.toLowerCase()) {
            words.push({
              ...w,
              word: pairResult.text,
              end: next.end,
              locationCorrected: true,
              entityKind: corr.entityKind,
              entityType: corr.entityType,
            });
            i++; // skip next word
            continue;
          }
        }
      }
    }

    // Try single-word correction. Short abbreviations (e.g. "NDC", "NPP")
    // are allowed through at length >= 3 since party abbreviations are
    // exactly 3-4 letters; everything else requires length >= 4 to avoid
    // over-eager fuzzy matching on very short words.
    if (w.word && w.word.length >= 3) {
      const singleResult = _correctLocations(w.word);
      if (singleResult.corrections.length > 0 && singleResult.corrections[0].confidence >= 0.90) {
        const corrText = singleResult.text;
        const corr = singleResult.corrections[0];
        // For parties: when the input is already an abbreviation (e.g. "ndc"),
        // just normalize to proper uppercase ("NDC") instead of expanding to
        // the full name ("National Democratic Congress"). The full expansion
        // would break word timing and be redundant if the full name was
        // already spoken earlier.
        const wordCount = corrText.split(/\s+/).length;
        const isParty = corr.entityKind === 'party';
        let displayText = corrText;
        if (isParty) {
          // Use the abbreviation if the original was short (abbreviation-length)
          const abbr = _getPartyAbbr(corrText);
          if (abbr && w.word.length <= abbr.length + 1) {
            displayText = abbr; // "ndc" → "NDC", not "National Democratic Congress"
          }
        }
        if (isParty || wordCount <= 2) {
          words.push({
            ...w,
            word: displayText,
            locationCorrected: true,
            entityKind: corr.entityKind,
            entityType: corr.entityType,
          });
          continue;
        }
      }
    }

    words.push(w);
  }

  return words;
}

module.exports = { correctWordsWalk, wordStopwords };
