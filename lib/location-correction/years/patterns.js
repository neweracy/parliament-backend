/**
 * Pattern matchers for the Year/Date Correction Engine.
 *
 * Exports: matchTwoThousand, matchCenturySuffix, matchDecade,
 *          matchSplitNumericYear, matchOrdinalOfMonth, matchMonthDay,
 *          matchNumericDayMonthYear
 */

'use strict';

const {
  clean,
  parseCenturyPrefix,
  parseTwoDigitSuffix,
  parseDayWords,
  isMonth,
  capitalizeMonth,
  ordinalSuffix,
  parseYearAtPosition,
} = require('./parsers');

// ---------------------------------------------------------------------------
// Pattern matchers — each returns { year, wordsConsumed } or null (year-only)
// or { formatted, wordsConsumed, entityType } (date patterns)
// ---------------------------------------------------------------------------

/**
 * Pattern 1: "two thousand [and] X" → 2000 + X
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
 *
 * Guard: "twenty" + single digit (1-9) is ambiguous — only accept "twenty"
 * as a century prefix when the suffix is >= 10.
 */
function matchCenturySuffix(words, idx) {
  const prefix = parseCenturyPrefix(clean(words[idx]));
  if (prefix === null) return null;

  const suffix = parseTwoDigitSuffix(words, idx + 1);
  if (!suffix) return null;

  // Guard: when the prefix is "twenty" (= 20), reject single-digit suffixes
  if (prefix === 20 && suffix.value < 10) return null;

  const year = prefix * 100 + suffix.value;

  // Validate as a plausible year (1300–2099)
  if (year < 1300 || year > 2099) return null;

  return { year, wordsConsumed: 1 + suffix.wordsConsumed };
}

/**
 * Pattern 3: Decade references — "the nineteen seventies" → "the 1970s"
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
 * Pattern 4: Bare numeric-sounding fragments — "19 92" → "1992"
 */
function matchSplitNumericYear(words, idx) {
  const w1 = clean(words[idx]);
  const w2 = idx + 1 < words.length ? clean(words[idx + 1]) : null;

  if (!w2) return null;

  if (!/^\d+$/.test(w1) || !/^\d+$/.test(w2)) return null;

  const combined = w1 + w2;
  const num = parseInt(combined, 10);

  if (combined.length === 4 && num >= 1900 && num <= 2099) {
    return { year: num, wordsConsumed: 2 };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Full date pattern matchers
// ---------------------------------------------------------------------------

/**
 * Pattern A: "Ordinal [of] Month [Year]"
 */
function matchOrdinalOfMonth(words, idx) {
  const wordStrings = words.map(w => typeof w === 'string' ? w : w.word || w);

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
 * Pattern B: "Month Ordinal/Cardinal [Year]" or "Month Year"
 */
function matchMonthDay(words, idx) {
  const wordStrings = words.map(w => typeof w === 'string' ? w : w.word || w);

  if (!isMonth(wordStrings[idx])) return null;
  const month = capitalizeMonth(wordStrings[idx]);

  let pos = idx + 1;

  // Before parsing as "Month Day", check if the next token is actually
  // a century prefix that starts a year
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
 */
function matchNumericDayMonthYear(words, idx) {
  const wordStrings = words.map(w => typeof w === 'string' ? w : w.word || w);
  const w1 = clean(wordStrings[idx]);

  if (!/^\d{1,2}$/.test(w1)) return null;
  const day = parseInt(w1, 10);
  if (day < 1 || day > 31) return null;

  if (idx + 1 >= wordStrings.length || !isMonth(wordStrings[idx + 1])) return null;
  const month = capitalizeMonth(wordStrings[idx + 1]);

  if (idx + 2 >= wordStrings.length) return null;
  const yearResult = parseYearAtPosition(wordStrings, idx + 2);
  if (!yearResult) return null;

  const formatted = `${day}${ordinalSuffix(day)} ${month}, ${yearResult.year}`;
  const totalConsumed = 2 + yearResult.wordsConsumed;

  return { formatted, wordsConsumed: totalConsumed, entityType: 'full-date' };
}

module.exports = {
  matchTwoThousand,
  matchCenturySuffix,
  matchDecade,
  matchSplitNumericYear,
  matchOrdinalOfMonth,
  matchMonthDay,
  matchNumericDayMonthYear,
};
