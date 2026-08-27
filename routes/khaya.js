/**
 * Khaya AI Routes
 *
 * Express router for Khaya AI (GhanaNLP) ASR endpoints.
 * Mounts at /api/khaya
 */

const express = require("express");
const khaya = require("../providers/khaya");
const requirePermission = require("../middleware/require-permission");

/**
 * Creates the Khaya AI router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} upload - Multer upload middleware instance
 * @returns {express.Router}
 */
function createRouter(requireSession, upload) {
  const router = express.Router();

  /**
   * POST /api/khaya/transcription
   *
   * Transcribes audio using Khaya AI ASR v3.
   * Body (multipart/form-data):
   *   - file: Audio file
   *   - language: Language code (e.g., "tw", "ee", "gaa", "dag")
   */
  router.post("/transcription", requireSession, requirePermission("upload_audio"), upload.single("file"), async (req, res) => {
    try {
      const { file, body } = req;
      const { language } = body;

      if (!file) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_INPUT",
            message: "Audio file is required. Send as multipart/form-data with field name 'file'",
          },
        });
      }

      if (!language) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_LANGUAGE",
            message: "Language code is required (e.g., 'tw' for Twi, 'ee' for Ewe, 'gaa' for Ga)",
          },
        });
      }

      const result = await khaya.transcribe(file.buffer, file.mimetype, language);
      res.json(result);
    } catch (err) {
      console.error("Khaya AI transcription error:", err);
      const status = err.statusCode || 500;
      res.status(status).json({
        error: {
          type: err.type || "TranscriptionError",
          code: err.code || "TRANSCRIPTION_FAILED",
          message: err.message || "An error occurred during Khaya AI transcription",
        },
      });
    }
  });

  /**
   * GET /api/khaya/languages
   *
   * Returns supported languages for Khaya AI ASR v3.
   */
  router.get("/languages", async (req, res) => {
    try {
      const languages = await khaya.getLanguages();
      res.json(languages);
    } catch (err) {
      console.error("Khaya languages error:", err);
      const status = err.statusCode || 500;
      res.status(status).json({
        error: {
          type: err.type || "ProviderError",
          code: err.code || "LANGUAGES_FETCH_FAILED",
          message: err.message,
        },
      });
    }
  });

  return router;
}

module.exports = createRouter;
