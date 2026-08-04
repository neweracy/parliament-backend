/**
 * Transcription pipeline — ASR + post-processing for stored audio files.
 *
 * The Gateway's POST /api/transcription endpoint transcribes an uploaded
 * buffer and returns the result inline. Hansard records instead have audio
 * already persisted to disk, so this module provides the equivalent pipeline
 * for a file path:
 *
 *   read file → Deepgram ASR → post-processing (entities/corrections)
 *
 * Extracted into its own module so both the legacy endpoint and the Hansard
 * record workflow share one implementation rather than duplicating the logic.
 *
 * @module lib/transcription-pipeline
 */

"use strict";

const crypto = require("crypto");
const fs = require("fs/promises");
const path = require("path");

const { createClient } = require("@deepgram/sdk");
const { postprocess } = require("./postprocess-client");
const { degradedResponse, mergeSuccess, logDegraded } = require("./postprocess-mode");
const {
  probeDuration,
  transcodeForAsr,
  tempPath,
  removeQuietly,
} = require("./audio-preprocess");

/** Default ASR model. Matches the Gateway's DEFAULT_MODEL. */
const DEFAULT_MODEL = "nova-3";

/** Post-processing mode: 'js' | 'python' | 'off'. */
const POSTPROCESS_MODE = (process.env.POSTPROCESS_MODE || "python").toLowerCase();

/**
 * Largest payload we will attempt in a single Deepgram request.
 *
 * Deepgram aborts prerecorded uploads that miss its deadline with
 * `408 SLOW_UPLOAD`. The ceiling that actually matters is upload bandwidth, not
 * audio length, so this is expressed in bytes. 1.5 MB uploads in roughly 20 s
 * on a 0.6 Mbps uplink, leaving margin under the observed ~30 s deadline.
 *
 * Override with ASR_MAX_UPLOAD_BYTES on faster connections to reduce chunking
 * (which improves cross-chunk speaker attribution — see transcribeInChunks).
 */
const MAX_UPLOAD_BYTES = Number(process.env.ASR_MAX_UPLOAD_BYTES) || 1.5 * 1024 * 1024;

/**
 * Audio seconds per chunk when splitting. At 16 kHz mono 32 kbps this yields
 * roughly 700 KB per chunk, comfortably inside the upload deadline.
 */
const CHUNK_SECONDS = Number(process.env.ASR_CHUNK_SECONDS) || 180;

/**
 * Attempts per chunk before giving up. Covers transient upload stalls and
 * dropped connections, which are common on constrained uplinks.
 */
const MAX_UPLOAD_ATTEMPTS = Number(process.env.ASR_MAX_UPLOAD_ATTEMPTS) || 5;

/**
 * Maps a file extension to the MIME type Deepgram expects. Deepgram requires
 * an explicit mimetype for buffer uploads.
 */
const MIME_BY_EXTENSION = {
  ".mp3": "audio/mpeg",
  ".mpeg": "audio/mpeg",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".webm": "audio/webm",
  ".mp4": "audio/mp4",
  ".m4a": "audio/mp4",
  ".flac": "audio/flac",
};

/**
 * Resolves the MIME type for an audio file path.
 *
 * @param {string} filePath - Path to the audio file
 * @returns {string} MIME type, defaulting to audio/mpeg
 */
function mimeTypeFor(filePath) {
  return MIME_BY_EXTENSION[path.extname(filePath).toLowerCase()] || "audio/mpeg";
}

/**
 * Lazily constructs the Deepgram client so this module can be imported (and
 * unit tested) without a configured API key.
 *
 * @returns {Object} Deepgram SDK client
 * @throws {Error} When DEEPGRAM_API_KEY is not configured
 */
function getDeepgramClient() {
  const apiKey = process.env.DEEPGRAM_API_KEY;
  if (!apiKey) {
    throw new Error(
      "DEEPGRAM_API_KEY is not configured — cannot transcribe audio"
    );
  }
  return createClient(apiKey);
}

/**
 * Extracts the transcript, words, and metadata from a raw Deepgram response.
 *
 * @param {Object} response - Raw Deepgram SDK response
 * @param {string} modelName - Model used for the request
 * @returns {{rawTranscript: string, rawWords: Array, meta: Object, duration: number|undefined}}
 * @throws {Error} When Deepgram returned no alternatives (an ASR failure)
 */
function extractDeepgramResult(response, modelName) {
  const transcription = response.result;
  const alternative = transcription?.results?.channels?.[0]?.alternatives?.[0];

  if (!alternative) {
    throw new Error("No transcription results returned from Deepgram");
  }

  return {
    rawTranscript: alternative.transcript || "",
    rawWords: (alternative.words || []).map((w) => ({ ...w })),
    meta: {
      model_uuid: transcription?.metadata?.model_info
        ? Object.keys(transcription.metadata.model_info)[0]
        : undefined,
      request_id: transcription?.metadata?.request_id,
      model_name: modelName,
    },
    duration: transcription?.metadata?.duration,
  };
}

/**
 * Returns true when a Deepgram error represents a transient upload failure
 * worth retrying, rather than a permanent rejection.
 *
 * @param {Object|Error} error
 * @returns {boolean}
 */
function isRetryableUploadError(error) {
  const message = String(error?.message || error || "");
  const code = String(error?.cause?.code || error?.code || "");

  return (
    error?.status === 408 ||
    error?.status === 429 ||
    error?.status >= 500 ||
    // Deepgram's upload deadline.
    message.includes("SLOW_UPLOAD") ||
    message.includes("Request upload timeout") ||
    // undici surfaces a dropped or reset connection as a bare "fetch failed".
    message.includes("fetch failed") ||
    message.includes("ETIMEDOUT") ||
    message.includes("ECONNRESET") ||
    message.includes("ECONNABORTED") ||
    message.includes("EPIPE") ||
    message.includes("socket hang up") ||
    message.includes("network") ||
    code === "UND_ERR_SOCKET" ||
    code === "ETIMEDOUT" ||
    code === "ECONNRESET"
  );
}

/**
 * Sleeps for the given milliseconds.
 *
 * @param {number} ms
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Uploads one buffer to Deepgram, retrying transient upload failures with
 * exponential backoff.
 *
 * @param {Object} deepgram - Deepgram SDK client
 * @param {Buffer} buffer - Audio bytes
 * @param {Object} requestOptions - Deepgram request options
 * @returns {Promise<Object>} Raw Deepgram response
 * @throws {Error} When every attempt fails
 */
async function uploadWithRetry(deepgram, buffer, requestOptions) {
  let lastError;
  let attemptsMade = 0;

  for (let attempt = 1; attempt <= MAX_UPLOAD_ATTEMPTS; attempt += 1) {
    attemptsMade = attempt;

    // The SDK either throws or returns { error }, depending on where the
    // failure happened. Normalise both into `failure`.
    let response;
    let failure = null;
    try {
      response = await deepgram.listen.prerecorded.transcribeFile(
        buffer,
        requestOptions
      );
      if (response.error) {
        failure = response.error;
      }
    } catch (err) {
      failure = err;
    }

    if (!failure) {
      return response;
    }

    lastError = failure;

    const canRetry =
      isRetryableUploadError(failure) && attempt < MAX_UPLOAD_ATTEMPTS;
    if (!canRetry) {
      break;
    }

    // Exponential backoff. This connection has shown multi-second outages, so
    // start at 2s rather than 1s to give it room to recover.
    await sleep(2000 * 2 ** (attempt - 1));
  }

  const detail = lastError?.message || lastError;
  const plural = attemptsMade === 1 ? "attempt" : "attempts";
  throw new Error(
    `Deepgram upload failed after ${attemptsMade} ${plural}: ${detail}`
  );
}

/**
 * Transcribes audio by splitting it into time-bounded chunks and stitching the
 * results back together.
 *
 * Used when the transcoded file still exceeds MAX_UPLOAD_BYTES, which on a slow
 * uplink is the only way to transcribe long recordings at all.
 *
 * Known limitation: Deepgram assigns diarization speaker indices per request,
 * so "speaker 0" in one chunk is not necessarily "speaker 0" in the next.
 * Cross-chunk speaker identity is therefore not reliable. Word timings are
 * corrected by offsetting each chunk by its start time, so timing stays
 * accurate across the whole recording.
 *
 * @param {Object} deepgram - Deepgram SDK client
 * @param {string} sourcePath - Path to the original audio
 * @param {number} totalDurationSec - Full audio duration
 * @param {Object} requestOptions - Deepgram request options
 * @param {(percent: number) => void} onProgress
 * @returns {Promise<{rawTranscript: string, rawWords: Array, meta: Object, duration: number, chunkCount: number}>}
 */
async function transcribeInChunks(
  deepgram,
  sourcePath,
  totalDurationSec,
  requestOptions,
  onProgress
) {
  const chunkCount = Math.ceil(totalDurationSec / CHUNK_SECONDS);
  const transcriptParts = [];
  const allWords = [];
  let lastMeta = {};

  for (let index = 0; index < chunkCount; index += 1) {
    const startSec = index * CHUNK_SECONDS;
    const durationSec = Math.min(CHUNK_SECONDS, totalDurationSec - startSec);
    const chunkPath = tempPath(`-chunk${index}.mp3`);

    try {
      await transcodeForAsr(sourcePath, chunkPath, { startSec, durationSec });
      const chunkBuffer = await fs.readFile(chunkPath);

      const response = await uploadWithRetry(deepgram, chunkBuffer, requestOptions);
      const chunk = extractDeepgramResult(response, requestOptions.model);

      if (chunk.rawTranscript.trim()) {
        transcriptParts.push(chunk.rawTranscript.trim());
      }

      // Shift timings from chunk-relative to recording-absolute.
      for (const word of chunk.rawWords) {
        allWords.push({
          ...word,
          start: (word.start ?? 0) + startSec,
          end: (word.end ?? 0) + startSec,
        });
      }

      lastMeta = chunk.meta;
    } finally {
      await removeQuietly(chunkPath);
    }

    // Chunk upload spans 20%..60% of overall progress.
    onProgress(20 + Math.round(((index + 1) / chunkCount) * 40));
  }

  return {
    rawTranscript: transcriptParts.join(" "),
    rawWords: allWords,
    meta: lastMeta,
    duration: totalDurationSec,
    chunkCount,
  };
}

/**
 * Transcribes an audio file from disk and runs post-processing.
 *
 * Audio is transcoded to 16 kHz mono before upload, and split into chunks when
 * it is still too large to upload within Deepgram's deadline.
 *
 * @param {string} audioPath - Absolute or relative path to the stored audio
 * @param {Object} [options]
 * @param {string} [options.model] - Deepgram model override
 * @param {string} [options.correlationId] - Correlation ID for tracing
 * @param {(percent: number) => void} [options.onProgress] - Progress callback
 * @returns {Promise<Object>} Normalised result:
 *   { rawText, correctedText, entities, wordTimings, provider, durationS, correlationId }
 */
async function transcribeStoredAudio(audioPath, options = {}) {
  const model = options.model || DEFAULT_MODEL;
  const correlationId = options.correlationId || crypto.randomUUID();
  const onProgress = typeof options.onProgress === "function"
    ? options.onProgress
    : () => {};

  // 1. Verify the source file is readable and non-empty before spending time
  // on transcoding.
  let sourceStat;
  try {
    sourceStat = await fs.stat(audioPath);
  } catch (err) {
    throw new Error(`Could not read audio file: ${err.message}`);
  }

  if (sourceStat.size === 0) {
    throw new Error("Audio file is empty");
  }

  const deepgram = getDeepgramClient();
  const requestOptions = {
    model,
    mimetype: "audio/mpeg", // Always MP3 after transcoding.
    smart_format: true,
    punctuate: true,
    diarize: true,
  };

  onProgress(10);

  // 2. Transcode to 16 kHz mono. Deepgram's models run at 16 kHz, so this cuts
  // upload bytes several-fold with no accuracy cost for speech.
  const transcodedPath = tempPath("-asr.mp3");
  let rawTranscript;
  let rawWords;
  let meta;
  let duration;
  let chunkCount = 1;

  try {
    await transcodeForAsr(audioPath, transcodedPath);
    const transcodedStat = await fs.stat(transcodedPath);

    onProgress(20);

    if (transcodedStat.size <= MAX_UPLOAD_BYTES) {
      // Small enough to send in one request, which keeps diarization speaker
      // indices consistent across the whole recording.
      const buffer = await fs.readFile(transcodedPath);
      const response = await uploadWithRetry(deepgram, buffer, requestOptions);
      ({ rawTranscript, rawWords, meta, duration } = extractDeepgramResult(
        response,
        model
      ));
      onProgress(60);
    } else {
      // Too large for this connection's upload deadline — split it.
      const totalDurationSec = await probeDuration(transcodedPath);
      const chunked = await transcribeInChunks(
        deepgram,
        transcodedPath,
        totalDurationSec,
        requestOptions,
        onProgress
      );
      rawTranscript = chunked.rawTranscript;
      rawWords = chunked.rawWords;
      meta = chunked.meta;
      duration = chunked.duration;
      chunkCount = chunked.chunkCount;

      console.warn(
        `[asr] ${correlationId}: transcribed in ${chunkCount} chunks ` +
          `(${(transcodedStat.size / 1024 / 1024).toFixed(1)} MB exceeded the ` +
          `${(MAX_UPLOAD_BYTES / 1024 / 1024).toFixed(1)} MB single-request limit). ` +
          `Speaker indices are not consistent across chunk boundaries.`
      );
    }
  } finally {
    await removeQuietly(transcodedPath);
  }

  if (!rawTranscript.trim()) {
    throw new Error("Transcription produced no text — the audio may be silent");
  }

  // 3. Post-process. A post-processing failure must not lose the ASR output,
  // so every failure path falls back to the raw transcript.
  let formatted;
  if (POSTPROCESS_MODE === "off") {
    formatted = degradedResponse(rawTranscript, rawWords, meta, duration, "disabled");
  } else {
    const result = await postprocess(rawTranscript, rawWords, {}, correlationId);
    if (result.ok) {
      formatted = mergeSuccess(result.data, rawTranscript, rawWords, meta, duration);
    } else {
      logDegraded(result, correlationId);
      formatted = degradedResponse(rawTranscript, rawWords, meta, duration, "skipped");
    }
  }

  onProgress(90);

  return {
    rawText: rawTranscript,
    correctedText: formatted.transcript || rawTranscript,
    entities: formatted.entities || [],
    wordTimings: formatted.words || rawWords,
    provider: `deepgram:${model}`,
    durationS: duration ?? null,
    correlationId,
  };
}

module.exports = {
  transcribeStoredAudio,
  extractDeepgramResult,
  mimeTypeFor,
  isRetryableUploadError,
  DEFAULT_MODEL,
  MAX_UPLOAD_BYTES,
  CHUNK_SECONDS,
};
