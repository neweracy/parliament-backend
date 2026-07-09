'use strict';

/**
 * Fixed tie-break priority order for selectWinner.
 * tw wins ties over ee, ee wins ties over gaa.
 */
const TIE_BREAK_ORDER = ['tw', 'ee', 'gaa'];

/**
 * Deterministic score for a single correction result's transcript text.
 * Higher is better. Empty/whitespace-only text scores 0 (lowest).
 *
 * Formula:
 *   tokens = whitespace-split non-empty tokens of trimmed text
 *   alphaRatio = (tokens containing at least one Unicode letter) / tokens.length
 *   lengthScore = min(tokens.length, 20) / 20
 *   score = 0.5 * alphaRatio + 0.5 * lengthScore, clamped to [0, 1]
 *
 * @param {string} text
 * @returns {number}  Score in [0, 1].
 */
function scoreResult(text) {
  const trimmed = (text || '').trim();
  if (trimmed.length === 0) {
    return 0;
  }

  const tokens = trimmed.split(/\s+/).filter((t) => t.length > 0);
  if (tokens.length === 0) {
    return 0;
  }

  const hasLetter = /\p{L}/u;
  const alphaCount = tokens.filter((t) => hasLetter.test(t)).length;
  const alphaRatio = alphaCount / tokens.length;

  const lengthScore = Math.min(tokens.length, 20) / 20;

  const raw = 0.5 * alphaRatio + 0.5 * lengthScore;
  return Math.max(0, Math.min(1, raw));
}

/**
 * @typedef {Object} LanguageRaceResult
 * @property {string} language     'tw' | 'ee' | 'gaa'.
 * @property {boolean} ok          True when the Khaya call succeeded.
 * @property {string} transcript   Returned text ('' when failed or empty).
 */

/**
 * Selects the Winning_Result from a set of race results. Failed and
 * empty-transcript results score 0. Ties at the highest score are broken by
 * the fixed order tw > ee > gaa (tw wins).
 *
 * @param {LanguageRaceResult[]} results
 * @returns {{ language: string, transcript: string, score: number } | null}
 *   null when no result has a positive score (all failed/empty).
 */
function selectWinner(results) {
  if (!results || results.length === 0) {
    return null;
  }

  let bestScore = -1;
  let bestIdx = -1;

  for (let i = 0; i < results.length; i++) {
    const result = results[i];
    if (!result.ok) continue;

    const score = scoreResult(result.transcript);
    if (score <= 0) continue;

    if (score > bestScore) {
      bestScore = score;
      bestIdx = i;
    } else if (score === bestScore) {
      // Tie-break: lower index in TIE_BREAK_ORDER wins
      const currentPriority = TIE_BREAK_ORDER.indexOf(results[bestIdx].language);
      const challengerPriority = TIE_BREAK_ORDER.indexOf(result.language);
      if (challengerPriority !== -1 && (currentPriority === -1 || challengerPriority < currentPriority)) {
        bestIdx = i;
      }
    }
  }

  if (bestIdx === -1) {
    return null;
  }

  return {
    language: results[bestIdx].language,
    transcript: results[bestIdx].transcript,
    score: bestScore,
  };
}

module.exports = { scoreResult, selectWinner };
