/**
 * Transcription Routes
 *
 * Express router for initiating and tracking transcription jobs.
 * Jobs run asynchronously — POST /transcribe returns 202 immediately
 * and the caller polls GET /transcription-status for progress.
 *
 * @module routes/transcription
 */

"use strict";

const express = require("express");
const crypto = require("crypto");

const { transcribeStoredAudio } = require("../lib/transcription-pipeline");
const requirePermission = require("../middleware/require-permission");
const { broadcast } = require("../lib/ws-server");

/**
 * Postprocessing Service base URL (for RAG ingestion trigger).
 */
const POSTPROCESS_URL = process.env.POSTPROCESS_URL || "http://localhost:8082";

/**
 * Service token for authenticating to the Postprocessing Service.
 */
const POSTPROCESS_TOKEN = process.env.POSTPROCESS_TOKEN || "";

/**
 * In-memory job state store.
 * Maps jobId → { status, progress, recordId, sittingId, error }
 *
 * In a production multi-instance deployment this would move to Redis or
 * the database, but for single-process operation this is sufficient.
 */
const jobs = new Map();

/**
 * How long a finished job (completed or failed) stays in memory so a polling
 * client can read its terminal state before it is discarded.
 */
const JOB_RETENTION_MS = 60000;

/**
 * Creates the Transcription router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function transcriptionRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * POST /api/sittings/:sittingId/records/:recordId/transcribe
   *
   * Initiates transcription of the record's uploaded audio.
   * Sets record status to 'Transcribing', generates a jobId, and returns 202.
   * The actual transcription work is kicked off asynchronously.
   */
  router.post(
    "/api/sittings/:sittingId/records/:recordId/transcribe",
    requireSession,
    requirePermission("upload_audio"),
    async (req, res) => {
      try {
        const { sittingId, recordId } = req.params;

        // Verify record exists and belongs to the specified sitting
        const recordResult = await db.query(
          "SELECT id, audio_path, status FROM hansard_record WHERE id = $1 AND sitting_id = $2",
          [recordId, sittingId]
        );

        if (recordResult.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "RECORD_NOT_FOUND",
              message:
                "Record with id " + recordId + " not found in sitting " + sittingId,
            },
          });
        }

        const record = recordResult.rows[0];

        // Verify the record has an audio file associated
        if (!record.audio_path) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "NO_AUDIO",
              message:
                "Record does not have an audio file. Upload audio before transcribing.",
            },
          });
        }

        // Generate a unique job identifier
        const jobId = crypto.randomUUID();

        // Set record status to Transcribing and progress to 0
        await db.query(
          "UPDATE hansard_record SET status = 'Transcribing', progress = 0, error = NULL, updated_at = now() WHERE id = $1",
          [recordId]
        );

        // Store job state in memory
        jobs.set(jobId, {
          status: "queued",
          progress: 0,
          recordId,
          sittingId,
          error: null,
        });

        // Kick off async transcription (do not await — return 202 immediately)
        runTranscription(jobId, recordId, sittingId, record.audio_path, db).catch(
          (err) => {
            console.error("Unhandled transcription error for job " + jobId + ":", err);
          }
        );

        res.status(202).json({ jobId });
      } catch (err) {
        console.error("POST /transcribe error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to initiate transcription",
          },
        });
      }
    }
  );

  /**
   * GET /api/sittings/:sittingId/records/:recordId/transcription-status
   *
   * Returns the current transcription progress for the record.
   * Checks in-memory job state first, then falls back to DB record status.
   */
  router.get(
    "/api/sittings/:sittingId/records/:recordId/transcription-status",
    requireSession,
    requirePermission("view_records"),
    async (req, res) => {
      try {
        const { sittingId, recordId } = req.params;

        // Check in-memory jobs for an active job matching this record
        let activeJob = null;
        for (const [, job] of jobs) {
          if (job.recordId === recordId && job.sittingId === sittingId) {
            activeJob = job;
            break;
          }
        }

        if (activeJob) {
          return res.json({
            status: activeJob.status,
            progress: activeJob.progress,
            ...(activeJob.error ? { error: activeJob.error } : {}),
          });
        }

        // Fallback: check DB record status
        const recordResult = await db.query(
          "SELECT status, progress, error FROM hansard_record WHERE id = $1 AND sitting_id = $2",
          [recordId, sittingId]
        );

        if (recordResult.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "RECORD_NOT_FOUND",
              message:
                "Record with id " + recordId + " not found in sitting " + sittingId,
            },
          });
        }

        const record = recordResult.rows[0];

        // Map DB record status onto transcription job status. An error field
        // takes precedence so a failed-then-retriable record reports 'failed'.
        // Every post-transcription status counts as completed — omitting
        // 'Under Review'/'Certified' would report a finished record as queued.
        const COMPLETED_STATUSES = [
          "Draft",
          "Editing",
          "Under Review",
          "Certified",
          "Published",
        ];

        let status;
        if (record.error) {
          status = "failed";
        } else if (record.status === "Transcribing") {
          status = "processing";
        } else if (COMPLETED_STATUSES.includes(record.status)) {
          status = "completed";
        } else {
          status = "queued";
        }

        res.json({
          status,
          progress: record.progress || 0,
          ...(record.error ? { error: record.error } : {}),
        });
      } catch (err) {
        console.error("GET /transcription-status error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to retrieve transcription status",
          },
        });
      }
    }
  );

  return router;
};

// ============================================================================
// ASYNC TRANSCRIPTION RUNNER
// ============================================================================

/**
 * Runs the transcription pipeline asynchronously.
 *
 * Reads the record's stored audio, sends it to Deepgram, runs post-processing
 * (entities and corrections), then persists the transcript and moves the
 * record to Draft.
 *
 * On completion: inserts transcript row, updates record status to Draft.
 * On failure: sets error on record, returns status to Draft (retriable).
 *
 * @param {string} jobId - Unique job identifier
 * @param {string} recordId - Hansard record ID
 * @param {string} sittingId - Parent sitting ID
 * @param {string} audioPath - Path to the audio file
 * @param {Object} db - Database client
 */
async function runTranscription(jobId, recordId, sittingId, audioPath, db) {
  const job = jobs.get(jobId);
  if (!job) return;

  /**
   * Mirrors progress into both the in-memory job and the record row so the
   * frontend sees movement whether it reads the job or falls back to the DB.
   */
  async function reportProgress(percent) {
    const current = jobs.get(jobId);
    if (current) {
      current.status = "processing";
      current.progress = percent;
    }
    try {
      await db.query(
        "UPDATE hansard_record SET progress = $1, updated_at = now() WHERE id = $2",
        [percent, recordId]
      );
    } catch (err) {
      // Progress reporting is best-effort — never fail the job over it.
      console.error("Progress update failed for job " + jobId + ":", err.message);
    }
  }

  try {
    await reportProgress(10);

    const result = await transcribeStoredAudio(audioPath, {
      correlationId: jobId,
      onProgress: (percent) => {
        // Fire-and-forget: the pipeline is synchronous from our perspective and
        // we don't want progress writes to serialise the ASR call.
        reportProgress(percent).catch(() => {});
      },
    });

    await completeTranscription(jobId, recordId, result, db);
  } catch (err) {
    console.error("Transcription failed for job " + jobId + ":", err);
    await failTranscription(
      jobId,
      recordId,
      err.message || "Transcription failed",
      db
    );
  }
}

/**
 * Completes a transcription job: stores transcript in DB and updates record.
 * Exported so the existing ASR pipeline can call it when results are ready.
 *
 * @param {string} jobId - Job identifier
 * @param {string} recordId - Hansard record ID
 * @param {Object} result - Transcription result
 * @param {string} result.rawText - Raw ASR output
 * @param {string} result.correctedText - Post-processed text
 * @param {Array} result.entities - Recognized entities
 * @param {Array} result.wordTimings - Word-level timing data
 * @param {string} [result.correlationId] - Correlation ID for tracing
 * @param {string} [result.provider] - ASR provider name
 * @param {number|null} [result.durationS] - Audio duration in seconds
 * @param {Object} db - Database client
 */
async function completeTranscription(jobId, recordId, result, db) {
  const job = jobs.get(jobId);

  // Determine the next version number for this record
  const versionResult = await db.query(
    "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM transcript WHERE record_id = $1",
    [recordId]
  );
  const nextVersion = versionResult.rows[0].next_version;

  // Insert transcript row
  const insertResult = await db.query(
    `INSERT INTO transcript (record_id, version, correlation_id, provider, duration_s, raw_text, corrected_text, entities, word_timings)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
     RETURNING id`,
    [
      recordId,
      nextVersion,
      result.correlationId || null,
      result.provider || null,
      result.durationS || null,
      result.rawText,
      result.correctedText,
      JSON.stringify(result.entities || []),
      JSON.stringify(result.wordTimings || []),
    ]
  );

  // Update record status to Draft with progress 100
  await db.query(
    "UPDATE hansard_record SET status = 'Draft', progress = 100, error = NULL, updated_at = now() WHERE id = $1",
    [recordId]
  );

  // Broadcast record status change
  broadcast("record:updated", { id: Number(recordId), status: "Draft", progress: 100 });

  // Fire-and-forget: trigger RAG ingestion on the Postprocessing Service
  const transcriptId = insertResult.rows[0].id;
  triggerIngestion(transcriptId);

  // Broadcast live update — transcription complete
  broadcast("transcript:created", {
    transcriptId,
    recordId: Number(recordId),
    version: nextVersion,
  });

  // Update job state
  if (job) {
    job.status = "completed";
    job.progress = 100;
    job.error = null;
  }

  // Retire the job shortly after the client observes completion. Without this
  // the entry lives forever and the status endpoint keeps serving stale
  // in-memory state instead of the record's real status.
  scheduleJobCleanup(jobId);
}

/**
 * Marks a transcription job as failed: sets error on record, returns to retriable state.
 * Exported so external callers can report failures.
 *
 * @param {string} jobId - Job identifier
 * @param {string} recordId - Hansard record ID
 * @param {string} errorMessage - Description of what went wrong
 * @param {Object} db - Database client
 */
async function failTranscription(jobId, recordId, errorMessage, db) {
  const job = jobs.get(jobId);

  // Update record: set error, return status to Draft (retriable)
  await db.query(
    "UPDATE hansard_record SET status = 'Draft', error = $1, progress = 0, updated_at = now() WHERE id = $2",
    [errorMessage, recordId]
  );

  // Update job state
  if (job) {
    job.status = "failed";
    job.progress = 0;
    job.error = errorMessage;
  }

  scheduleJobCleanup(jobId);
}

/**
 * Terminal job states are kept briefly so a polling client can observe them,
 * then removed. Retaining them indefinitely would shadow the record's real
 * status on every later status request (and leak memory).
 *
 * @param {string} jobId - Job identifier to retire
 */
function scheduleJobCleanup(jobId) {
  const timer = setTimeout(() => jobs.delete(jobId), JOB_RETENTION_MS);
  // Don't hold the event loop open in tests or on shutdown.
  if (typeof timer.unref === "function") timer.unref();
}

// Export helpers so the existing transcription pipeline can integrate
module.exports.completeTranscription = completeTranscription;
module.exports.failTranscription = failTranscription;
module.exports.jobs = jobs;

/**
 * Fire-and-forget: sends a POST to the Postprocessing Service to trigger
 * RAG ingestion for the given transcript. Does not block or throw on failure.
 *
 * @param {number|string} transcriptId - The transcript ID to ingest
 */
function triggerIngestion(transcriptId) {
  fetch(`${POSTPROCESS_URL}/rag/ingest`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${POSTPROCESS_TOKEN}`,
    },
    body: JSON.stringify({ transcript_id: transcriptId }),
  }).catch((err) => {
    console.error("RAG ingestion trigger failed (non-blocking):", err.message);
  });
}

module.exports.triggerIngestion = triggerIngestion;
