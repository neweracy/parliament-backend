/**
 * Year/Date Correction Post-Processor
 *
 * Fixes common ASR issues with spoken years and dates in transcripts:
 * - "twenty twenty four" → "2024"
 * - "two thousand and sixteen" → "2016"
 * - "nineteen sixty" → "1960"
 * - "nineteen ninety two" → "1992"
 * - Decade references: "the nineteen seventies" → "the 1970s"
 *
 * Full date patterns:
 * - "fifth of March twenty twenty four" → "5th March 2024"
 * - "March fifth twenty twenty four" → "March 5th, 2024"
 * - "the twenty first of January two thousand and sixteen" → "21st January 2016"
 * - "seventh of July nineteen ninety two" → "7th July 1992"
 * - "January seven twenty twenty five" → "January 7th, 2025"
 *
 * Operates on the words[] array so word timings are preserved where possible.
 */

'use strict';

// ---------------------------------------------------------------------------
// Number word mappings
// ---------------------------------------------------------------------------

const ONES = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5,
  six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15,
  sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
};

const TENS = {
  twenty: 20, thirty: 30, forty: 40, fifty: 50,
  sixty: 60, seventy: 70, eighty: 80, ninety: 90,
};

const MONTHS = new Set([
  'january', 'february', 'march', 'april', 'may', 'june',
  'july', 'august', 'september', 'october', 'november', 'december',
]);

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const ORDINALS = {
  first: 1, second: 2, third: 3, fourth: 4, fifth: 5,
  sixth: 6, seventh: 7, eighth: 8, ninth: 9, tenth: 10,
  eleventh: 11, twelfth: 12, thirteenth: 13, fourteenth: 14,
  fifteenth: 15, sixteenth: 16, seventeenth: 17, eighteenth: 18,
  nineteenth: 19, twentieth: 20, 'twenty-first': 21, 'twenty-second': 22,
  'twenty-third': 23, 'twenty-fourth': 24, 'twenty-fifth': 25,
  'twenty-sixth': 26, 'twenty-seventh': 27, 'twenty-eighth': 28,
  'twenty-ninth': 29, thirtieth: 30, 'thirty-first': 31,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function clean(word) {
  return (word || '').toLowerCase().replace(/[.,;:!?]/g, '').trim();
}

function isFillerWord(word) {
  const w = clean(word);
  return w === 'and' || w === 'oh';
}

/**
 * Try to parse a "century prefix" — e.g. "nineteen", "twenty", "eighteen"
 * These are the tens-digit representations for century (19xx, 20xx, 18xx).
 */
function parseCenturyPrefix(word) {
  const w = clean(word);
  if (TENS[w] !== undefined) return TENS[w]; // twenty=20, nineteen would be in ONES
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
 * E.g. "sixty" → 60, "ninety two" → 92, "oh five" → 05, "twelve" → 12
 * Returns { value, wordsConsumed } or null.
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

// ---------------------------------------------------------------------------
// Pattern matchers — each returns { year, wordsConsumed } or null
// ---------------------------------------------------------------------------

/**
 * Pattern 1: "two thousand [and] X" → 2000 + X
 * E.g. "two thousand and sixteen" → 2016
 *      "two thousand twenty four" → 2024
 *      "two thousand" → 2000
 */
function matchTwoThousand(words, idx) {
  const w1 = clean(words[idx]);
  if (w1 !== 'two') return null;
  if (idx + 1 >= words.length) return null;

  const w2 = clean(words[idx + 1]);
  if (w2 !== 'thousand') return null;

  let nextIdx = idx + 2;
  let consumed = 2;

  // Skip optional "and"
  if (nextIdx < words.length && clean(words[nextIdx]) === 'and') {
    nextIdx++;
    consumed++;
  }

  // Try to parse the suffix
  const suffix = parseTwoDigitSuffix(words, nextIdx);
  if (suffix) {
    const year = 2000 + suffix.value;
    if (year >= 2000 && year <= 2099) {
      return { year, wordsConsumed: consumed + suffix.wordsConsumed };
    }
  }

  // Just "two thousand" = 2000
  return { year: 2000, wordsConsumed: consumed };
}

/**
 * Pattern 2: "nineteen/eighteen/twenty XX" → 19XX/18XX/20XX
 * E.g. "nineteen sixty" → 1960
 *      "nineteen ninety two" → 1992
 *      "twenty twenty four" → 2024
 *
 * Guard: "twenty" + single digit (1-9) is ambiguous — "twenty six" usually
 * means the number 26, not the year 2006. Only accept "twenty" as a century
 * prefix when the suffix is >= 10 (e.g. "twenty twelve" → 2012,
 * "twenty twenty" → 2020). For 200X years, use the "two thousand" pattern.
 */
function matchCenturySuffix(words, idx) {
  const prefix = parseCenturyPrefix(clean(words[idx]));
  if (prefix === null) return null;

  // Need a suffix (otherwise "twenty" alone is just a number, not a year)
  const suffix = parseTwoDigitSuffix(words, idx + 1);
  if (!suffix) return null;

  // Guard: when the prefix is "twenty" (= 20), reject single-digit suffixes
  // to avoid "twenty six" → 2006. These should use "two thousand and six".
  if (prefix === 20 && suffix.value < 10) return null;

  const year = prefix * 100 + suffix.value;

  // Validate as a plausible year (1300–2099)
  if (year < 1300 || year > 2099) return null;

  return { year, wordsConsumed: 1 + suffix.wordsConsumed };
}

/**
 * Pattern 3: Decade references — "the nineteen seventies" → "the 1970s"
 * Also: "eighties" → "80s" (context-dependent, only after a century prefix)
 */
function matchDecade(words, idx) {
  const prefix = parseCenturyPrefix(clean(words[idx]));
  if (prefix === null) return null;
  if (idx + 1 >= words.length) return null;

  const nextWord = clean(words[idx + 1]);
  const decadeMap = {
    twenties: 20, thirties: 30, forties: 40, fifties: 50,
    sixties: 60, seventies: 70, eighties: 80, nineties: 90,
  };

  if (decadeMap[nextWord] !== undefined) {
    const year = prefix * 100 + decadeMap[nextWord];
    return { decade: `${year}s`, wordsConsumed: 2 };
  }

  return null;
}

/**
 * Pattern 4: Bare numeric-sounding fragments that Deepgram sometimes outputs
 * E.g. "19 92" (two separate tokens) → "1992"
 */
function matchSplitNumericYear(words, idx) {
  const w1 = clean(words[idx]);
  const w2 = idx + 1 < words.length ? clean(words[idx + 1]) : null;

  if (!w2) return null;

  // Both must be pure digits
  if (!/^\d+$/.test(w1) || !/^\d+$/.test(w2)) return null;

  const combined = w1 + w2;
  const num = parseInt(combined, 10);

  // Must be a 4-digit year in plausible range
  if (combined.length === 4 && num >= 1900 && num <= 2099) {
    return { year: num, wordsConsumed: 2 };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

/**
 * Capitalize a month name: "march" → "March"
 */
function capitalizeMonth(word) {
  const w = clean(word);
  const idx = MONTH_NAMES.findIndex(m => m.toLowerCase() === w);
  return idx >= 0 ? MONTH_NAMES[idx] : word;
}

/**
 * Returns the ordinal suffix for a day number: 1→"st", 2→"nd", 3→"rd", else "th"
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
 * Try to parse an ordinal day from a word.
 * Handles word ordinals ("fifth" → 5) and numeric ordinals ("5th" → 5).
 * Returns the day number (1–31) or null.
 */
function parseOrdinalDay(word) {
  const w = clean(word);
  // Word ordinal
  if (ORDINALS[w] !== undefined) return ORDINALS[w];
  // Numeric ordinal like "5th", "21st", "3rd"
  const numMatch = w.match(/^(\d{1,2})(st|nd|rd|th)$/);
  if (numMatch) {
    const num = parseInt(numMatch[1], 10);
    if (num >= 1 && num <= 31) return num;
  }
  return null;
}

/**
 * Try to parse a cardinal day (1–31) from a word.
 * Handles word cardinals ("seven" → 7) and plain numbers ("7" → 7).
 */
function parseCardinalDay(word) {
  const w = clean(word);
  // Plain number
  if (/^\d{1,2}$/.test(w)) {
    const num = parseInt(w, 10);
    if (num >= 1 && num <= 31) return num;
  }
  // Word cardinal (one through nineteen for days, plus twenty/thirty + ones)
  if (ONES[w] !== undefined && ONES[w] >= 1 && ONES[w] <= 19) return ONES[w];
  if (TENS[w] !== undefined && (TENS[w] === 20 || TENS[w] === 30)) return TENS[w];
  return null;
}

/**
 * Try to parse a compound day from one or two words (e.g. "twenty" "first" → 21).
 * Returns { day, wordsConsumed } or null.
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
      // Hyphenated: "twenty-first" already in ORDINALS
      const compound = w1 + '-' + w2;
      if (ORDINALS[compound] !== undefined) {
        return { day: ORDINALS[compound], wordsConsumed: 2, isOrdinal: true };
      }
      // "twenty" + "first" as separate words
      if (ORDINALS[w2] !== undefined && ORDINALS[w2] <= 9) {
        return { day: TENS[w1] + ORDINALS[w2], wordsConsumed: 2, isOrdinal: true };
      }
      // Cardinal: "twenty" + "one" → 21 (used for day in "January twenty one")
      if (ONES[w2] !== undefined && ONES[w2] >= 1 && ONES[w2] <= 9) {
        return { day: TENS[w1] + ONES[w2], wordsConsumed: 2, isOrdinal: false };
      }
    }
    // Just "twenty" or "thirty" as a day
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
 * Try to parse a year starting at a position. Uses the existing year patterns.
 * Returns { year, wordsConsumed } or null.
 */
function parseYearAtPosition(wordStrings, idx) {
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

// ---------------------------------------------------------------------------
// Full date pattern matchers
// ---------------------------------------------------------------------------

/**
 * Pattern A: "Ordinal [of] Month [Year]"
 * E.g. "fifth of March twenty twenty four" → "5th March 2024"
 *      "the twenty first of January two thousand and sixteen" → "21st January 2016"
 *      "seventh of July" → "7th July"
 */
function matchOrdinalOfMonth(words, idx) {
  const wordStrings = words.map(w => typeof w === 'string' ? w : w.word || w);

  // Parse day (ordinal or compound ordinal)
  const dayResult = parseDayWords(wordStrings, idx);
  if (!dayResult || !dayResult.isOrdinal) return null;
  if (dayResult.day < 1 || dayResult.day > 31) return null;

  let pos = idx + dayResult.wordsConsumed;

  // Optional "of"
  if (pos < wordStrings.length && clean(wordStrings[pos]) === 'of') {
    pos++;
  }

  // Month
  if (pos >= wordStrings.length || !isMonth(wordStrings[pos])) return null;
  const month = capitalizeMonth(wordStrings[pos]);
  pos++;

  // Optional year
  let yearStr = '';
  let yearConsumed = 0;
  if (pos < wordStrings.length) {
    const yearResult = parseYearAtPosition(wordStrings, pos);
    if (yearResult) {
      yearStr = ' ' + String(yearResult.year);
      yearConsumed = yearResult.wordsConsumed;
    }
  }

  const day = dayResult.day;
  const formatted = `${day}${ordinalSuffix(day)} ${month}${yearStr}`;
  const totalConsumed = (pos - idx) + yearConsumed;

  return { formatted, wordsConsumed: totalConsumed, entityType: yearStr ? 'full-date' : 'partial-date' };
}

/**
 * Pattern B: "Month Ordinal/Cardinal [Year]"
 * E.g. "March fifth twenty twenty four" → "March 5th, 2024"
 *      "January seven twenty twenty five" → "January 7th, 2025"
 *      "December twenty first" → "December 21st"
 */
function matchMonthDay(words, idx) {
  const wordStrings = words.map(w => typeof w === 'string' ? w : w.word || w);

  // Must start with a month
  if (!isMonth(wordStrings[idx])) return null;
  const month = capitalizeMonth(wordStrings[idx]);

  let pos = idx + 1;

  // Before parsing as "Month Day", check if the next token is actually a
  // century prefix that starts a year (e.g. "june nineteen ninety two" should
  // be "June 1992", not "June 19th" + leftover "ninety two").
  if (pos < wordStrings.length) {
    const yearDirect = parseYearAtPosition(wordStrings, pos);
    if (yearDirect) {
      const formatted = `${month} ${yearDirect.year}`;
      const totalConsumed = 1 + yearDirect.wordsConsumed;
      return { formatted, wordsConsumed: totalConsumed, entityType: 'month-year' };
    }
  }

  // Parse day (ordinal or cardinal)
  if (pos >= wordStrings.length) return null;
  const dayResult = parseDayWords(wordStrings, pos);
  if (!dayResult) return null;
  if (dayResult.day < 1 || dayResult.day > 31) return null;

  pos += dayResult.wordsConsumed;

  // Optional year
  let yearStr = '';
  let yearConsumed = 0;
  if (pos < wordStrings.length) {
    const yearResult = parseYearAtPosition(wordStrings, pos);
    if (yearResult) {
      yearStr = ', ' + String(yearResult.year);
      yearConsumed = yearResult.wordsConsumed;
    }
  }

  const day = dayResult.day;
  const formatted = `${month} ${day}${ordinalSuffix(day)}${yearStr}`;
  const totalConsumed = (pos - idx) + yearConsumed;

  return { formatted, wordsConsumed: totalConsumed, entityType: yearStr ? 'full-date' : 'partial-date' };
}

/**
 * Pattern C: "Day Month Year" with numeric day
 * E.g. "7 July 1992" (already partially numeric from ASR)
 * Only triggers if followed by a year to avoid false positives.
 */
function matchNumericDayMonthYear(words, idx) {
  const wordStrings = words.map(w => typeof w === 'string' ? w : w.word || w);
  const w1 = clean(wordStrings[idx]);

  // Must be a 1-2 digit number
  if (!/^\d{1,2}$/.test(w1)) return null;
  const day = parseInt(w1, 10);
  if (day < 1 || day > 31) return null;

  // Next must be a month
  if (idx + 1 >= wordStrings.length || !isMonth(wordStrings[idx + 1])) return null;
  const month = capitalizeMonth(wordStrings[idx + 1]);

  // Must have a year after to confirm this is a date (avoid "3 March" alone being too aggressive)
  if (idx + 2 >= wordStrings.length) return null;
  const yearResult = parseYearAtPosition(wordStrings, idx + 2);
  if (!yearResult) return null;

  const formatted = `${day}${ordinalSuffix(day)} ${month}, ${yearResult.year}`;
  const totalConsumed = 2 + yearResult.wordsConsumed;

  return { formatted, wordsConsumed: totalConsumed, entityType: 'full-date' };
}

// ---------------------------------------------------------------------------
// Main correction function
// ---------------------------------------------------------------------------

/**
 * Corrects year/date expressions in the words array.
 *
 * @param {Array<{ word: string, start?: number, end?: number, confidence?: number }>} words
 * @returns {{ words: Array, corrections: Array<{ original: string, corrected: string, index: number }> }}
 */
function correctYears(words) {
  if (!words || words.length === 0) {
    return { words, corrections: [] };
  }

  const corrections = [];
  const result = [...words];
  const consumed = new Set();

  for (let i = 0; i < result.length; i++) {
    if (consumed.has(i)) continue;

    // === Full date patterns (higher priority — more specific) ===

    // Pattern A: "fifth of March twenty twenty four"
    const dateA = matchOrdinalOfMonth(result, i);
    if (dateA) {
      const originalWords = [];
      for (let j = i; j < i + dateA.wordsConsumed; j++) {
        originalWords.push(result[j].word);
      }
      const original = originalWords.join(' ');
      const lastWord = result[i + dateA.wordsConsumed - 1];

      result[i] = {
        ...result[i],
        word: dateA.formatted,
        end: lastWord.end || result[i].end,
        yearCorrected: true,
      };

      corrections.push({
        original,
        corrected: dateA.formatted,
        index: i,
        entityKind: 'date',
        entityType: dateA.entityType,
      });

      for (let j = i + 1; j < i + dateA.wordsConsumed; j++) {
        consumed.add(j);
      }
      continue;
    }

    // Pattern B: "March fifth twenty twenty four"
    const dateB = matchMonthDay(result, i);
    if (dateB) {
      const originalWords = [];
      for (let j = i; j < i + dateB.wordsConsumed; j++) {
        originalWords.push(result[j].word);
      }
      const original = originalWords.join(' ');
      const lastWord = result[i + dateB.wordsConsumed - 1];

      result[i] = {
        ...result[i],
        word: dateB.formatted,
        end: lastWord.end || result[i].end,
        yearCorrected: true,
      };

      corrections.push({
        original,
        corrected: dateB.formatted,
        index: i,
        entityKind: 'date',
        entityType: dateB.entityType,
      });

      for (let j = i + 1; j < i + dateB.wordsConsumed; j++) {
        consumed.add(j);
      }
      continue;
    }

    // Pattern C: "7 July 1992" (numeric day + month + year)
    const dateC = matchNumericDayMonthYear(result, i);
    if (dateC) {
      const originalWords = [];
      for (let j = i; j < i + dateC.wordsConsumed; j++) {
        originalWords.push(result[j].word);
      }
      const original = originalWords.join(' ');
      const lastWord = result[i + dateC.wordsConsumed - 1];

      result[i] = {
        ...result[i],
        word: dateC.formatted,
        end: lastWord.end || result[i].end,
        yearCorrected: true,
      };

      corrections.push({
        original,
        corrected: dateC.formatted,
        index: i,
        entityKind: 'date',
        entityType: dateC.entityType,
      });

      for (let j = i + 1; j < i + dateC.wordsConsumed; j++) {
        consumed.add(j);
      }
      continue;
    }

    // === Year-only patterns ===

    // Try each pattern in priority order
    let match = null;

    // Pattern 1: "two thousand..."
    match = matchTwoThousand(result.map(w => w.word), i);

    // Pattern 2: "nineteen/twenty XX"
    if (!match) {
      match = matchCenturySuffix(result.map(w => w.word), i);
    }

    // Pattern 4: Split numeric "19 92"
    if (!match) {
      match = matchSplitNumericYear(result.map(w => w.word), i);
    }

    if (match && match.year) {
      const yearStr = String(match.year);
      const originalWords = [];
      for (let j = i; j < i + match.wordsConsumed; j++) {
        originalWords.push(result[j].word);
      }
      const original = originalWords.join(' ');

      // Don't correct if it's already the right numeral
      if (original === yearStr) {
        i += match.wordsConsumed - 1;
        continue;
      }

      // Merge into the first word's slot, extend end time to last consumed word
      const lastWord = result[i + match.wordsConsumed - 1];
      result[i] = {
        ...result[i],
        word: yearStr,
        end: lastWord.end || result[i].end,
        yearCorrected: true,
      };

      corrections.push({
        original,
        corrected: yearStr,
        index: i,
        entityKind: 'date',
        entityType: 'year',
      });

      // Mark subsequent words as consumed
      for (let j = i + 1; j < i + match.wordsConsumed; j++) {
        consumed.add(j);
      }
      continue;
    }

    // Pattern 3: Decade "nineteen seventies"
    const decadeMatch = matchDecade(result.map(w => w.word), i);
    if (decadeMatch) {
      const originalWords = [];
      for (let j = i; j < i + decadeMatch.wordsConsumed; j++) {
        originalWords.push(result[j].word);
      }
      const original = originalWords.join(' ');
      const lastWord = result[i + decadeMatch.wordsConsumed - 1];

      result[i] = {
        ...result[i],
        word: decadeMatch.decade,
        end: lastWord.end || result[i].end,
        yearCorrected: true,
      };

      corrections.push({
        original,
        corrected: decadeMatch.decade,
        index: i,
        entityKind: 'date',
        entityType: 'decade',
      });

      for (let j = i + 1; j < i + decadeMatch.wordsConsumed; j++) {
        consumed.add(j);
      }
      continue;
    }
  }

  // Remove consumed words from result
  const finalWords = result.filter((_, idx) => !consumed.has(idx));

  return { words: finalWords, corrections };
}

/**
 * Corrects year/date expressions in plain text.
 *
 * @param {string} text
 * @returns {{ text: string, corrections: Array }}
 */
function correctYearsInText(text) {
  if (!text || !text.trim()) return { text, corrections: [] };

  // Build a fake words array from the text tokens
  const tokens = text.split(/\s+/);
  const fakeWords = tokens.map(t => ({ word: t }));
  const { words: corrected, corrections } = correctYears(fakeWords);
  const newText = corrected.map(w => w.word).join(' ');
  return { text: newText, corrections };
}

module.exports = {
  correctYears,
  correctYearsInText,
  // Exposed for testing
  matchTwoThousand,
  matchCenturySuffix,
  matchDecade,
  matchSplitNumericYear,
  matchOrdinalOfMonth,
  matchMonthDay,
  matchNumericDayMonthYear,
  parseTwoDigitSuffix,
  parseCenturyPrefix,
  parseDayWords,
  parseOrdinalDay,
  parseCardinalDay,
  ordinalSuffix,
  capitalizeMonth,
  isMonth,
  ONES,
  TENS,
  MONTHS,
  MONTH_NAMES,
  ORDINALS,
};
