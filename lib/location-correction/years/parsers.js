/**
 * Parsing helpers for the Year/Date Correction Engine.
 *
 * Exports: clean, parseCenturyPrefix, parseTwoDigitSuffix,
 *          parseOrdinalDay, parseCardinalDay, parseDayWords, isMonth,
 *          capitalizeMonth, ordinalSuffix, parseYearAtPosition
 */

'use strict';

const { ONES, TENS, MONTHS, MONTH_NAMES, ORDINALS } = require('./numbers');

function clean(word) {
  return (word || '').toLowerCase().replace(/[.,;:!?]/g, '').trim();
}

/**
 * Try to parse a "century prefix" — e.g. "nineteen", "twenty", "eighteen"
 */
function parseCenturyPrefix(word) {
  const w = clean(word);
  if (TENS[w] !== undefined) return TENS[w];
  if (w === 'nineteen') return 19;
  if (w === 'eighteen') return 18;
  if (w === 'seventeen') return 17;
  if (w === 'sixteen') return 16;
  if (w === 'fifteen') return 15;
  if (w === 'fourteen') return 14;
  if (w === 'thirteen') return 13;
  return null;
}

/**
 * Parse a two-digit suffix from one or two words.
 * Returns { value, wordsConsumed, padded } or null.
 */
function parseTwoDigitSuffix(words, startIdx) {
  if (startIdx >= words.length) return null;

  const w1 = clean(words[startIdx]);

  // "oh" + digit (e.g. "oh five" = 05)
  if (w1 === 'oh' || w1 === 'o') {
    if (startIdx + 1 < words.length) {
      const w2 = clean(words[startIdx + 1]);
      if (ONES[w2] !== undefined && ONES[w2] <= 9) {
        return { value: ONES[w2], wordsConsumed: 2, padded: true };
      }
    }
    return null;
  }

  // Direct ones (one through nineteen)
  if (ONES[w1] !== undefined && ONES[w1] <= 19) {
    return { value: ONES[w1], wordsConsumed: 1, padded: ONES[w1] < 10 };
  }

  // Tens (twenty, thirty, etc.)
  if (TENS[w1] !== undefined) {
    // Check for hyphenated form already: "twenty-four"
    if (w1.includes('-')) {
      const parts = w1.split('-');
      if (TENS[parts[0]] !== undefined && ONES[parts[1]] !== undefined) {
        return { value: TENS[parts[0]] + ONES[parts[1]], wordsConsumed: 1, padded: false };
      }
    }
    // Check next word for ones
    if (startIdx + 1 < words.length) {
      const w2 = clean(words[startIdx + 1]);
      if (ONES[w2] !== undefined && ONES[w2] <= 9) {
        return { value: TENS[w1] + ONES[w2], wordsConsumed: 2, padded: false };
      }
    }
    return { value: TENS[w1], wordsConsumed: 1, padded: false };
  }

  return null;
}

/**
 * Try to parse an ordinal day from a word.
 */
function parseOrdinalDay(word) {
  const w = clean(word);
  if (ORDINALS[w] !== undefined) return ORDINALS[w];
  const numMatch = w.match(/^(\d{1,2})(st|nd|rd|th)$/);
  if (numMatch) {
    const num = parseInt(numMatch[1], 10);
    if (num >= 1 && num <= 31) return num;
  }
  return null;
}

/**
 * Try to parse a cardinal day (1–31) from a word.
 */
function parseCardinalDay(word) {
  const w = clean(word);
  if (/^\d{1,2}$/.test(w)) {
    const num = parseInt(w, 10);
    if (num >= 1 && num <= 31) return num;
  }
  if (ONES[w] !== undefined && ONES[w] >= 1 && ONES[w] <= 19) return ONES[w];
  if (TENS[w] !== undefined && (TENS[w] === 20 || TENS[w] === 30)) return TENS[w];
  return null;
}

/**
 * Try to parse a compound day from one or two words.
 * Returns { day, wordsConsumed, isOrdinal } or null.
 */
function parseDayWords(words, startIdx) {
  if (startIdx >= words.length) return null;

  const w1 = clean(words[startIdx]);

  // Single ordinal: "fifth", "twentieth", "5th"
  const ord = parseOrdinalDay(words[startIdx]);
  if (ord) return { day: ord, wordsConsumed: 1, isOrdinal: true };

  // Compound ordinal: "twenty" + "first" → 21
  if (TENS[w1] !== undefined && (TENS[w1] === 20 || TENS[w1] === 30)) {
    if (startIdx + 1 < words.length) {
      const w2 = clean(words[startIdx + 1]);
      const compound = w1 + '-' + w2;
      if (ORDINALS[compound] !== undefined) {
        return { day: ORDINALS[compound], wordsConsumed: 2, isOrdinal: true };
      }
      if (ORDINALS[w2] !== undefined && ORDINALS[w2] <= 9) {
        return { day: TENS[w1] + ORDINALS[w2], wordsConsumed: 2, isOrdinal: true };
      }
      if (ONES[w2] !== undefined && ONES[w2] >= 1 && ONES[w2] <= 9) {
        return { day: TENS[w1] + ONES[w2], wordsConsumed: 2, isOrdinal: false };
      }
    }
    if (TENS[w1] === 20 || TENS[w1] === 30) {
      return { day: TENS[w1], wordsConsumed: 1, isOrdinal: false };
    }
  }

  // Single cardinal: "seven", "15"
  const card = parseCardinalDay(words[startIdx]);
  if (card) return { day: card, wordsConsumed: 1, isOrdinal: false };

  return null;
}

/**
 * Check if a word is a month name.
 */
function isMonth(word) {
  return MONTHS.has(clean(word));
}

/**
 * Capitalize a month name: "march" → "March"
 */
function capitalizeMonth(word) {
  const w = clean(word);
  const idx = MONTH_NAMES.findIndex(m => m.toLowerCase() === w);
  return idx >= 0 ? MONTH_NAMES[idx] : word;
}

/**
 * Returns the ordinal suffix for a day number.
 */
function ordinalSuffix(n) {
  if (n >= 11 && n <= 13) return 'th';
  const last = n % 10;
  if (last === 1) return 'st';
  if (last === 2) return 'nd';
  if (last === 3) return 'rd';
  return 'th';
}

/**
 * Try to parse a year starting at a position.
 * Returns { year, wordsConsumed } or null.
 */
function parseYearAtPosition(wordStrings, idx) {
  // Lazy require to avoid circular dependency
  const { matchTwoThousand, matchCenturySuffix } = require('./patterns');
  let result = matchTwoThousand(wordStrings, idx);
  if (result) return result;
  result = matchCenturySuffix(wordStrings, idx);
  if (result) return result;
  // Also handle plain 4-digit numeric year
  const w = clean(wordStrings[idx]);
  if (/^\d{4}$/.test(w)) {
    const num = parseInt(w, 10);
    if (num >= 1900 && num <= 2099) {
      return { year: num, wordsConsumed: 1 };
    }
  }
  return null;
}

module.exports = {
  clean,
  parseCenturyPrefix,
  parseTwoDigitSuffix,
  parseOrdinalDay,
  parseCardinalDay,
  parseDayWords,
  isMonth,
  capitalizeMonth,
  ordinalSuffix,
  parseYearAtPosition,
};
