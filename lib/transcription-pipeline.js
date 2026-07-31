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

/** Default ASR model. Matches the Gateway's DEFAULT_MODEL. */
const DEFAULT_MODEL = "nova-3";

/** Post-processing mode: 'js' | 'python' | 'off'. */
const POSTPROCESS_MODE = (process.env.POSTPROCESS_MODE || "python").toLowerCase();

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
 * Transcribes an audio file from disk and runs post-processing.
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

  // 1. Read the stored audio into memory.
  let buffer;
  try {
    buffer = await fs.readFile(audioPath);
  } catch (err) {
    throw new Error(`Could not read audio file: ${err.message}`);
  }

  if (buffer.length === 0) {
    throw new Error("Audio file is empty");
  }

  onProgress(20);

  // 2. Send to Deepgram.
  const deepgram = getDeepgramClient();
  const response = await deepgram.listen.prerecorded.transcribeFile(buffer, {
    model,
    mimetype: mimeTypeFor(audioPath),
    smart_format: true,
    punctuate: true,
    diarize: true,
  });

  if (response.error) {
    throw new Error(`Deepgram error: ${response.error.message || response.error}`);
  }

  onProgress(60);

  const { rawTranscript, rawWords, meta, duration } = extractDeepgramResult(response, model);

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
  DEFAULT_MODEL,
};
