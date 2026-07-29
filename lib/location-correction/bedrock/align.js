/**
 * LCS alignment and correction application for Bedrock post-processing.
 *
 * Exports: applyAlignedCorrectionsWithMap, chunkWords
 */

'use strict';

/**
 * Splits transcript into chunks of ~300 array entries each for Bedrock processing.
 */
function chunkWords(words, chunkSize = 300) {
  const chunks = [];
  for (let i = 0; i < words.length; i += chunkSize) {
    const slice = words.slice(i, i + chunkSize);
    const tokenMap = [];
    for (let k = 0; k < slice.length; k++) {
      const tokenCount = (slice[k].word || '').split(/\s+/).filter(Boolean).length || 1;
      for (let t = 0; t < tokenCount; t++) {
        tokenMap.push(i + k);
      }
    }
    chunks.push({
      segment: slice.map(w => w.word).join(' '),
      startIdx: i,
      endIdx: Math.min(i + chunkSize - 1, words.length - 1),
      tokenMap,
    });
  }
  return chunks;
}

/**
 * Token-map-aware variant of LCS alignment. Uses the tokenMap to resolve each
 * original token position back to the correct words[] array index.
 *
 * @param {Array} words        The master words[] array (mutated in place).
 * @param {string[]} originalTokens  Whitespace-split tokens from chunk.segment.
 * @param {string[]} correctedTokens Whitespace-split tokens from Bedrock response.
 * @param {number[]} tokenMap   Maps each originalTokens index → words[] array index.
 * @returns {number} count of corrections applied
 */
function applyAlignedCorrectionsWithMap(words, originalTokens, correctedTokens, tokenMap) {
  const n = originalTokens.length;
  const m = correctedTokens.length;
  const a = originalTokens.map(w => w.toLowerCase());
  const b = correctedTokens.map(w => w.toLowerCase());

  // Standard LCS DP
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Walk back to recover aligned pairs
  let i = n, j = m;
  const pairs = [];
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      pairs.push([i - 1, j - 1]);
      i--; j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }
  pairs.reverse();

  let applied = 0;

  // Apply anchor-point corrections via tokenMap
  for (const [oi, ci] of pairs) {
    if (oi < tokenMap.length) {
      const wordIdx = tokenMap[oi];
      if (wordIdx < words.length && originalTokens[oi] !== correctedTokens[ci]) {
        const entryStart = tokenMap.indexOf(wordIdx);
        const entryEnd = tokenMap.lastIndexOf(wordIdx);
        if (entryStart === oi && entryEnd === oi) {
          words[wordIdx] = { ...words[wordIdx], word: correctedTokens[ci], bedrockCorrected: true };
          applied++;
        }
      }
    }
  }

  // Handle same-sized gaps between anchors (word-for-word corrections)
  let prevOrig = -1;
  let prevCorr = -1;
  const boundaries = [...pairs, [n, m]];

  for (const [oi, ci] of boundaries) {
    const origGap = oi - prevOrig - 1;
    const corrGap = ci - prevCorr - 1;
    if (origGap > 0 && origGap === corrGap) {
      for (let k = 0; k < origGap; k++) {
        const origIdx = prevOrig + 1 + k;
        const corrIdx = prevCorr + 1 + k;
        if (origIdx < tokenMap.length) {
          const wordIdx = tokenMap[origIdx];
          if (wordIdx < words.length && originalTokens[origIdx] !== correctedTokens[corrIdx]) {
            const entryStart = tokenMap.indexOf(wordIdx);
            if (origIdx === entryStart) {
              words[wordIdx] = { ...words[wordIdx], word: correctedTokens[corrIdx], bedrockCorrected: true };
              applied++;
            }
          }
        }
      }
    }
    prevOrig = oi;
    prevCorr = ci;
  }

  return applied;
}

module.exports = { chunkWords, applyAlignedCorrectionsWithMap };
