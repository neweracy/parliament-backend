'use strict';

/**
 * Golden Corpus Loader
 *
 * Reads corpus.json and ensures every case has a fully-populated `input_words`
 * array. Where a migrated case has no `input_words`, the loader synthesises one
 * from `input_transcript` using fixed 0.3s spacing and confidence 0.95,
 * matching what `test_regression_corpus.py` does today.
 *
 * @module fixtures/golden-corpus/loader
 */

const path = require('path');
const fs = require('fs');

const CORPUS_PATH = path.join(__dirname, 'corpus.json');

/** Default word duration in seconds for synthesised input_words. */
const WORD_DURATION = 0.3;

/** Default confidence for synthesised input_words. */
const DEFAULT_CONFIDENCE = 0.95;

/**
 * Synthesise an input_words array from an input_transcript string.
 * Each word gets 0.3s duration starting from 0.0, confidence 0.95.
 *
 * @param {string} transcript - The input transcript to split into words
 * @returns {Array<{word: string, start: number, end: number, confidence: number}>}
 */
function synthesiseInputWords(transcript) {
  const tokens = transcript.split(/\s+/).filter(Boolean);
  const words = [];
  let pos = 0.0;

  for (const token of tokens) {
    words.push({
      word: token,
      start: Math.round(pos * 1000) / 1000,
      end: Math.round((pos + WORD_DURATION) * 1000) / 1000,
      confidence: DEFAULT_CONFIDENCE,
    });
    pos += WORD_DURATION;
  }

  return words;
}

/**
 * Load the Golden Corpus and return all cases with fully-populated fields.
 * For any case missing `input_words`, synthesises them from `input_transcript`.
 *
 * @returns {Array<{id: string, description: string, category: string, input_transcript: string, input_words: Array, expected_transcript: string, should_correct: boolean, expected_entities: Array<string>}>}
 */
function loadCorpus() {
  const raw = fs.readFileSync(CORPUS_PATH, 'utf8');
  const data = JSON.parse(raw);

  return data.cases.map((c) => {
    const inputWords = c.input_words || synthesiseInputWords(c.input_transcript);
    return {
      id: c.id,
      description: c.description,
      category: c.category,
      input_transcript: c.input_transcript,
      input_words: inputWords,
      expected_transcript: c.expected_transcript,
      should_correct: c.should_correct,
      expected_entities: c.expected_entities || [],
    };
  });
}

/**
 * Load the raw corpus metadata (version, case count).
 *
 * @returns {{version: string, caseCount: number}}
 */
function loadCorpusMeta() {
  const raw = fs.readFileSync(CORPUS_PATH, 'utf8');
  const data = JSON.parse(raw);
  return { version: data.version, caseCount: data.cases.length };
}

module.exports = { loadCorpus, loadCorpusMeta, synthesiseInputWords };
