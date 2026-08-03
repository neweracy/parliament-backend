/**
 * Dictionary Routes
 *
 * Express router for Custom Dictionary CRUD operations.
 * Mounts at /api/dictionary
 *
 * The dictionary is stored as a JSON array in the `custom_dictionary` column
 * of the `app_settings` table (row id=1).
 *
 * @module routes/dictionary
 */

"use strict";

const express = require("express");
const multer = require("multer");

const requirePermission = require("../middleware/require-permission");

// ─── Constants ──────────────────────────────────────────────────────────────

/** Maximum number of terms allowed in the dictionary. */
const MAX_DICTIONARY_SIZE = 10_000;

/** Maximum CSV upload size in bytes (1 MB). */
const MAX_CSV_SIZE = 1 * 1024 * 1024;

/** Default page size for paginated listing. */
const DEFAULT_PAGE_SIZE = 20;

/**
 * Allowed characters in a dictionary term.
 * Letters (including accented/extended Latin), digits, spaces, hyphens,
 * periods, and apostrophes.
 */
const TERM_REGEX = /^[a-zA-Z0-9\s\-.'\u00C0-\u024F]+$/;

// ─── Validation Helpers ─────────────────────────────────────────────────────

/**
 * Validates a single dictionary term.
 * @param {string} term - The term to validate
 * @returns {{ valid: boolean, reason?: string }}
 */
function validateTerm(term) {
  if (typeof term !== "string") {
    return { valid: false, reason: "Term must be a string" };
  }
  const trimmed = term.trim();
  if (trimmed.length === 0) {
    return { valid: false, reason: "Term must not be empty" };
  }
  if (trimmed.length > 200) {
    return { valid: false, reason: "Term must not exceed 200 characters" };
  }
  if (!TERM_REGEX.test(trimmed)) {
    return { valid: false, reason: "Term contains disallowed characters" };
  }
  return { valid: true };
}

/**
 * Case-insensitive check whether a term exists in the dictionary.
 * @param {string[]} dictionary - Current dictionary array
 * @param {string} term - The term to check
 * @returns {boolean}
 */
function isDuplicate(dictionary, term) {
  const lower = term.toLowerCase();
  return dictionary.some((existing) => existing.toLowerCase() === lower);
}

// ─── Route Factory ──────────────────────────────────────────────────────────

/**
 * Creates the Dictionary router.
 * @param {Function} authMiddleware - Authentication middleware (Cognito or legacy)
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function dictionaryRoutes(authMiddleware, db) {
  const router = express.Router();

  // Multer configuration for CSV upload (memory storage, 1 MB limit)
  const csvUpload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: MAX_CSV_SIZE },
  });

  // ─── GET /api/dictionary — paginated list ──────────────────────────────

  router.get(
    "/api/dictionary",
    authMiddleware,
    requirePermission("system_config"),
    async (req, res) => {
      try {
        const page = Math.max(1, parseInt(req.query.page, 10) || 1);
        const pageSize = Math.max(1, Math.min(100, parseInt(req.query.pageSize, 10) || DEFAULT_PAGE_SIZE));

        const result = await db.query(
          "SELECT custom_dictionary FROM app_settings WHERE id = 1"
        );

        if (result.rows.length === 0) {
          return res.json({ terms: [], total: 0, page, pageSize });
        }

        const dictionary = result.rows[0].custom_dictionary || [];
        const total = dictionary.length;
        const startIndex = (page - 1) * pageSize;
        const terms = dictionary.slice(startIndex, startIndex + pageSize);

        res.json({ terms, total, page, pageSize });
      } catch (err) {
        console.error("GET /api/dictionary error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to retrieve dictionary",
          },
        });
      }
    }
  );

  // ─── POST /api/dictionary — add single term ────────────────────────────

  router.post(
    "/api/dictionary",
    authMiddleware,
    requirePermission("system_config"),
    express.json(),
    async (req, res) => {
      try {
        const { term } = req.body;

        // Validate term
        const validation = validateTerm(term);
        if (!validation.valid) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "VALIDATION_ERROR",
              message: validation.reason,
            },
          });
        }

        const trimmedTerm = term.trim();

        // Fetch current dictionary
        const result = await db.query(
          "SELECT custom_dictionary FROM app_settings WHERE id = 1"
        );
        const dictionary = result.rows[0]?.custom_dictionary || [];

        // Case-insensitive duplicate check
        if (isDuplicate(dictionary, trimmedTerm)) {
          return res.status(409).json({
            error: {
              type: "ConflictError",
              code: "DUPLICATE_TERM",
              message: `Term '${trimmedTerm}' already exists in the dictionary (case-insensitive)`,
            },
          });
        }

        // Capacity check
        if (dictionary.length >= MAX_DICTIONARY_SIZE) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "DICTIONARY_FULL",
              message: `Dictionary has reached the maximum capacity of ${MAX_DICTIONARY_SIZE} terms`,
            },
          });
        }

        // Append term and persist
        dictionary.push(trimmedTerm);
        await db.query(
          "UPDATE app_settings SET custom_dictionary = $1, updated_at = now() WHERE id = 1",
          [JSON.stringify(dictionary)]
        );

        res.status(201).json({
          term: trimmedTerm,
          total: dictionary.length,
        });
      } catch (err) {
        console.error("POST /api/dictionary error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to add term",
          },
        });
      }
    }
  );

  // ─── DELETE /api/dictionary/:term — remove a term ──────────────────────

  router.delete(
    "/api/dictionary/:term",
    authMiddleware,
    requirePermission("system_config"),
    async (req, res) => {
      try {
        const termToRemove = decodeURIComponent(req.params.term);

        // Fetch current dictionary
        const result = await db.query(
          "SELECT custom_dictionary FROM app_settings WHERE id = 1"
        );
        const dictionary = result.rows[0]?.custom_dictionary || [];

        // Find term (case-insensitive)
        const lowerTerm = termToRemove.toLowerCase();
        const index = dictionary.findIndex(
          (existing) => existing.toLowerCase() === lowerTerm
        );

        if (index === -1) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "TERM_NOT_FOUND",
              message: `Term '${termToRemove}' not found in the dictionary`,
            },
          });
        }

        // Remove term and persist
        dictionary.splice(index, 1);
        await db.query(
          "UPDATE app_settings SET custom_dictionary = $1, updated_at = now() WHERE id = 1",
          [JSON.stringify(dictionary)]
        );

        res.json({ removed: termToRemove, total: dictionary.length });
      } catch (err) {
        console.error("DELETE /api/dictionary/:term error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to remove term",
          },
        });
      }
    }
  );

  // ─── POST /api/dictionary/import — bulk CSV import ─────────────────────

  router.post(
    "/api/dictionary/import",
    authMiddleware,
    requirePermission("system_config"),
    (req, res, next) => {
      csvUpload.single("file")(req, res, (err) => {
        if (err) {
          if (err.code === "LIMIT_FILE_SIZE") {
            return res.status(413).json({
              error: {
                type: "ValidationError",
                code: "FILE_TOO_LARGE",
                message: "CSV file exceeds maximum size of 1 MB",
              },
            });
          }
          return res.status(400).json({
            error: {
              type: "ValidationError",
              code: "UPLOAD_ERROR",
              message: err.message || "File upload failed",
            },
          });
        }
        next();
      });
    },
    async (req, res) => {
      try {
        if (!req.file) {
          return res.status(400).json({
            error: {
              type: "ValidationError",
              code: "MISSING_FILE",
              message: "A CSV file must be provided",
            },
          });
        }

        // Parse CSV content — each line is one term
        const content = req.file.buffer.toString("utf-8");
        const lines = content.split(/\r?\n/);

        // Fetch current dictionary
        const result = await db.query(
          "SELECT custom_dictionary FROM app_settings WHERE id = 1"
        );
        const dictionary = result.rows[0]?.custom_dictionary || [];

        // Build a lowercase set for efficient duplicate detection
        const existingLower = new Set(
          dictionary.map((t) => t.toLowerCase())
        );

        let addedCount = 0;
        const warnings = [];
        const MAX_WARNINGS = 100;

        for (let i = 0; i < lines.length; i++) {
          const lineNum = i + 1;
          const raw = lines[i].trim();

          // Skip empty lines silently
          if (raw.length === 0) continue;

          // Validate term
          const validation = validateTerm(raw);
          if (!validation.valid) {
            if (warnings.length < MAX_WARNINGS) {
              warnings.push({ line: lineNum, term: raw.substring(0, 50), reason: validation.reason });
            }
            continue;
          }

          // Check duplicate (against existing + newly added in this import)
          if (existingLower.has(raw.toLowerCase())) {
            if (warnings.length < MAX_WARNINGS) {
              warnings.push({ line: lineNum, term: raw.substring(0, 50), reason: "Duplicate term (case-insensitive)" });
            }
            continue;
          }

          // Capacity check
          if (dictionary.length >= MAX_DICTIONARY_SIZE) {
            if (warnings.length < MAX_WARNINGS) {
              warnings.push({ line: lineNum, term: raw.substring(0, 50), reason: "Dictionary capacity reached (10,000 terms)" });
            }
            continue;
          }

          // Add term
          dictionary.push(raw);
          existingLower.add(raw.toLowerCase());
          addedCount++;
        }

        // Persist updated dictionary
        await db.query(
          "UPDATE app_settings SET custom_dictionary = $1, updated_at = now() WHERE id = 1",
          [JSON.stringify(dictionary)]
        );

        res.json({
          added: addedCount,
          total: dictionary.length,
          warnings: warnings.length > 0 ? warnings : undefined,
        });
      } catch (err) {
        console.error("POST /api/dictionary/import error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to import dictionary terms",
          },
        });
      }
    }
  );

  return router;
};

// Export constants for testing
module.exports.MAX_DICTIONARY_SIZE = MAX_DICTIONARY_SIZE;
module.exports.MAX_CSV_SIZE = MAX_CSV_SIZE;
module.exports.TERM_REGEX = TERM_REGEX;
module.exports.validateTerm = validateTerm;
module.exports.isDuplicate = isDuplicate;
