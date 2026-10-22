'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');

const { hybridSegmentsToWords } = require('../../lib/transcription-pipeline');

const DG_WORDS = [
  { word: 'mister', punctuated_word: 'Mister', start: 0.0, end: 0.4, confidence: 0.99, speaker: 0 },
  { word: 'speaker', punctuated_word: 'Speaker,', start: 0.4, end: 0.9, confidence: 0.98, speaker: 0 },
  { word: 'eti', punctuated_word: 'eti', start: 1.0, end: 1.2, confidence: 0.3, speaker: 0 },
  { word: 'sen', punctuated_word: 'sen', start: 1.2, end: 1.5, confidence: 0.2, speaker: 0 },
  { word: 'thank', punctuated_word: 'Thank', start: 2.0, end: 2.3, confidence: 0.97, speaker: 0 },
];

describe('hybridSegmentsToWords', () => {
  it('keeps original Deepgram word objects for uncorrected segments', () => {
    const segments = [
      { text: 'mister', start: 0.0, end: 0.4, corrected: false },
      { text: 'speaker', start: 0.4, end: 0.9, corrected: false },
    ];
    const words = hybridSegmentsToWords(segments, DG_WORDS);
    assert.deepEqual(words, [DG_WORDS[0], DG_WORDS[1]]);
    // A copy, not the same reference.
    assert.notEqual(words[0], DG_WORDS[0]);
  });

  it('expands a corrected Khaya block into tokens carrying the block span and no confidence', () => {
    const segments = [
      { text: 'mister', start: 0.0, end: 0.4, corrected: false },
      { text: 'speaker', start: 0.4, end: 0.9, corrected: false },
      { text: 'ɛte sɛn?', start: 0.75, end: 1.75, corrected: true, language: 'tw' },
      { text: 'thank', start: 2.0, end: 2.3, corrected: false },
    ];
    const words = hybridSegmentsToWords(segments, DG_WORDS);

    assert.deepEqual(
      words.map((w) => w.word),
      ['mister', 'speaker', 'ɛte', 'sɛn?', 'thank']
    );
    const khayaTokens = words.filter((w) => w.hybrid_corrected);
    assert.equal(khayaTokens.length, 2);
    for (const t of khayaTokens) {
      assert.equal(t.start, 0.75);
      assert.equal(t.end, 1.75);
      assert.equal(t.language, 'tw');
      assert.equal(t.confidence, undefined);
    }
    // The uncorrected word after the block still resolves to its Deepgram object.
    assert.deepEqual(words[4], DG_WORDS[4]);
  });

  it('token count matches the whitespace-split transcript text (editor alignment)', () => {
    const segments = [
      { text: 'mister', start: 0.0, end: 0.4, corrected: false },
      { text: '  ɛte   sɛn  ', start: 1.0, end: 1.5, corrected: true, language: 'tw' },
    ];
    const words = hybridSegmentsToWords(segments, DG_WORDS);
    const text = segments.map((s) => s.text.trim()).join(' ');
    assert.equal(words.length, text.split(/\s+/).filter(Boolean).length);
  });

  it('falls back to a minimal word when no Deepgram word matches', () => {
    const segments = [{ text: 'unknown', start: 9.0, end: 9.5, corrected: false }];
    assert.deepEqual(hybridSegmentsToWords(segments, DG_WORDS), [
      { word: 'unknown', start: 9.0, end: 9.5 },
    ]);
  });
});
