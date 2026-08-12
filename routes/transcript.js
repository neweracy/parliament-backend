/**
 * Transcript Routes
 *
 * Express router for retrieving and editing persisted transcripts.
 * Supports versioning — each PATCH creates a new version row.
 *
 * @module routes/transcript
 */

"use strict";

const express = require("express");
const requirePermission = require("../middleware/require-permission");
const { broadcast } = require("../lib/ws-server");

/**
 * Maximum allowed transcript text size in bytes (1 MB).
 */
const MAX_TEXT_BYTES = 1_048_576;

/**
 * Postprocessing Service base URL (for RAG re-ingestion trigger).
 */
const POSTPROCESS_URL = process.env.POSTPROCESS_URL || "http://localhost:8082";

/**
 * Service token for authenticating to the Postprocessing Service.
 */
const POSTPROCESS_TOKEN = process.env.POSTPROCESS_TOKEN || "";

/**
 * Creates the Transcript router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function transcriptRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * GET /api/sittings/:sittingId/records/:recordId/transcript
   *
   * Returns the latest version of the transcript for the specified record.
   * Response includes correctedText, rawText, entities, wordTimings, and version.
   * Returns 404 if no transcript exists for the record.
   */
  router.get(
    "/api/sittings/:sittingId/records/:recordId/transcript",
    requireSession,
    requirePermission("view_records"),
    async (req, res) => {
      try {
        const { recordId } = req.params;

        const result = await db.query(
          `SELECT corrected_text, raw_text, entities, word_timings, version
           FROM transcript
           WHERE record_id = $1
           ORDER BY version DESC
           LIMIT 1`,
          [recordId]
        );

        if (result.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "TRANSCRIPT_NOT_FOUND",
              message: "No transcript exists for this record",
            },
          });
        }

        const row = result.rows[0];

        res.json({
          correctedText: row.corrected_text,
          rawText: row.raw_text,
          entities: row.entities,
          wordTimings: row.word_timings,
          version: row.version,
        });
      } catch (err) {
        console.error("GET /transcript error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to retrieve transcript",
          },
        });
      }
    }
  );

  /**
   * PATCH /api/sittings/:sittingId/records/:recordId/transcript
   *
   * Creates a new transcript version with the provided corrected text.
   * Validates that text is non-empty and does not exceed 1 MB.
   * Copies metadata fields (raw_text, entities, word_timings, correlation_id,
   * provider, duration_s) from the previous version.
   *
   * Returns 200 with the new version number on success.
   * Returns 404 if no transcript exists to update.
   * Returns 422 for validation failures (empty text or too large).
   */
  router.patch(
    "/api/sittings/:sittingId/records/:recordId/transcript",
    requireSession,
    requirePermission("edit_record"),
    express.json({ limit: "2mb" }),
    async (req, res) => {
      try {
        const { recordId } = req.params;
        const { text } = req.body;

        // Validate: text must be a non-empty string after trimming
        if (!text || typeof text !== "string" || text.trim().length === 0) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "EMPTY_TRANSCRIPT",
              message: "Transcript text must not be empty",
            },
          });
        }

        // Validate: text must not exceed 1 MB
        if (Buffer.byteLength(text, "utf8") > MAX_TEXT_BYTES) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "TRANSCRIPT_TOO_LARGE",
              message: "Transcript text must not exceed 1 MB",
            },
          });
        }

        // Get the current latest version for this record
        const latestResult = await db.query(
          `SELECT version, raw_text, entities, word_timings, correlation_id, provider, duration_s
           FROM transcript
           WHERE record_id = $1
           ORDER BY version DESC
           LIMIT 1`,
          [recordId]
        );

        if (latestResult.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "TRANSCRIPT_NOT_FOUND",
              message: "No transcript exists for this record",
            },
          });
        }

        const previous = latestResult.rows[0];
        const newVersion = previous.version + 1;

        // Insert new version row, copying metadata from previous version
        const insertResult = await db.query(
          `INSERT INTO transcript (record_id, version, corrected_text, raw_text, entities, word_timings, correlation_id, provider, duration_s)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           RETURNING id`,
          [
            recordId,
            newVersion,
            text,
            previous.raw_text,
            JSON.stringify(previous.entities || []),
            JSON.stringify(previous.word_timings || []),
            previous.correlation_id || null,
            previous.provider || null,
            previous.duration_s || null,
          ]
        );

        // Fire-and-forget: trigger RAG re-ingestion for the updated transcript
        const transcriptId = insertResult.rows[0].id;
        triggerIngestion(transcriptId);

        res.status(200).json({ version: newVersion });

        // Broadcast live update
        broadcast("transcript:updated", {
          transcriptId,
          recordId: Number(recordId),
          version: newVersion,
        });
      } catch (err) {
        console.error("PATCH /transcript error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to update transcript",
          },
        });
      }
    }
  );

  return router;
};

/**
 * Fire-and-forget: sends a POST to the Postprocessing Service to trigger
 * RAG re-ingestion for the given transcript. Does not block or throw on failure.
 *
 * @param {number|string} transcriptId - The transcript ID to re-ingest
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
    console.error("RAG re-ingestion trigger failed (non-blocking):", err.message);
  });
}
