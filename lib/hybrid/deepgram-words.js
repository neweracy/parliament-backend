'use strict';

/**
 * @typedef {Object} Word
 * @property {string} word        Word text.
 * @property {number} start       Start timestamp (seconds).
 * @property {number} end         End timestamp (seconds).
 * @property {number} confidence  Confidence in [0,1].
 */

/**
 * Extracts the ordered word list and audio duration from a raw Deepgram
 * response. Reads results.channels[0].alternatives[0].words[].
 *
 * @param {Object} deepgramResponse  Raw SDK response (has .result).
 * @returns {{ words: Word[], duration: number, transcript: string }}
 * @throws {Error} type=TranscriptionError when no word-level results exist.
 */
function extractWords(deepgramResponse) {
  const result = deepgramResponse && deepgramResponse.result;
  if (!result) {
    const err = new Error('No result in Deepgram response');
    err.type = 'TranscriptionError';
    throw err;
  }

  const channels = result.results && result.results.channels;
  if (!channels || !channels.length) {
    const err = new Error('No channels in Deepgram response');
    err.type = 'TranscriptionError';
    throw err;
  }

  const alternatives = channels[0].alternatives;
  if (!alternatives || !alternatives.length) {
    const err = new Error('No alternatives in Deepgram response');
    err.type = 'TranscriptionError';
    throw err;
  }

  const rawWords = alternatives[0].words;
  if (!rawWords || rawWords.length === 0) {
    const err = new Error('No word-level results in Deepgram response');
    err.type = 'TranscriptionError';
    throw err;
  }

  const words = rawWords.map((w) => ({
    word: w.word,
    start: w.start,
    end: w.end,
    confidence: w.confidence,
  }));

  const duration = (result.metadata && result.metadata.duration) || 0;
  const transcript = alternatives[0].transcript || '';

  return { words, duration, transcript };
}

module.exports = { extractWords };
