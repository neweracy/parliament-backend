/**
 * Settings Routes
 *
 * Express router for application settings persistence.
 * Mounts at /api/settings
 *
 * @module routes/settings
 */

"use strict";

const express = require("express");

// ─── Allowed Values ──────────────────────────────────────────────────────────

const ALLOWED_TRANSCRIPTION_ENGINES = new Set(["deepgram", "khaya", "hybrid"]);
const ALLOWED_LANGUAGES = new Set(["en", "tw", "ga", "ee", "ha"]);

/**
 * Formats a DB row into a camelCase settings object.
 * @param {Object} row - Database row from app_settings
 * @returns {Object} camelCase settings object
 */
function formatSettings(row) {
  return {
    transcriptionEngine: row.transcription_engine,
    defaultLanguage: row.default_language,
    customDictionary: row.custom_dictionary || [],
    autoSaveIntervalS: row.auto_save_interval_s,
  };
}

/**
 * Creates the Settings router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function settingsRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * GET /api/settings
   *
   * Returns the singleton settings row.
   */
  router.get("/api/settings", requireSession, async (req, res) => {
    try {
      const result = await db.query("SELECT * FROM app_settings WHERE id = 1");

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SETTINGS_NOT_FOUND",
            message: "Settings not found",
          },
        });
      }

      res.json(formatSettings(result.rows[0]));
    } catch (err) {
      console.error("GET /api/settings error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve settings",
        },
      });
    }
  });

  /**
   * PATCH /api/settings
   *
   * Updates specified setting fields. Validates values against allowed sets.
   * Returns 422 for invalid values.
   */
  router.patch("/api/settings", requireSession, express.json(), async (req, res) => {
    try {
      const { transcriptionEngine, defaultLanguage, customDictionary, autoSaveIntervalS } = req.body;

      // Validate transcriptionEngine
      if (transcriptionEngine !== undefined && !ALLOWED_TRANSCRIPTION_ENGINES.has(transcriptionEngine)) {
        return res.status(422).json({
          error: {
            type: "ValidationError",
            code: "VALIDATION_ERROR",
            message: `Invalid transcription_engine value: '${transcriptionEngine}'. Allowed values: ${[...ALLOWED_TRANSCRIPTION_ENGINES].join(", ")}`,
          },
        });
      }

      // Validate defaultLanguage
      if (defaultLanguage !== undefined && !ALLOWED_LANGUAGES.has(defaultLanguage)) {
        return res.status(422).json({
          error: {
            type: "ValidationError",
            code: "VALIDATION_ERROR",
            message: `Invalid default_language value: '${defaultLanguage}'. Allowed values: ${[...ALLOWED_LANGUAGES].join(", ")}`,
          },
        });
      }

      // Validate autoSaveIntervalS
      if (autoSaveIntervalS !== undefined && (typeof autoSaveIntervalS !== "number" || autoSaveIntervalS <= 0)) {
        return res.status(422).json({
          error: {
            type: "ValidationError",
            code: "VALIDATION_ERROR",
            message: "auto_save_interval_s must be a positive number",
          },
        });
      }

      // Validate customDictionary (must be an array of strings if provided)
      if (customDictionary !== undefined && !Array.isArray(customDictionary)) {
        return res.status(422).json({
          error: {
            type: "ValidationError",
            code: "VALIDATION_ERROR",
            message: "custom_dictionary must be an array",
          },
        });
      }

      // Build SET clause dynamically from provided fields
      const updates = [];
      const params = [];
      let paramIndex = 1;

      if (transcriptionEngine !== undefined) {
        updates.push(`transcription_engine = $${paramIndex++}`);
        params.push(transcriptionEngine);
      }
      if (defaultLanguage !== undefined) {
        updates.push(`default_language = $${paramIndex++}`);
        params.push(defaultLanguage);
      }
      if (customDictionary !== undefined) {
        updates.push(`custom_dictionary = $${paramIndex++}`);
        params.push(JSON.stringify(customDictionary));
      }
      if (autoSaveIntervalS !== undefined) {
        updates.push(`auto_save_interval_s = $${paramIndex++}`);
        params.push(autoSaveIntervalS);
      }

      if (updates.length === 0) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "NO_FIELDS_TO_UPDATE",
            message: "At least one field must be provided for update",
          },
        });
      }

      // Always update updated_at
      updates.push("updated_at = now()");

      const result = await db.query(
        `UPDATE app_settings SET ${updates.join(", ")} WHERE id = 1 RETURNING *`,
        params
      );

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SETTINGS_NOT_FOUND",
            message: "Settings not found",
          },
        });
      }

      res.json(formatSettings(result.rows[0]));
    } catch (err) {
      console.error("PATCH /api/settings error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to update settings",
        },
      });
    }
  });

  return router;
};

// Export validation sets for testing
module.exports.ALLOWED_TRANSCRIPTION_ENGINES = ALLOWED_TRANSCRIPTION_ENGINES;
module.exports.ALLOWED_LANGUAGES = ALLOWED_LANGUAGES;
