'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const {
  correctYears,
  correctYearsInText,
  matchTwoThousand,
  matchCenturySuffix,
  matchDecade,
  matchSplitNumericYear,
  matchOrdinalOfMonth,
  matchMonthDay,
  matchNumericDayMonthYear,
  ordinalSuffix,
  parseDayWords,
} = require('../../lib/location-correction/year-correction');

// Helper: build words array from string
function w(text) {
  return text.split(/\s+/).map((word, i) => ({
    word,
    start: i * 0.5,
    end: (i + 1) * 0.5,
    confidence: 0.95,
  }));
}

describe('year-correction', () => {
  describe('matchTwoThousand', () => {
    it('"two thousand and sixteen" → 2016', () => {
      const words = ['two', 'thousand', 'and', 'sixteen'];
      const result = matchTwoThousand(words, 0);
      assert.deepEqual(result, { year: 2016, wordsConsumed: 4 });
    });

    it('"two thousand twenty four" → 2024', () => {
      const words = ['two', 'thousand', 'twenty', 'four'];
      const result = matchTwoThousand(words, 0);
      assert.deepEqual(result, { year: 2024, wordsConsumed: 4 });
    });

    it('"two thousand" → 2000', () => {
      const words = ['two', 'thousand'];
      const result = matchTwoThousand(words, 0);
      assert.deepEqual(result, { year: 2000, wordsConsumed: 2 });
    });

    it('"two thousand and five" → 2005', () => {
      const words = ['two', 'thousand', 'and', 'five'];
      const result = matchTwoThousand(words, 0);
      assert.deepEqual(result, { year: 2005, wordsConsumed: 4 });
    });
  });

  describe('matchCenturySuffix', () => {
    it('"nineteen sixty" → 1960', () => {
      const words = ['nineteen', 'sixty'];
      const result = matchCenturySuffix(words, 0);
      assert.deepEqual(result, { year: 1960, wordsConsumed: 2 });
    });

    it('"nineteen ninety two" → 1992', () => {
      const words = ['nineteen', 'ninety', 'two'];
      const result = matchCenturySuffix(words, 0);
      assert.deepEqual(result, { year: 1992, wordsConsumed: 3 });
    });

    it('"twenty twenty four" → 2024', () => {
      const words = ['twenty', 'twenty', 'four'];
      const result = matchCenturySuffix(words, 0);
      assert.deepEqual(result, { year: 2024, wordsConsumed: 3 });
    });

    it('"eighteen forty" → 1840', () => {
      const words = ['eighteen', 'forty'];
      const result = matchCenturySuffix(words, 0);
      assert.deepEqual(result, { year: 1840, wordsConsumed: 2 });
    });
  });

  describe('matchDecade', () => {
    it('"nineteen seventies" → 1970s', () => {
      const words = ['nineteen', 'seventies'];
      const result = matchDecade(words, 0);
      assert.deepEqual(result, { decade: '1970s', wordsConsumed: 2 });
    });

    it('"twenty twenties" → 2020s', () => {
      const words = ['twenty', 'twenties'];
      const result = matchDecade(words, 0);
      assert.deepEqual(result, { decade: '2020s', wordsConsumed: 2 });
    });
  });

  describe('matchSplitNumericYear', () => {
    it('"19 92" → 1992', () => {
      const words = ['19', '92'];
      const result = matchSplitNumericYear(words, 0);
      assert.deepEqual(result, { year: 1992, wordsConsumed: 2 });
    });

    it('"20 24" → 2024', () => {
      const words = ['20', '24'];
      const result = matchSplitNumericYear(words, 0);
      assert.deepEqual(result, { year: 2024, wordsConsumed: 2 });
    });

    it('rejects non-year combinations like "12 345"', () => {
      const words = ['12', '345'];
      const result = matchSplitNumericYear(words, 0);
      assert.equal(result, null);
    });
  });

  describe('correctYears (words array)', () => {
    it('converts "twenty twenty four" to single "2024" word', () => {
      const words = w('in twenty twenty four the bill passed');
      const { words: result, corrections } = correctYears(words);
      const yearWord = result.find(r => r.word === '2024');
      assert.ok(yearWord, 'should have a 2024 word');
      assert.equal(yearWord.yearCorrected, true);
      assert.equal(corrections.length, 1);
      assert.equal(corrections[0].corrected, '2024');
      // Consumed words should be removed
      assert.equal(result.length, 5); // "in 2024 the bill passed"
    });

    it('converts "two thousand and sixteen" in context', () => {
      const words = w('since two thousand and sixteen we have');
      const { words: result } = correctYears(words);
      assert.equal(result.map(r => r.word).join(' '), 'since 2016 we have');
    });

    it('converts "nineteen ninety two"', () => {
      const words = w('in nineteen ninety two parliament');
      const { words: result } = correctYears(words);
      assert.equal(result.map(r => r.word).join(' '), 'in 1992 parliament');
    });

    it('handles decade "nineteen seventies"', () => {
      const words = w('the nineteen seventies were good');
      const { words: result } = correctYears(words);
      assert.equal(result.map(r => r.word).join(' '), 'the 1970s were good');
    });

    it('handles split numeric year "19 92"', () => {
      const words = w('in 19 92 the act');
      const { words: result } = correctYears(words);
      assert.equal(result.map(r => r.word).join(' '), 'in 1992 the act');
    });

    it('does not modify already-correct numerals', () => {
      const words = w('in 2024 the bill');
      const { words: result, corrections } = correctYears(words);
      assert.equal(corrections.length, 0);
      assert.equal(result.map(r => r.word).join(' '), 'in 2024 the bill');
    });
  });

  describe('correctYearsInText', () => {
    it('corrects years in plain text', () => {
      const { text } = correctYearsInText('since two thousand and sixteen we have progressed');
      assert.equal(text, 'since 2016 we have progressed');
    });

    it('handles multiple years in one sentence', () => {
      const { text, corrections } = correctYearsInText(
        'from nineteen ninety two to twenty twenty four'
      );
      assert.equal(text, 'from 1992 to 2024');
      assert.equal(corrections.length, 2);
    });
  });

  describe('ordinalSuffix', () => {
    it('1 → st', () => assert.equal(ordinalSuffix(1), 'st'));
    it('2 → nd', () => assert.equal(ordinalSuffix(2), 'nd'));
    it('3 → rd', () => assert.equal(ordinalSuffix(3), 'rd'));
    it('4 → th', () => assert.equal(ordinalSuffix(4), 'th'));
    it('11 → th', () => assert.equal(ordinalSuffix(11), 'th'));
    it('12 → th', () => assert.equal(ordinalSuffix(12), 'th'));
    it('13 → th', () => assert.equal(ordinalSuffix(13), 'th'));
    it('21 → st', () => assert.equal(ordinalSuffix(21), 'st'));
    it('22 → nd', () => assert.equal(ordinalSuffix(22), 'nd'));
    it('31 → st', () => assert.equal(ordinalSuffix(31), 'st'));
  });

  describe('matchOrdinalOfMonth (Pattern A)', () => {
    it('"fifth of March twenty twenty four" → "5th March 2024"', () => {
      const words = 'fifth of March twenty twenty four'.split(' ').map(w => ({ word: w }));
      const result = matchOrdinalOfMonth(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '5th March 2024');
      assert.equal(result.wordsConsumed, 6);
      assert.equal(result.entityType, 'full-date');
    });

    it('"seventh of July nineteen ninety two" → "7th July 1992"', () => {
      const words = 'seventh of July nineteen ninety two'.split(' ').map(w => ({ word: w }));
      const result = matchOrdinalOfMonth(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '7th July 1992');
      assert.equal(result.wordsConsumed, 6);
    });

    it('"twenty first of January two thousand and sixteen" → "21st January 2016"', () => {
      const words = 'twenty first of January two thousand and sixteen'.split(' ').map(w => ({ word: w }));
      const result = matchOrdinalOfMonth(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '21st January 2016');
      assert.equal(result.wordsConsumed, 8);
    });

    it('"fifth of March" without year → "5th March"', () => {
      const words = 'fifth of March and then'.split(' ').map(w => ({ word: w }));
      const result = matchOrdinalOfMonth(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '5th March');
      assert.equal(result.wordsConsumed, 3);
      assert.equal(result.entityType, 'partial-date');
    });

    it('"third March twenty twenty five" without "of" → "3rd March 2025"', () => {
      const words = 'third March twenty twenty five'.split(' ').map(w => ({ word: w }));
      const result = matchOrdinalOfMonth(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '3rd March 2025');
      assert.equal(result.wordsConsumed, 5);
    });
  });

  describe('matchMonthDay (Pattern B)', () => {
    it('"March fifth twenty twenty four" → "March 5th, 2024"', () => {
      const words = 'March fifth twenty twenty four'.split(' ').map(w => ({ word: w }));
      const result = matchMonthDay(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, 'March 5th, 2024');
      assert.equal(result.wordsConsumed, 5);
      assert.equal(result.entityType, 'full-date');
    });

    it('"January seven twenty twenty five" → "January 7th, 2025"', () => {
      const words = 'January seven twenty twenty five'.split(' ').map(w => ({ word: w }));
      const result = matchMonthDay(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, 'January 7th, 2025');
      assert.equal(result.wordsConsumed, 5);
    });

    it('"December twenty first" without year → "December 21st"', () => {
      const words = 'December twenty first and then'.split(' ').map(w => ({ word: w }));
      const result = matchMonthDay(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, 'December 21st');
      assert.equal(result.wordsConsumed, 3);
      assert.equal(result.entityType, 'partial-date');
    });

    it('"July fourth nineteen seventy six" → "July 4th, 1976"', () => {
      const words = 'July fourth nineteen seventy six'.split(' ').map(w => ({ word: w }));
      const result = matchMonthDay(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, 'July 4th, 1976');
      assert.equal(result.wordsConsumed, 5);
    });
  });

  describe('matchNumericDayMonthYear (Pattern C)', () => {
    it('"7 July 1992" → "7th July, 1992"', () => {
      const words = '7 July 1992'.split(' ').map(w => ({ word: w }));
      const result = matchNumericDayMonthYear(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '7th July, 1992');
      assert.equal(result.wordsConsumed, 3);
    });

    it('"21 January nineteen ninety two" → "21st January, 1992"', () => {
      const words = '21 January nineteen ninety two'.split(' ').map(w => ({ word: w }));
      const result = matchNumericDayMonthYear(words, 0);
      assert.ok(result);
      assert.equal(result.formatted, '21st January, 1992');
      assert.equal(result.wordsConsumed, 5);
    });

    it('rejects "7 July" without year (too aggressive)', () => {
      const words = '7 July was nice'.split(' ').map(w => ({ word: w }));
      const result = matchNumericDayMonthYear(words, 0);
      assert.equal(result, null);
    });
  });

  describe('full dates via correctYearsInText', () => {
    it('converts "fifth of March twenty twenty four" in sentence', () => {
      const { text } = correctYearsInText('on the fifth of March twenty twenty four we met');
      assert.equal(text, 'on the 5th March 2024 we met');
    });

    it('converts "March fifth twenty twenty four" in sentence', () => {
      const { text } = correctYearsInText('it was March fifth twenty twenty four');
      assert.equal(text, 'it was March 5th, 2024');
    });

    it('converts date and separate year in same sentence', () => {
      const { text, corrections } = correctYearsInText(
        'since fifth of March and again in twenty twenty four'
      );
      assert.equal(text, 'since 5th March and again in 2024');
      assert.equal(corrections.length, 2);
    });

    it('handles "the twenty first of January two thousand and sixteen"', () => {
      const { text } = correctYearsInText('the twenty first of January two thousand and sixteen was historic');
      assert.equal(text, 'the 21st January 2016 was historic');
    });
  });
});
