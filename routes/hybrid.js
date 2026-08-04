/**
 * Hybrid Confidence Transcription Routes
 *
 * Express router for the hybrid confidence-correction pipeline. Runs a primary
 * Deepgram transcription, detects low-confidence segments, and corrects them via
 * Khaya AI (GhanaNLP) before returning a unified transcript.
 * Mounts at /api/transcription/hybrid
 */

const express = require("express");
const { loadHybridConfig } = require("../lib/hybrid/config");
const { runHybridPipeline } = require("../lib/hybrid/pipeline");
const requirePermission = require("../middleware/require-permission");

/**
 * @typedef {import('../lib/hybrid/pipeline').HybridDeps} HybridDeps
 */

/**
 * Creates the hybrid transcription router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} upload - Multer upload middleware instance (memory storage)
 * @param {HybridDeps} deps - Injected pipeline collaborators
 * @returns {express.Router}
 */
function createRouter(requireSession, upload, deps) {
  const router = express.Router();

  /**
   * POST /api/transcription/hybrid
   *
   * Transcribes audio using the hybrid confidence-correction pipeline.
   * Body (multipart/form-data):
   *   - file: Audio file
   */
  router.post("/", requireSession, requirePermission("upload_audio"), upload.single("file"), async (req, res) => {
    try {
      const { file } = req;

      if (!file) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_INPUT",
            message: "Audio file is required. Send as multipart/form-data with field name 'file'",
          },
        });
      }

      const config = loadHybridConfig();
      const result = await runHybridPipeline(
        { buffer: file.buffer, mimetype: file.mimetype },
        deps,
        config
      );
      res.json(result);
    } catch (err) {
      console.error("Hybrid transcription error:", err);
      const status = err.statusCode || 500;
      res.status(status).json({
        error: {
          type: err.type || "TranscriptionError",
          code: err.code || "TRANSCRIPTION_FAILED",
          message: err.message || "An error occurred during hybrid transcription",
        },
      });
    }
  });

  return router;
}

module.exports = createRouter;
