/**
 * Correction Evidence Routes
 *
 * Express router exposing the persisted Correction_Evidence for a record's
 * transcript version. This is a SIBLING route to `GET transcript` so the
 * Baseline transcript response stays byte-identical for clients that do not
 * read evidence, and evidence is fetched lazily on the editing stage.
 *
 * Feature: transcript-evidence-navigation (task 6.1, Req 8.1, 8.3, 8.5, 8.6,
 * 8.7, 8.9, 10.4).
 *
 * The Gateway only READS evidence: it resolves the record's current transcript
 * (id + version), proxies to the Python Postprocess_Service evidence endpoint
 * (the sole writer/owner of the correction table), and maps snake_case ->
 * camelCase at the boundary. It never writes `correction_evidence` and never
 * computes evidence of its own (Req 8.5, 8.6).
 *
 * @module routes/evidence
 */

"use strict";

const express = require("express");
const requirePermission = require("../middleware/require-permission");

/**
 * Postprocessing Service base URL.
 */
const POSTPROCESS_URL = process.env.POSTPROCESS_URL || "http://localhost:8082";

/**
 * Service token for authenticating to the Postprocessing Service.
 * Its /v1/* endpoints are guarded by verify_service_token (Bearer).
 */
const POSTPROCESS_TOKEN = process.env.POSTPROCESS_TOKEN || "";

/**
 * Maps a single snake_case CorrectionEntry from the Python service to the
 * camelCase gateway/frontend contract.
 *
 * Fields absent from the Python payload (omitted provenance, entity
 * classification, mappingConfidence) are omitted here too rather than emitted
 * as empty placeholders (Req 10.4) — the additive/omit-when-unsupplied contract.
 *
 * @param {object} e - Raw snake_case CorrectionEntry
 * @returns {object} camelCase CorrectionEntry
 */
function mapCorrectionEntry(e) {
  const entry = {
    sourceSpanId: e.source_span_id,
    sourceWordIds: Array.isArray(e.source_word_ids) ? e.source_word_ids : [],
    original: e.original ?? "",
    corrected: e.corrected ?? "",
    correctionStage: e.correction_stage,
    correctionOutcome: e.correction_outcome,
    confidence: e.confidence,
  };

  // Immutable ASR timing — present when the covered words carried timing.
  if (e.source_start !== undefined && e.source_start !== null) entry.sourceStart = e.source_start;
  if (e.source_end !== undefined && e.source_end !== null) entry.sourceEnd = e.source_end;

  // Acceptance threshold the confidence was compared against.
  if (e.threshold !== undefined && e.threshold !== null) entry.threshold = e.threshold;

  // Entity classification — omitted when the correction is not an entity.
  if (e.entity_kind !== undefined && e.entity_kind !== null) entry.entityKind = e.entity_kind;
  if (e.entity_type !== undefined && e.entity_type !== null) entry.entityType = e.entity_type;

  // Provenance — each omitted when the producing stage supplied no value.
  if (e.correlation_id !== undefined && e.correlation_id !== null) entry.correlationId = e.correlation_id;
  if (e.dataset_version !== undefined && e.dataset_version !== null) entry.datasetVersion = e.dataset_version;
  if (e.model_id !== undefined && e.model_id !== null) entry.modelId = e.model_id;

  // Mapping confidence — present only when the builder knows alignment was lost.
  if (e.mapping_confidence !== undefined && e.mapping_confidence !== null) {
    entry.mappingConfidence = e.mapping_confidence;
  }

  return entry;
}

/**
 * Maps the raw snake_case CorrectionEvidence from the Python service to the
 * camelCase gateway response.
 *
 * @param {object} raw - Raw snake_case CorrectionEvidence
 * @returns {object} camelCase CorrectionEvidence
 */
function mapCorrectionEvidence(raw) {
  return {
    transcriptId: raw.transcript_id ?? 0,
    version: raw.version ?? 0,
    entries: (raw.entries || []).filter(Boolean).map(mapCorrectionEntry),
  };
}

/**
 * Creates the Correction Evidence router.
 * @param {Function} authMiddleware - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
function evidenceRoutes(authMiddleware, db) {
  const router = express.Router();

  /**
   * GET /api/sittings/:sittingId/records/:recordId/transcript/evidence
   *
   * Returns the persisted Correction_Evidence for the record's latest
   * transcript version (snake_case from Python, mapped to camelCase here).
   *
   * Resolves the record's current transcript the same way `GET transcript`
   * does (latest version row for the record), then reads that transcript's
   * evidence from Python keyed by transcript_id + version.
   *
   * Retains the SAME RBAC as the transcript GET (`view_records`) — no new
   * permission is introduced (Req 8.7, 8.9).
   *
   * A record with no transcript, or a transcript version carrying no evidence,
   * returns an empty `entries` list which the frontend treats as Baseline
   * "no evidence" (Req 10.4, 10.5).
   */
  router.get(
    "/api/sittings/:sittingId/records/:recordId/transcript/evidence",
    authMiddleware,
    requirePermission("view_records"),
    async (req, res, next) => {
      try {
        const { recordId } = req.params;

        // Resolve the record's current transcript (id + version) the same way
        // the transcript GET does. The Gateway is keyed by sitting/record, but
        // Python is keyed by transcript_id — this bridges the two.
        const result = await db.query(
          `SELECT id, version
           FROM transcript
           WHERE record_id = $1
           ORDER BY version DESC
           LIMIT 1`,
          [recordId]
        );

        // No transcript for the record — no evidence (Baseline, Req 10.5).
        if (result.rows.length === 0) {
          return res.json({ transcriptId: 0, version: 0, entries: [] });
        }

        const { id: transcriptId, version } = result.rows[0];

        const upstream = await fetch(
          `${POSTPROCESS_URL}/v1/transcripts/${encodeURIComponent(transcriptId)}/correction-evidence?version=${encodeURIComponent(version)}`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${POSTPROCESS_TOKEN}`,
            },
          }
        );

        // A 404 from Python means "no evidence" for this version — the Gateway
        // treats it as an empty list rather than an error (Req 10.4, 10.5).
        if (upstream.status === 404) {
          return res.json({ transcriptId, version, entries: [] });
        }

        if (!upstream.ok) {
          const errBody = await upstream.text();
          console.error("Postprocessing /correction-evidence error:", upstream.status, errBody);
          return res.status(upstream.status >= 500 ? 502 : upstream.status).json({
            error: {
              type: "ServerError",
              code: "EVIDENCE_FETCH_FAILED",
              message: "Correction evidence service returned an error",
            },
          });
        }

        const raw = await upstream.json();
        res.json(mapCorrectionEvidence(raw));
      } catch (err) {
        // The Postprocessing Service is unreachable — report as an upstream
        // dependency failure so the cause is actionable.
        if (err.cause?.code === "ECONNREFUSED" || err.cause?.code === "ENOTFOUND") {
          return res.status(503).json({
            error: {
              type: "ServiceUnavailable",
              code: "EVIDENCE_UNAVAILABLE",
              message:
                "Correction evidence service is unavailable. The Postprocessing Service is not reachable.",
            },
          });
        }
        next(err);
      }
    }
  );

  return router;
}

module.exports = evidenceRoutes;
module.exports.mapCorrectionEvidence = mapCorrectionEvidence;
module.exports.mapCorrectionEntry = mapCorrectionEntry;
