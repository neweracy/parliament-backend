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
 * Creates the Ask router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function askRoutes(requireSession, _db) {
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
  router.post("/api/ask", requireSession, express.json(), async (req, res) => {
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

        // Map snake_case from the Python service to camelCase for the frontend.
        const result = {
          answer: raw.answer,
          citations: (raw.citations || []).map((c) => ({
            transcriptId: c.transcript_id,
            chunkId: c.chunk_id,
            speaker: c.speaker,
            startS: c.start_s,
            endS: c.end_s,
            excerpt: c.excerpt,
          })),
          sourceChunks: (raw.source_chunks || []).map((c) => ({
            chunkId: c.chunk_id,
            transcriptId: c.transcript_id,
            text: c.text,
            speaker: c.speaker,
            startS: c.start_s,
            endS: c.end_s,
            relevanceScore: c.relevance_score,
          })),
          recommendations: (raw.recommendations || []).map((r) => ({
            text: r.text,
            reason: r.reason,
          })),
          latencyMs: raw.latency_ms ?? raw.latencyMs ?? 0,
        };

        res.json(result);
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
};
