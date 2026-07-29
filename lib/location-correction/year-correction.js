/**
 * Year/Date Correction Post-Processor — Public Entry Point
 *
 * Fixes common ASR issues with spoken years and dates in transcripts:
 * - "twenty twenty four" → "2024"
 * - "two thousand and sixteen" → "2016"
 * - "nineteen sixty" → "1960"
 * - Decade references: "the nineteen seventies" → "the 1970s"
 * - Full date patterns: "fifth of March twenty twenty four" → "5th March 2024"
 *
 * Operates on the words[] array so word timings are preserved where possible.
 */

'use strict';

const { ordinalSuffix } = require('./years/parsers');
const { parseDayWords } = require('./years/parsers');
const {
  matchTwoThousand,
  matchCenturySuffix,
  matchDecade,
  matchSplitNumericYear,
  matchOrdinalOfMonth,
  matchMonthDay,
  matchNumericDayMonthYear,
} = require('./years/patterns');

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

  const tokens = text.split(/\s+/);
  const fakeWords = tokens.map(t => ({ word: t }));
  const { words: corrected, corrections } = correctYears(fakeWords);
  const newText = corrected.map(w => w.word).join(' ');
  return { text: newText, corrections };
}

module.exports = {
  correctYears,
  correctYearsInText,
  // Exposed for testing — re-exported from sub-modules
  matchTwoThousand,
  matchCenturySuffix,
  matchDecade,
  matchSplitNumericYear,
  matchOrdinalOfMonth,
  matchMonthDay,
  matchNumericDayMonthYear,
  parseDayWords,
  ordinalSuffix,
};
