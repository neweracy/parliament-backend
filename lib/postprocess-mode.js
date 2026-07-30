/**
 * Degraded_Mode response construction and success-path merge logic.
 *
 * Used by the Gateway when `POSTPROCESS_MODE=python`:
 * - `degradedResponse` builds a 200-OK body from raw ASR data when the
 *   Postprocessing_Service is unavailable or mode is `off`.
 * - `mergeSuccess` field-wise merges a Correction_Response with Gateway-owned
 *   metadata fields.
 * - `logDegraded` emits the error record and degraded metric on every
 *   degradation event.
 *
 * @module lib/postprocess-mode
 */

"use strict";

// ---------------------------------------------------------------------------
// degradedResponse
// ---------------------------------------------------------------------------

/**
 * Build a 200-OK response body when post-processing is skipped or disabled.
 *
 * @param {string} rawTranscript - Raw ASR transcript text
 * @param {Array<object>} rawWords - Raw Word list from ASR (shallow-copied)
 * @param {object} meta - Gateway-owned metadata (model_uuid, request_id, model_name)
 * @param {number|undefined} duration - Top-level duration from Deepgram (omitted when undefined)
 * @param {string} status - 'skipped' or 'disabled'
 * @returns {object} Client-facing response body
 */
function degradedResponse(rawTranscript, rawWords, meta, duration, status) {
  const body = {
    transcript: rawTranscript,
    words: rawWords.map(w => ({ ...w })),
    entities: [],
    metadata: { ...meta, _version: "v6-python", postprocessing_status: status },
    raw: { transcript: rawTranscript, words: rawWords.map(w => ({ ...w })) },
  };
  if (duration !== undefined) {
    body.duration = duration;
  }
  return body;
}

// ---------------------------------------------------------------------------
// mergeSuccess
// ---------------------------------------------------------------------------

/**
 * Field-wise merge of a successful Correction_Response with Gateway-owned fields.
 *
 * Gateway `meta` wins for `model_uuid`, `request_id`, `model_name`.
 * Service counters (`location_corrections`, `year_corrections`, `bedrock_corrections`),
 * `llm_status`, `dataset_version`, and `correlationId` pass through from the service.
 * Zero-valued counters stay omitted (not added if absent from service data).
 * `_version` is always set to 'v6-python'.
 * `raw` is always constructed from the Gateway's raw ASR data.
 * `duration` is top-level and present only when defined.
 *
 * @param {object} serviceData - Parsed Correction_Response from the service
 * @param {string} rawTranscript - Raw ASR transcript text
 * @param {Array<object>} rawWords - Raw Word list from ASR
 * @param {object} meta - Gateway-owned metadata (model_uuid, request_id, model_name)
 * @param {number|undefined} duration - Top-level duration from Deepgram
 * @returns {object} Client-facing response body
 */
function mergeSuccess(serviceData, rawTranscript, rawWords, meta, duration) {
  const merged = {
    transcript: serviceData.transcript,
    words: serviceData.words,
    entities: serviceData.entities,
  };

  // Corrections array passes through if present
  if (serviceData.corrections) {
    merged.corrections = serviceData.corrections;
  }

  // Metadata: service counters pass through, Gateway fields win
  merged.metadata = {
    ...serviceData.metadata,
    ...meta,
    _version: "v6-python",
  };

  // raw is always Gateway-owned
  merged.raw = { transcript: rawTranscript, words: rawWords };

  // duration: top-level, only when present
  if (duration !== undefined) {
    merged.duration = duration;
  }

  return merged;
}

// ---------------------------------------------------------------------------
// logDegraded
// ---------------------------------------------------------------------------

/**
 * Log one error record and emit the degraded metric when the Gateway enters
 * Degraded_Mode.
 *
 * @param {{ reason: string, elapsedMs: number }} result - Failure result from postprocess-client
 * @param {string} correlationId - Correlation identifier for tracing
 */
function logDegraded(result, correlationId) {
  console.error(JSON.stringify({
    level: "error",
    event: "postprocess.degraded",
    reason: result.reason,
    elapsedMs: result.elapsedMs,
    correlationId,
  }));
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  degradedResponse,
  mergeSuccess,
  logDegraded,
};
