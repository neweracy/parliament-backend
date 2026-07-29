'use strict';

const { extractWords } = require('./deepgram-words');
const { classifyWords } = require('./confidence-detector');
const { groupLowConfidence, buildBundles } = require('./segment-grouper');
const { batchBundles } = require('./batcher');
const { raceLanguages } = require('./language-race');
const { selectWinner } = require('./scorer');
const { reassemble } = require('./reassembler');

/**
 * @typedef {import('./deepgram-words').Word} Word
 * @typedef {import('./reassembler').UnifiedSegment} UnifiedSegment
 * @typedef {import('./config').HybridConfig} HybridConfig
 */

/**
 * @typedef {Object} HybridDeps
 * @property {(req: {buffer: Buffer, mimetype: string}) => Promise<Object>} transcribePrimary
 *   Wraps the existing Deepgram transcribeAudio with word output. Returns the raw Deepgram response (has .result).
 * @property {(buf: Buffer, mime: string, lang: string) => Promise<{transcript: string}>} khayaTranscribe
 *   The existing khayaProvider.transcribe.
 * @property {(buf: Buffer, ranges: Array<{start: number, end: number}>) => Promise<{buffer: Buffer, mimetype: string}>} sliceAndConcatAudio
 *   Extracts and concatenates all low-confidence ranges into a single clip.
 * @property {() => boolean} khayaConfigured  khayaProvider.getApiKey() != null.
 */

/**
 * @typedef {Object} CorrectionStats
 * @property {number} segmentsDetected     Count of low-confidence segments detected.
 * @property {boolean} corrected           True when at least one batch correction was applied.
 * @property {string|null} language        Winning Candidate_Language(s); comma-joined if batches differ, or null when skipped.
 * @property {boolean} correctionSkipped   True when Khaya was unavailable / produced nothing.
 */

/**
 * @typedef {Object} HybridResult
 * @property {string} transcript                 Flattened Unified_Transcript text.
 * @property {UnifiedSegment[]} segments
 * @property {Word[]} words                       Reassembled word-equivalent list.
 * @property {number} duration
 * @property {Object} metadata                    Includes correctionStats + config echo.
 */

/**
 * Builds a ConfigurationError carrying the shared error envelope fields.
 *
 * @returns {Error}
 */
function configurationError() {
  const err = new Error(
    'Khaya (Correction_Engine) API key is not configured; hybrid correction is unavailable'
  );
  err.type = 'ConfigurationError';
  err.code = 'MISSING_API_KEY';
  err.statusCode = 500;
  return err;
}

/**
 * Safely reads the Deepgram model name from the raw response metadata.
 *
 * @param {Object} deepgramResponse
 * @returns {string|undefined}
 */
function extractModelName(deepgramResponse) {
  const metadata =
    deepgramResponse &&
    deepgramResponse.result &&
    deepgramResponse.result.metadata;
  if (!metadata) {
    return undefined;
  }
  if (typeof metadata.model_name === 'string') {
    return metadata.model_name;
  }
  // Deepgram nests per-model details under model_info keyed by model uuid.
  const modelInfo = metadata.model_info;
  if (modelInfo && typeof modelInfo === 'object') {
    const first = Object.values(modelInfo)[0];
    if (first && typeof first.name === 'string') {
      return first.name;
    }
  }
  return undefined;
}

/**
 * Joins unified segment text into a single flattened transcript string.
 *
 * @param {UnifiedSegment[]} segments
 * @returns {string}
 */
function flattenSegments(segments) {
  return segments
    .map((seg) => (seg.text || '').trim())
    .filter((text) => text.length > 0)
    .join(' ');
}

/**
 * Assembles the final HybridResult, echoing config and correction stats.
 *
 * @param {Object} params
 * @param {string} params.transcript
 * @param {UnifiedSegment[]} params.segments
 * @param {Word[]} params.words
 * @param {number} params.duration
 * @param {string|undefined} params.modelName
 * @param {HybridConfig} params.config
 * @param {CorrectionStats} params.correctionStats
 * @returns {HybridResult}
 */
function buildResult({
  transcript,
  segments,
  words,
  duration,
  modelName,
  config,
  correctionStats,
}) {
  const metadata = {
    pipeline: 'hybrid-confidence',
    correctionStats,
    config: {
      threshold: config.threshold,
      gapTolerance: config.gapTolerance,
      padding: config.padding,
      maxCallsPerModel: config.maxCallsPerModel,
    },
  };
  if (modelName !== undefined) {
    metadata.model_name = modelName;
  }

  return {
    transcript,
    segments,
    words,
    duration,
    metadata,
  };
}

/**
 * Reassembles the Unified_Transcript from preserved high-confidence words and a
 * flat list of per-batch corrections.
 *
 * Each correction spans a batch's full word-index range (start → end of that
 * batch's low-confidence region). Any word inside a correction's range —
 * including high-confidence words Deepgram likely misheard from the Ghanaian
 * audio — is replaced by the single corrected block. Words outside every
 * correction range are preserved verbatim at their positions. Output is ordered
 * by ascending start.
 *
 * @param {Word[]} words
 * @param {Array<{start: number, end: number, wordIndexRange: [number, number], text: string, language: string}>} corrections
 * @returns {UnifiedSegment[]}
 */
function reassembleCorrections(words, corrections) {
  const covered = new Set();
  for (const c of corrections) {
    const [first, last] = c.wordIndexRange;
    for (let i = first; i <= last; i++) {
      covered.add(i);
    }
  }

  const segments = [];

  // Emit preserved words that fall outside every correction range.
  for (let i = 0; i < words.length; i++) {
    if (!covered.has(i)) {
      const word = words[i];
      segments.push({
        text: word.word,
        start: word.start,
        end: word.end,
        corrected: false,
        confidence: word.confidence,
      });
    }
  }

  // Emit each corrected block at its batch's time span.
  for (const c of corrections) {
    if (c.text) {
      segments.push({
        text: c.text,
        start: c.start,
        end: c.end,
        corrected: true,
        language: c.language,
      });
    }
  }

  segments.sort((a, b) => a.start - b.start);
  return segments;
}

/**
 * Runs the full hybrid pipeline. Throws ConfigurationError when Khaya is not
 * configured. Falls back to the untouched primary transcript when no low-
 * confidence words exist or when correction is skipped/failed.
 *
 * @param {{ buffer: Buffer, mimetype: string }} input
 * @param {HybridDeps} deps
 * @param {HybridConfig} config
 * @returns {Promise<HybridResult>}
 */
async function runHybridPipeline(input, deps, config) {
  // 1. Config check: Khaya must be configured to run any correction.
  if (!deps.khayaConfigured()) {
    throw configurationError();
  }

  // 2. Primary transcription. extractWords throws TranscriptionError when the
  //    response carries no word-level results; that propagates to the caller.
  const dgResponse = await deps.transcribePrimary(input);
  const { words, duration, transcript: primaryTranscript } =
    extractWords(dgResponse);
  const modelName = extractModelName(dgResponse);

  // 3. Classify each word against the confidence threshold.
  const classified = classifyWords(words, config.threshold);

  // 4. No low-confidence words: passthrough the primary transcript unchanged.
  const hasLowConfidence = classified.some((c) => c.isLow);
  if (!hasLowConfidence) {
    const segments = reassemble(words, []);
    return buildResult({
      transcript: primaryTranscript,
      segments,
      words,
      duration,
      modelName,
      config,
      correctionStats: {
        segmentsDetected: 0,
        corrected: false,
        language: null,
        correctionSkipped: false,
      },
    });
  }

  // 5. Group low-confidence words into segments and bundle with padding.
  const segments = groupLowConfidence(classified, config.gapTolerance);
  const bundles = buildBundles(segments, duration, config.padding);

  const skipCorrection = (correctionSkipped) => {
    const passthroughSegments = reassemble(words, []);
    return buildResult({
      transcript: primaryTranscript,
      segments: passthroughSegments,
      words,
      duration,
      modelName,
      config,
      correctionStats: {
        segmentsDetected: bundles.length,
        corrected: false,
        language: null,
        correctionSkipped,
      },
    });
  };

  // 6. Batch the bundles into at most `maxCallsPerModel` contiguous batches.
  //    Each batch is corrected with one request per candidate language, so the
  //    number of batches caps the Correction_Engine calls per model. Batching
  //    by time also keeps corrections positioned near where they occur, so any
  //    proportional-split drift stays local to a short window instead of
  //    spanning the whole file.
  const batches = batchBundles(bundles, config.maxCallsPerModel);

  const corrections = [];
  const languagesUsed = new Set();
  let anyBatchSucceeded = false;

  for (const batch of batches) {
    let slice;
    try {
      slice = await deps.sliceAndConcatAudio(input.buffer, batch.ranges);
    } catch (_sliceErr) {
      // Could not build this batch's clip: leave its segments uncorrected.
      continue;
    }

    const raceResults = await raceLanguages(
      slice.buffer,
      slice.mimetype,
      deps.khayaTranscribe
    );

    if (raceResults.some((r) => r.ok)) {
      anyBatchSucceeded = true;
    }

    const winner = selectWinner(raceResults);
    if (!winner) {
      // All languages failed or returned empty for this batch: leave it uncorrected.
      continue;
    }

    languagesUsed.add(winner.language);

    // Insert the whole batch transcript as ONE corrected block spanning the
    // batch's time window (first segment start → last segment end). This keeps
    // the corrected text coherent and localized to its window instead of
    // scattering fragments word-by-word (which scrambles ordering because Khaya
    // returns no per-word timestamps).
    const batchStart = batch.bundles[0].originalStart;
    const batchEnd = batch.bundles[batch.bundles.length - 1].originalEnd;
    const firstIndex = batch.bundles[0].wordIndexRange[0];
    const lastIndex = batch.bundles[batch.bundles.length - 1].wordIndexRange[1];
    corrections.push({
      start: batchStart,
      end: batchEnd,
      wordIndexRange: [firstIndex, lastIndex],
      text: winner.transcript,
      language: winner.language,
    });
  }

  // 7. Khaya unavailable / errored for every batch: skip correction.
  if (!anyBatchSucceeded || corrections.length === 0) {
    return skipCorrection(true);
  }

  // 8. Reassemble: preserved high-confidence words plus each corrected block at
  //    its batch's time span.
  const unifiedSegments = reassembleCorrections(words, corrections);
  const winningLanguage =
    languagesUsed.size === 1 ? [...languagesUsed][0] : [...languagesUsed].join(',');

  return buildResult({
    transcript: flattenSegments(unifiedSegments),
    segments: unifiedSegments,
    words,
    duration,
    modelName,
    config,
    correctionStats: {
      segmentsDetected: bundles.length,
      corrected: true,
      language: winningLanguage,
      correctionSkipped: false,
    },
  });
}

module.exports = { runHybridPipeline };
