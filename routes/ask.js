/**
 * Ask Routes
 *
 * Express router for RAG grounded Q&A over parliamentary transcripts.
 * Proxies questions to the Postprocessing Service /rag/ask endpoint.
 *
 * @module routes/ask
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
 * Its /rag/* endpoints are guarded by verify_service_token.
 */
const POSTPROCESS_TOKEN = process.env.POSTPROCESS_TOKEN || "";

/**
 * Map raw Python postprocess response to camelCase gateway response.
 * @param {object} raw - The raw response from the postprocess service
 * @returns {object} The mapped response with camelCase keys
 */
function mapAskResponse(raw) {
  return {
    answer: raw.answer || "",
    citations: (raw.citations || []).filter(Boolean).map((c) => ({
      transcriptId: c.transcript_id ?? 0,
      chunkId: c.chunk_id ?? 0,
      speaker: c.speaker || null,
      startS: c.start_s ?? null,
      endS: c.end_s ?? null,
      excerpt: c.excerpt || "",
      // Distinct identifier spaces — the frontend navigates by sitting+record,
      // never by transcriptId.
      sittingId: c.sitting_id ?? null,
      recordId: c.record_id ?? null,
    })),
    sourceChunks: (raw.source_chunks || []).filter(Boolean).map((c) => ({
      chunkId: c.chunk_id ?? 0,
      text: c.text || "",
      relevanceScore: c.relevance_score ?? 0,
      transcriptId: c.transcript_id ?? 0,
      speaker: c.speaker || null,
      startS: c.start_s ?? null,
      endS: c.end_s ?? null,
      matchedEntities: c.matched_entities || [],
      recordTitle: c.record_title || null,
      sittingTitle: c.sitting_title || null,
      date: c.date || null,
      sittingId: c.sitting_id ?? null,
      recordId: c.record_id ?? null,
    })),
    recommendations: (raw.recommendations || []).filter(Boolean).map((r) => ({
      text: r.text || "",
      reason: r.reason || "",
    })),
    relatedRecords: (raw.related_records || []).filter(Boolean).map((r) => ({
      transcriptId: r.transcript_id ?? 0,
      label: r.label || "",
      chunkId: r.chunk_id ?? 0,
      speaker: r.speaker || null,
      sittingTitle: r.sitting_title || null,
      recordTitle: r.record_title || null,
      date: r.date || null,
      startS: r.start_s ?? null,
      sittingId: r.sitting_id ?? null,
      recordId: r.record_id ?? null,
    })),
    // Sittings/records the assistant found via find_recent_activity — a
    // navigation reference with no chunk to cite, distinct from relatedRecords
    // (which always comes from a retrieved transcript chunk).
    registryReferences: (raw.registry_references || []).filter(Boolean).map((r) => ({
      kind: r.kind || "record",
      id: r.id ?? 0,
      title: r.title || "",
      sittingId: r.sitting_id ?? 0,
      recordId: r.record_id ?? null,
      createdAt: r.created_at || null,
    })),
    latencyMs: raw.latency_ms ?? raw.latencyMs ?? 0,
  };
}

/**
 * Creates the Ask router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
function askRoutes(requireSession, _db) {
  const router = express.Router();

  /**
   * POST /api/ask
   *
   * Sends a natural language question to the RAG grounded answering engine.
   * Proxies to the Postprocessing Service POST /rag/ask endpoint.
   *
   * Body: { question, entityFilter?, dateFrom?, dateTo?, speaker? }
   *
   * Returns the generated answer with citations and source chunks.
   * Enforces a 30-second timeout — returns 504 if exceeded.
   */
  router.post("/api/ask", requireSession, requirePermission("search_hansard"), express.json(), async (req, res) => {
    try {
      const { question, entityFilter, dateFrom, dateTo, speaker, conversationHistory } = req.body;

      if (!question || typeof question !== "string" || question.trim().length === 0) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_QUESTION",
            message: "A non-empty question string is required",
          },
        });
      }

      // Build request body for the Postprocessing Service
      const askBody = {
        question: question.trim(),
      };

      if (entityFilter) askBody.entity_filter = entityFilter;
      if (dateFrom) askBody.date_from = dateFrom;
      if (dateTo) askBody.date_to = dateTo;
      if (speaker) askBody.speaker = speaker;

      // Forward conversation history for multi-turn context
      if (Array.isArray(conversationHistory) && conversationHistory.length > 0) {
        // Limit to last 20 messages and validate shape
        askBody.conversation_history = conversationHistory
          .slice(-20)
          .filter((m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string")
          .map((m) => ({ role: m.role, content: m.content }));
      }

      // Proxy to Postprocessing Service with 30s timeout
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);

      try {
        const upstream = await fetch(`${POSTPROCESS_URL}/rag/ask`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${POSTPROCESS_TOKEN}`,
          },
          body: JSON.stringify(askBody),
          signal: controller.signal,
        });

        clearTimeout(timeout);

        if (!upstream.ok) {
          const errBody = await upstream.text();
          console.error("Postprocessing /rag/ask error:", upstream.status, errBody);
          return res.status(upstream.status >= 500 ? 502 : upstream.status).json({
            error: {
              type: "ServerError",
              code: "RAG_ASK_FAILED",
              message: "Q&A service returned an error",
            },
          });
        }

        const raw = await upstream.json();
        res.json(mapAskResponse(raw));
      } catch (fetchErr) {
        clearTimeout(timeout);
        if (fetchErr.name === "AbortError") {
          return res.status(504).json({
            error: {
              type: "ServerError",
              code: "RAG_TIMEOUT",
              message: "Q&A request timed out — try a more specific question",
            },
          });
        }
        throw fetchErr;
      }
    } catch (err) {
      console.error("POST /api/ask error:", err);

      // The Postprocessing Service is unreachable (not started, wrong port,
      // crashed). Report it as an upstream dependency failure rather than a
      // generic Gateway error so the cause is actionable.
      if (err.cause?.code === "ECONNREFUSED" || err.cause?.code === "ENOTFOUND") {
        return res.status(503).json({
          error: {
            type: "ServiceUnavailable",
            code: "RAG_UNAVAILABLE",
            message:
              "Q&A service is unavailable. The Postprocessing Service is not reachable.",
          },
        });
      }

      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to process question",
        },
      });
    }
  });

  return router;
}

module.exports = askRoutes;
module.exports.mapAskResponse = mapAskResponse;
