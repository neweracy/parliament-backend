'use strict';

/**
 * @typedef {import('./deepgram-words').Word} Word
 */

/**
 * @typedef {Object} ClassifiedWord
 * @property {Word} word
 * @property {number} index    Position in the original ordered word list.
 * @property {boolean} isLow   True when word.confidence < threshold.
 */

/**
 * Classifies each Word as low- or high-confidence against the threshold.
 * A Word is low-confidence when confidence < threshold (strict).
 *
 * @param {Word[]} words
 * @param {number} threshold  Confidence_Threshold in [0,1].
 * @returns {ClassifiedWord[]}  Same length and order as input.
 */
function classifyWords(words, threshold) {
  return words.map((word, index) => ({
    word,
    index,
    isLow: word.confidence < threshold,
  }));
}

module.exports = { classifyWords };
