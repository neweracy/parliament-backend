'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { scoreResult, selectWinner } = require('../../lib/hybrid/scorer');

describe('lib/hybrid/scorer - scoreResult', () => {
  describe('empty and whitespace-only text scores 0', () => {
    it('returns 0 for empty string', () => {
      assert.equal(scoreResult(''), 0);
    });

    it('returns 0 for whitespace-only string', () => {
      assert.equal(scoreResult('   \t\n  '), 0);
    });

    it('returns 0 for null/undefined input', () => {
      assert.equal(scoreResult(null), 0);
      assert.equal(scoreResult(undefined), 0);
    });
  });

  describe('punctuation-only text scores low (alphaRatio = 0)', () => {
    it('pure punctuation tokens have zero alpha contribution', () => {
      const score = scoreResult('... !!! ???');
      // alphaRatio = 0 (no token has a letter), lengthScore = 3/20 = 0.15
      // score = 0.5*0 + 0.5*0.15 = 0.075
      assert.equal(score, 0.075);
    });

    it('single punctuation token scores very low', () => {
      const score = scoreResult('!!!');
      // alphaRatio = 0, lengthScore = 1/20 = 0.05, score = 0.025
      assert.equal(score, 0.025);
    });
  });

  describe('saturating length at 20 tokens', () => {
    it('lengthScore saturates at 1.0 when tokens >= 20', () => {
      const text = 'a b c d e f g h i j k l m n o p q r s t u v w x y';
      const score = scoreResult(text);
      // 25 tokens, all letters → alphaRatio = 1, lengthScore = 20/20 = 1.0
      // score = 0.5*1 + 0.5*1 = 1.0
      assert.equal(score, 1.0);
    });

    it('exactly 20 tokens also saturates', () => {
      const text = 'a b c d e f g h i j k l m n o p q r s t';
      const score = scoreResult(text);
      // 20 tokens, alphaRatio = 1, lengthScore = 1.0, score = 1.0
      assert.equal(score, 1.0);
    });

    it('fewer than 20 tokens gives proportional lengthScore', () => {
      const text = 'hello world test';
      const score = scoreResult(text);
      // 3 tokens, alphaRatio = 1, lengthScore = 3/20 = 0.15
      // score = 0.5*1 + 0.5*0.15 = 0.575
      assert.equal(score, 0.575);
    });
  });
});

describe('lib/hybrid/scorer - selectWinner', () => {
  describe('all-empty set returns null', () => {
    it('returns null when all transcripts are empty strings', () => {
      const results = [
        { language: 'tw', ok: true, transcript: '' },
        { language: 'ee', ok: true, transcript: '' },
        { language: 'gaa', ok: true, transcript: '' },
      ];
      assert.equal(selectWinner(results), null);
    });

    it('returns null when all results failed (ok: false)', () => {
      const results = [
        { language: 'tw', ok: false, transcript: '' },
        { language: 'ee', ok: false, transcript: 'some text' },
        { language: 'gaa', ok: false, transcript: '' },
      ];
      assert.equal(selectWinner(results), null);
    });

    it('returns null for empty array', () => {
      assert.equal(selectWinner([]), null);
    });

    it('returns null for null input', () => {
      assert.equal(selectWinner(null), null);
    });

    it('returns null when all transcripts are whitespace-only', () => {
      const results = [
        { language: 'tw', ok: true, transcript: '   ' },
        { language: 'ee', ok: true, transcript: '\t\n' },
        { language: 'gaa', ok: true, transcript: '  ' },
      ];
      assert.equal(selectWinner(results), null);
    });
  });

  describe('winner selection with valid results', () => {
    it('picks the result with the highest score', () => {
      const results = [
        { language: 'tw', ok: true, transcript: 'a' },
        { language: 'ee', ok: true, transcript: 'hello world this is a longer sentence with more words' },
        { language: 'gaa', ok: true, transcript: 'hi' },
      ];
      const winner = selectWinner(results);
      assert.equal(winner.language, 'ee');
    });

    it('uses tie-break order tw > ee > gaa on equal scores', () => {
      const results = [
        { language: 'gaa', ok: true, transcript: 'hello' },
        { language: 'tw', ok: true, transcript: 'hello' },
        { language: 'ee', ok: true, transcript: 'hello' },
      ];
      const winner = selectWinner(results);
      assert.equal(winner.language, 'tw');
    });

    it('returns score in the winner object', () => {
      const results = [
        { language: 'tw', ok: true, transcript: 'hello world' },
      ];
      const winner = selectWinner(results);
      assert.ok(winner.score > 0);
      assert.equal(winner.language, 'tw');
      assert.equal(winner.transcript, 'hello world');
    });
  });
});
