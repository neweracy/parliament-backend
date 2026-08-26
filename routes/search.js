/**
 * Search Routes
 *
 * Express router for RAG hybrid search and search suggestions.
 * Proxies search queries to the Postprocessing Service and exposes
 * typeahead suggestions from indexed entity names and speaker labels.
 *
 * @module routes/search
 */

"use strict";

const express = require("express");
const requirePermission = require("../middleware/require-permission");

/**
 * Maximum allowed search limit (results per query).
 */
const MAX_LIMIT = 50;

/**
 * Default search limit when none is specified.
 */
const DEFAULT_LIMIT = 10;

/**
 * Default number of AI recommendations when not provided.
 *
 * Mirrors TARGET_SEARCH_RECOMMENDATION_COUNT in the Postprocessing Service
 * (app/rag/recommendations.py), which owns this band. Its Pydantic model
 * rejects anything outside 1..MAX_RECOMMENDATION_LIMIT, so the two must agree.
 */
const DEFAULT_RECOMMENDATION_LIMIT = 5;

/**
 * Maximum number of AI recommendations to return.
 *
 * Mirrors MAX_SEARCH_RECOMMENDATION_COUNT in the Postprocessing Service.
 */
const MAX_RECOMMENDATION_LIMIT = 8;

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
 * Map the raw /rag/recommendations body to the camelCase gateway response.
 *
 * Items are passed through as the upstream produced them. The gateway used to
 * scrape markdown bold markers out of follow-up questions here and drop
 * anything unbolded, which is what made the endpoint intermittently return an
 * empty list. Emphasis normalisation now happens once, upstream, in the shared
 * recommendation parser.
 *
 * @param {any} raw - Upstream JSON body
 * @param {number} limit - Defensive cap; upstream already honours it
 * @returns {{ recommendations: {text: string, reason: string}[], latencyMs: number, source: string, modelUsed: boolean }}
 */
function mapRecommendationResponse(raw, limit) {
  const recommendations = (raw?.recommendations || [])
    .filter(Boolean)
    .map((r) => ({
      text: typeof r.text === "string" ? r.text.trim() : "",
      reason: typeof r.reason === "string" ? r.reason.trim() : "",
    }))
    .filter((r) => r.text.length > 0)
    .slice(0, limit);

  return {
    recommendations,
    latencyMs: raw?.latency_ms ?? raw?.latencyMs ?? 0,
    source: typeof raw?.source === "string" ? raw.source : "deterministic",
    modelUsed: Boolean(raw?.model_used ?? raw?.modelUsed ?? false),
  };
}

/**
 * Creates the Search router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @param {import('../lib/cache').CacheUtil} [cache] - Optional cache utility instance
 * @returns {express.Router}
 */
module.exports = function searchRoutes(requireSession, db, cache) {
  const router = express.Router();

  /**
   * POST /api/search
   *
   * Executes a hybrid semantic + lexical search over indexed transcript chunks.
   * Proxies to the Postprocessing Service POST /rag/search endpoint.
   *
   * Body: { query, entityFilter?, dateFrom?, dateTo?, speaker?, limit? }
   * - limit defaults to 10, max 50; values >50 are clamped to 50.
   *
   * Returns the search results from the RAG pipeline.
   */
  router.post("/api/search", requireSession, requirePermission("search_hansard"), express.json(), async (req, res) => {
    try {
      const { query, entityFilter, dateFrom, dateTo, speaker, limit } = req.body;

      if (!query || typeof query !== "string" || query.trim().length === 0) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_QUERY",
            message: "A non-empty query string is required",
          },
        });
      }

      // Enforce limit: default 10, max 50, clamp >50 to 50
      let effectiveLimit = DEFAULT_LIMIT;
      if (limit !== undefined && limit !== null) {
        const parsed = Number(limit);
        if (!Number.isNaN(parsed) && parsed > 0) {
          effectiveLimit = Math.min(parsed, MAX_LIMIT);
        }
      }

      // Cache-aside: check cache before proxying to upstream
      let cacheKey;
      if (cache) {
        try {
          const hash = cache.hashParams({ query: query.trim(), entityFilter, dateFrom, dateTo, speaker, limit: effectiveLimit });
          cacheKey = cache.key("search", hash);
          const cached = await cache.get(cacheKey);
          if (cached) {
            return res.json(cached);
          }
        } catch {
          // Redis unavailable — continue without cache (graceful degradation)
        }
      }

      // Build request body for the Postprocessing Service
      const searchBody = {
        query: query.trim(),
        limit: effectiveLimit,
      };

      if (entityFilter) searchBody.entity_filter = entityFilter;
      if (dateFrom) searchBody.date_from = dateFrom;
      if (dateTo) searchBody.date_to = dateTo;
      if (speaker) searchBody.speaker = speaker;

      // Proxy to Postprocessing Service with 30s timeout
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);

      try {
        const upstream = await fetch(`${POSTPROCESS_URL}/rag/search`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${POSTPROCESS_TOKEN}`,
          },
          body: JSON.stringify(searchBody),
          signal: controller.signal,
        });

        clearTimeout(timeout);

        if (!upstream.ok) {
          const errBody = await upstream.text();
          console.error("Postprocessing /rag/search error:", upstream.status, errBody);
          return res.status(upstream.status >= 500 ? 502 : upstream.status).json({
            error: {
              type: "ServerError",
              code: "RAG_SEARCH_FAILED",
              message: "Search service returned an error",
            },
          });
        }

        const raw = await upstream.json();

        // The Python service returns snake_case field names. The frontend
        // expects camelCase. Map them here at the Gateway boundary so neither
        // side has to know about the other's conventions.
        const results = {
          results: (raw.results || []).map((r) => ({
            chunkId: r.chunk_id,
            chunkText: r.chunk_text,
            relevanceScore: r.relevance_score,
            transcriptId: r.transcript_id,
            speaker: r.speaker,
            startS: r.start_s,
            endS: r.end_s,
            matchedEntities: r.matched_entities || [],
            recordTitle: r.record_title,
            sittingTitle: r.sitting_title,
            date: r.date,
            // Navigation target — distinct identifier spaces from transcriptId.
            sittingId: r.sitting_id ?? null,
            recordId: r.record_id ?? null,
          })),
          totalMatched: raw.total_matched ?? raw.totalMatched ?? 0,
          latencyMs: raw.latency_ms ?? raw.latencyMs ?? 0,
        };

        // Cache successful response (fire-and-forget, never block the response)
        if (cache && cacheKey) {
          cache.set(cacheKey, results, 300).catch(() => {});
        }

        res.json(results);
      } catch (fetchErr) {
        clearTimeout(timeout);
        if (fetchErr.name === "AbortError") {
          return res.status(504).json({
            error: {
              type: "ServerError",
              code: "RAG_TIMEOUT",
              message: "Search request timed out — try a more specific query",
            },
          });
        }
        throw fetchErr;
      }
    } catch (err) {
      console.error("POST /api/search error:", err);

      // The Postprocessing Service is unreachable (not started, wrong port,
      // crashed). Report it as an upstream dependency failure rather than a
      // generic Gateway error so the cause is actionable.
      if (err.cause?.code === "ECONNREFUSED" || err.cause?.code === "ENOTFOUND") {
        return res.status(503).json({
          error: {
            type: "ServiceUnavailable",
            code: "RAG_UNAVAILABLE",
            message:
              "Search service is unavailable. The Postprocessing Service is not reachable.",
          },
        });
      }

      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to execute search",
        },
      });
    }
  });

  /**
   * GET /api/search/suggestions
   *
   * Returns typeahead suggestions: distinct entity names and speaker labels
   * from indexed transcript chunks.
   */
  router.get("/api/search/suggestions", requireSession, requirePermission("search_hansard"), async (req, res) => {
    try {
      // Check cache for suggestions
      if (cache) {
        const cacheKey = cache.key("suggestions", "current");
        const cached = await cache.get(cacheKey);
        if (cached) {
          return res.json(cached);
        }
      }

      // Get distinct entity names from transcript_chunk
      const entitiesResult = await db.query(
        `SELECT DISTINCT unnest(entity_names) AS name FROM transcript_chunk WHERE entity_names != '{}' ORDER BY name LIMIT 200`
      );

      // Get distinct speakers from transcript_chunk
      const speakersResult = await db.query(
        `SELECT DISTINCT speaker FROM transcript_chunk WHERE speaker IS NOT NULL ORDER BY speaker LIMIT 100`
      );

      const suggestions = {
        entities: entitiesResult.rows.map((row) => ({ name: row.name, kind: "entity" })),
        speakers: speakersResult.rows.map((row) => row.speaker),
      };

      // Cache suggestions with 1-hour TTL (fire-and-forget)
      if (cache) {
        const cacheKey = cache.key("suggestions", "current");
        cache.set(cacheKey, suggestions, 3600).catch(() => {});
      }

      res.json(suggestions);
    } catch (err) {
      console.error("GET /api/search/suggestions error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve search suggestions",
        },
      });
    }
  });

  /**
   * POST /api/search/recommendations
   *
   * Returns AI-backed search-query recommendations grounded in transcript data.
   * Proxies to the Postprocessing Service POST /rag/recommendations endpoint.
   *
   * Body: { query?, entityFilter?, dateFrom?, dateTo?, speaker?, limit? }
   * - query is optional: with none, the response offers entry points into the
   *   corpus rather than refinements of an existing query.
   * - limit defaults to 5, max 8; values outside that band are clamped.
   *
   * The upstream endpoint guarantees a non-empty set — it tops up from a
   * deterministic builder when the model returns too few — so a 200 here always
   * carries usable recommendations.
   */
  router.post("/api/search/recommendations", requireSession, requirePermission("search_hansard"), express.json(), async (req, res) => {
    try {
      const { query, entityFilter, dateFrom, dateTo, speaker, limit } = req.body || {};

      // Clamp into the band the upstream Pydantic model accepts. Sending a
      // value outside 1..MAX would come back as a 422 rather than a result.
      let effectiveLimit = DEFAULT_RECOMMENDATION_LIMIT;
      if (limit !== undefined && limit !== null) {
        const parsed = Number(limit);
        if (Number.isFinite(parsed) && parsed > 0) {
          effectiveLimit = Math.min(Math.floor(parsed), MAX_RECOMMENDATION_LIMIT);
        }
      }

      const recommendationBody = {
        limit: effectiveLimit,
      };

      if (typeof query === "string" && query.trim().length > 0) {
        recommendationBody.query = query.trim();
      }
      if (Array.isArray(entityFilter) && entityFilter.length > 0) recommendationBody.entity_filter = entityFilter;
      if (dateFrom) recommendationBody.date_from = dateFrom;
      if (dateTo) recommendationBody.date_to = dateTo;
      if (speaker) recommendationBody.speaker = speaker;

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);

      try {
        const upstream = await fetch(`${POSTPROCESS_URL}/rag/recommendations`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${POSTPROCESS_TOKEN}`,
          },
          body: JSON.stringify(recommendationBody),
          signal: controller.signal,
        });

        clearTimeout(timeout);

        if (!upstream.ok) {
          const errBody = await upstream.text();
          console.error("Postprocessing /rag/recommendations error:", upstream.status, errBody);
          return res.status(upstream.status >= 500 ? 502 : upstream.status).json({
            error: {
              type: "ServerError",
              code: "RAG_RECOMMENDATIONS_FAILED",
              message: "Recommendation service returned an error",
            },
          });
        }

        const raw = await upstream.json();
        return res.json(mapRecommendationResponse(raw, effectiveLimit));
      } catch (fetchErr) {
        clearTimeout(timeout);
        if (fetchErr.name === "AbortError") {
          return res.status(504).json({
            error: {
              type: "ServerError",
              code: "RAG_TIMEOUT",
              message: "Recommendation request timed out",
            },
          });
        }
        throw fetchErr;
      }
    } catch (err) {
      console.error("POST /api/search/recommendations error:", err);

      if (err.cause?.code === "ECONNREFUSED" || err.cause?.code === "ENOTFOUND") {
        return res.status(503).json({
          error: {
            type: "ServiceUnavailable",
            code: "RAG_UNAVAILABLE",
            message: "Recommendation service is unavailable. The Postprocessing Service is not reachable.",
          },
        });
      }

      return res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to generate search recommendations",
        },
      });
    }
  });

  return router;
};

module.exports.mapRecommendationResponse = mapRecommendationResponse;
module.exports.DEFAULT_RECOMMENDATION_LIMIT = DEFAULT_RECOMMENDATION_LIMIT;
module.exports.MAX_RECOMMENDATION_LIMIT = MAX_RECOMMENDATION_LIMIT;
