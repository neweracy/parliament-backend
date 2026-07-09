'use strict';

/**
 * @typedef {Object} LanguageRaceResult
 * @property {string} language     Candidate_Language code: 'tw' | 'ee' | 'gaa'.
 * @property {boolean} ok          True when the Khaya call succeeded.
 * @property {string} transcript   Returned text ('' when failed or empty).
 * @property {Error} [error]       Present when ok === false.
 */

const CANDIDATE_LANGUAGES = ['tw', 'ee', 'gaa'];

/**
 * Sends one audio slice to the Correction_Engine once per Candidate_Language.
 * Runs the three calls concurrently and tolerates partial failure: a rejected
 * language yields { ok: false } rather than aborting the race.
 *
 * @param {Buffer} sliceBuffer
 * @param {string} mimetype   'audio/mpeg'
 * @param {(buf: Buffer, mime: string, lang: string) => Promise<{transcript: string}>} khayaTranscribe
 * @returns {Promise<LanguageRaceResult[]>}  One entry per candidate language.
 */
async function raceLanguages(sliceBuffer, mimetype, khayaTranscribe) {
  const promises = CANDIDATE_LANGUAGES.map((language) =>
    khayaTranscribe(sliceBuffer, mimetype, language)
      .then((result) => ({
        language,
        ok: true,
        transcript: (result && result.transcript) || '',
      }))
      .catch((error) => ({
        language,
        ok: false,
        transcript: '',
        error,
      }))
  );

  const results = await Promise.allSettled(promises);

  return results.map((settled) => settled.value);
}

module.exports = { raceLanguages, CANDIDATE_LANGUAGES };
