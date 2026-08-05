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
const requirePermission = require("../middleware/require-permission");

// ─── Allowed Values ──────────────────────────────────────────────────────────

const ALLOWED_TRANSCRIPTION_ENGINES = new Set(["deepgram", "khaya", "hybrid"]);
const LANGUAGE_OPTIONS_BY_ENGINE = {
  deepgram: ["en"],
  khaya: ["tw", "ga", "ee", "ha"],
  hybrid: ["en", "tw", "ga", "ee", "ha"],
};

function getAllowedLanguagesForEngine(engine) {
  return LANGUAGE_OPTIONS_BY_ENGINE[engine] || [];
}

// ─── Export Config Validation Constants ──────────────────────────────────────

const ALLOWED_PAGE_SIZES = new Set(["A4", "A5", "Letter"]);
const MARGIN_MIN = 0.5;
const MARGIN_MAX = 5.0;
const FONT_SIZE_MIN = 8;
const FONT_SIZE_MAX = 24;
const NAMING_PATTERN_MAX_LENGTH = 200;
const ALLOWED_TEMPLATE_VARIABLES = new Set([
  "sessionType",
  "date",
  "committee",
  "presidingOfficer",
  "recordId",
]);

// ─── Helpers ─────────────────────────────────────────────────────────────────

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
    exportConfig: row.export_config || null,
  };
}

/**
 * Validates the export configuration object.
 * Returns an error message string if invalid, or null if valid.
 *
 * @param {Object} config - The export config to validate
 * @returns {string|null} Error message or null
 */
function validateExportConfig(config) {
  if (!config || typeof config !== "object") {
    return "Export config must be an object";
  }

  // Validate PDF section
  if (config.pdf !== undefined) {
    if (typeof config.pdf !== "object" || config.pdf === null) {
      return "pdf must be an object";
    }

    const { pageSize, marginsCm, fontSizePt, fontFamily, pageNumbers, parliamentCrest, timestamps } = config.pdf;

    if (pageSize !== undefined && !ALLOWED_PAGE_SIZES.has(pageSize)) {
      return `Invalid page size: '${pageSize}'. Allowed values: ${[...ALLOWED_PAGE_SIZES].join(", ")}`;
    }

    if (marginsCm !== undefined) {
      if (typeof marginsCm !== "number" || marginsCm < MARGIN_MIN || marginsCm > MARGIN_MAX) {
        return `Margins must be a number between ${MARGIN_MIN} and ${MARGIN_MAX} cm`;
      }
    }

    if (fontSizePt !== undefined) {
      if (typeof fontSizePt !== "number" || !Number.isInteger(fontSizePt) || fontSizePt < FONT_SIZE_MIN || fontSizePt > FONT_SIZE_MAX) {
        return `Font size must be an integer between ${FONT_SIZE_MIN} and ${FONT_SIZE_MAX} pt`;
      }
    }

    if (fontFamily !== undefined && typeof fontFamily !== "string") {
      return "Font family must be a string";
    }

    if (pageNumbers !== undefined && typeof pageNumbers !== "boolean") {
      return "pageNumbers must be a boolean";
    }

    if (parliamentCrest !== undefined && typeof parliamentCrest !== "boolean") {
      return "parliamentCrest must be a boolean";
    }

    if (timestamps !== undefined && typeof timestamps !== "boolean") {
      return "timestamps must be a boolean";
    }
  }

  // Validate DOCX section
  if (config.docx !== undefined) {
    if (typeof config.docx !== "object" || config.docx === null) {
      return "docx must be an object";
    }

    const { hansardStyles, trackChanges, includeMetadata } = config.docx;

    if (hansardStyles !== undefined && typeof hansardStyles !== "boolean") {
      return "hansardStyles must be a boolean";
    }

    if (trackChanges !== undefined && typeof trackChanges !== "boolean") {
      return "trackChanges must be a boolean";
    }

    if (includeMetadata !== undefined && typeof includeMetadata !== "boolean") {
      return "includeMetadata must be a boolean";
    }
  }

  // Validate naming pattern
  if (config.namingPattern !== undefined) {
    if (typeof config.namingPattern !== "string") {
      return "namingPattern must be a string";
    }

    if (config.namingPattern.length > NAMING_PATTERN_MAX_LENGTH) {
      return `Naming pattern must not exceed ${NAMING_PATTERN_MAX_LENGTH} characters`;
    }

    // Extract template variables from the pattern
    const variableRegex = /\{([^}]+)\}/g;
    let match;
    const invalidVars = [];
    while ((match = variableRegex.exec(config.namingPattern)) !== null) {
      if (!ALLOWED_TEMPLATE_VARIABLES.has(match[1])) {
        invalidVars.push(`{${match[1]}}`);
      }
    }

    if (invalidVars.length > 0) {
      return `Invalid template variables in naming pattern: ${invalidVars.join(", ")}. Allowed variables: ${[...ALLOWED_TEMPLATE_VARIABLES].map(v => `{${v}}`).join(", ")}`;
    }
  }

  return null;
}

/**
 * Creates the Settings router.
 * @param {Function} requireSession - Auth middleware (JWT or Cognito depending on AUTH_MODE)
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function settingsRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * GET /api/settings
   *
   * Returns the singleton settings row including export_config.
   * Permission: view_records
   */
  router.get("/api/settings", requireSession, requirePermission("view_records"), async (req, res) => {
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
   * Permission: system_config
   */
  router.patch("/api/settings", requireSession, requirePermission("system_config"), express.json(), async (req, res) => {
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

      let effectiveEngine = transcriptionEngine;
      if (defaultLanguage !== undefined) {
        const currentSettings = await db.query("SELECT transcription_engine FROM app_settings WHERE id = 1");
        if (currentSettings.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "SETTINGS_NOT_FOUND",
              message: "Settings not found",
            },
          });
        }

        if (!effectiveEngine) {
          effectiveEngine = currentSettings.rows[0].transcription_engine;
        }
      }

      // Validate defaultLanguage against the selected engine
      if (defaultLanguage !== undefined) {
        const allowedLanguages = getAllowedLanguagesForEngine(effectiveEngine);
        if (!allowedLanguages.includes(defaultLanguage)) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "VALIDATION_ERROR",
              message: `Invalid default_language value: '${defaultLanguage}' for engine '${effectiveEngine}'. Allowed values: ${allowedLanguages.join(", ")}`,
            },
          });
        }
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

  /**
   * GET /api/settings/export
   *
   * Returns the export configuration from the app_settings table.
   * Permission: system_config
   */
  router.get("/api/settings/export", requireSession, requirePermission("system_config"), async (req, res) => {
    try {
      const result = await db.query("SELECT export_config FROM app_settings WHERE id = 1");

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SETTINGS_NOT_FOUND",
            message: "Settings not found",
          },
        });
      }

      res.json(result.rows[0].export_config);
    } catch (err) {
      console.error("GET /api/settings/export error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve export configuration",
        },
      });
    }
  });

  /**
   * PATCH /api/settings/export
   *
   * Validates and persists the export configuration.
   * Merges the provided fields with the existing config (partial update).
   * Permission: system_config
   */
  router.patch("/api/settings/export", requireSession, requirePermission("system_config"), express.json(), async (req, res) => {
    try {
      const exportConfig = req.body;

      // Validate the export config
      const validationError = validateExportConfig(exportConfig);
      if (validationError) {
        return res.status(422).json({
          error: {
            type: "ValidationError",
            code: "VALIDATION_ERROR",
            message: validationError,
          },
        });
      }

      // Fetch current config for merging
      const current = await db.query("SELECT export_config FROM app_settings WHERE id = 1");

      if (current.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SETTINGS_NOT_FOUND",
            message: "Settings not found",
          },
        });
      }

      const existingConfig = current.rows[0].export_config || {};

      // Deep merge: top-level keys (pdf, docx, namingPattern)
      const mergedConfig = { ...existingConfig };

      if (exportConfig.pdf !== undefined) {
        mergedConfig.pdf = { ...(existingConfig.pdf || {}), ...exportConfig.pdf };
      }

      if (exportConfig.docx !== undefined) {
        mergedConfig.docx = { ...(existingConfig.docx || {}), ...exportConfig.docx };
      }

      if (exportConfig.namingPattern !== undefined) {
        mergedConfig.namingPattern = exportConfig.namingPattern;
      }

      // Persist the merged config
      const result = await db.query(
        "UPDATE app_settings SET export_config = $1, updated_at = now() WHERE id = 1 RETURNING export_config",
        [JSON.stringify(mergedConfig)]
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

      res.json(result.rows[0].export_config);
    } catch (err) {
      console.error("PATCH /api/settings/export error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to update export configuration",
        },
      });
    }
  });

  return router;
};

// Export validation sets and helpers for testing
module.exports.ALLOWED_TRANSCRIPTION_ENGINES = ALLOWED_TRANSCRIPTION_ENGINES;
module.exports.LANGUAGE_OPTIONS_BY_ENGINE = LANGUAGE_OPTIONS_BY_ENGINE;
module.exports.getAllowedLanguagesForEngine = getAllowedLanguagesForEngine;
module.exports.ALLOWED_PAGE_SIZES = ALLOWED_PAGE_SIZES;
module.exports.ALLOWED_TEMPLATE_VARIABLES = ALLOWED_TEMPLATE_VARIABLES;
module.exports.validateExportConfig = validateExportConfig;
