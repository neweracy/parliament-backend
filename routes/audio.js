/**
 * Audio Routes
 *
 * Express router for audio file upload and retrieval endpoints.
 * Mounts at /api/sittings/:sittingId/records/:recordId/audio
 *
 * @module routes/audio
 */

"use strict";

const express = require("express");
const multer = require("multer");
const path = require("path");
const fs = require("fs");
const requirePermission = require("../middleware/require-permission");

/**
 * Allowed audio MIME types for upload validation (Requirement 4.3).
 */
const ALLOWED_MIME_TYPES = new Set([
  "audio/mpeg",
  "audio/wav",
  "audio/ogg",
  "audio/webm",
  "audio/mp4",
]);

/**
 * Maximum file size: 500 MB (Requirement 4.4).
 */
const MAX_FILE_SIZE = 500 * 1024 * 1024;

/**
 * Creates the Audio router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function audioRoutes(requireSession, db) {
  const router = express.Router();

  // Resolve the audio storage directory from env or use default
  const storagePath = process.env.AUDIO_STORAGE_PATH || "./uploads/audio";

  // Ensure the storage directory exists
  const absoluteStoragePath = path.resolve(storagePath);
  fs.mkdirSync(absoluteStoragePath, { recursive: true });

  // Configure multer for disk storage with unique filenames
  const storage = multer.diskStorage({
    destination(_req, _file, cb) {
      cb(null, absoluteStoragePath);
    },
    filename(_req, file, cb) {
      const uniqueSuffix = `${Date.now()}-${Math.round(Math.random() * 1e9)}`;
      const ext = path.extname(file.originalname) || "";
      cb(null, `${uniqueSuffix}${ext}`);
    },
  });

  // File filter to validate MIME type before accepting upload
  function fileFilter(_req, file, cb) {
    if (ALLOWED_MIME_TYPES.has(file.mimetype)) {
      cb(null, true);
    } else {
      const err = new Error("Unsupported media type");
      err.code = "UNSUPPORTED_MEDIA";
      cb(err, false);
    }
  }

  const upload = multer({
    storage,
    fileFilter,
    limits: { fileSize: MAX_FILE_SIZE },
  });

  /**
   * POST /api/sittings/:sittingId/records/:recordId/audio
   *
   * Accepts a multipart file upload, validates MIME type and size,
   * stores the file to durable storage, and associates the path with the record.
   */
  router.post(
    "/api/sittings/:sittingId/records/:recordId/audio",
    requireSession,
    requirePermission("upload_audio"),
    function handleUpload(req, res, next) {
      upload.single("file")(req, res, function (err) {
        if (err) {
          // MIME type rejection
          if (err.code === "UNSUPPORTED_MEDIA") {
            return res.status(415).json({
              error: {
                type: "ValidationError",
                code: "UNSUPPORTED_MEDIA",
                message:
                  "Unsupported file type. Accepted types: audio/mpeg, audio/wav, audio/ogg, audio/webm, audio/mp4",
              },
            });
          }
          // File size exceeded
          if (err.code === "LIMIT_FILE_SIZE") {
            return res.status(413).json({
              error: {
                type: "ValidationError",
                code: "FILE_TOO_LARGE",
                message: "File size exceeds the maximum allowed size of 500 MB",
              },
            });
          }
          // Other multer errors
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
        const { sittingId, recordId } = req.params;

        if (!req.file) {
          return res.status(400).json({
            error: {
              type: "ValidationError",
              code: "MISSING_FILE",
              message: "No file was provided in the upload",
            },
          });
        }

        // Verify the record exists and belongs to the sitting
        const recordResult = await db.query(
          "SELECT id FROM hansard_record WHERE id = $1 AND sitting_id = $2",
          [recordId, sittingId]
        );

        if (recordResult.rows.length === 0) {
          // Clean up the uploaded file since the record doesn't exist
          fs.unlink(req.file.path, () => {});
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "RECORD_NOT_FOUND",
              message: `Record with id ${recordId} not found in sitting ${sittingId}`,
            },
          });
        }

        // Update the record with the audio file info
        await db.query(
          `UPDATE hansard_record
           SET audio_file_name = $1, audio_path = $2, updated_at = now()
           WHERE id = $3`,
          [req.file.originalname, req.file.path, recordId]
        );

        res.status(200).json({
          message: "Audio uploaded successfully",
          fileName: req.file.originalname,
          size: req.file.size,
        });
      } catch (err) {
        console.error("POST /api/.../audio error:", err);
        // Clean up file on error if it was saved
        if (req.file && req.file.path) {
          fs.unlink(req.file.path, () => {});
        }
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to process audio upload",
          },
        });
      }
    }
  );

  /**
   * GET /api/sittings/:sittingId/records/:recordId/audio
   *
   * Streams the stored audio file back to the client.
   * Returns 404 if no audio is associated with the record.
   */
  router.get(
    "/api/sittings/:sittingId/records/:recordId/audio",
    requireSession,
    requirePermission("view_records"),
    async (req, res) => {
      try {
        const { sittingId, recordId } = req.params;

        // Look up the audio path from the record
        const recordResult = await db.query(
          "SELECT audio_path, audio_file_name FROM hansard_record WHERE id = $1 AND sitting_id = $2",
          [recordId, sittingId]
        );

        if (recordResult.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "RECORD_NOT_FOUND",
              message: `Record with id ${recordId} not found in sitting ${sittingId}`,
            },
          });
        }

        const { audio_path: audioPath, audio_file_name: audioFileName } =
          recordResult.rows[0];

        if (!audioPath) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "AUDIO_NOT_FOUND",
              message: "No audio file is associated with this record",
            },
          });
        }

        // Verify the file still exists on disk
        if (!fs.existsSync(audioPath)) {
          return res.status(404).json({
            error: {
              type: "NotFoundError",
              code: "AUDIO_NOT_FOUND",
              message: "Audio file not found on storage",
            },
          });
        }

        // Determine content type from file extension
        const ext = path.extname(audioPath).toLowerCase();
        const mimeMap = {
          ".mp3": "audio/mpeg",
          ".wav": "audio/wav",
          ".ogg": "audio/ogg",
          ".webm": "audio/webm",
          ".mp4": "audio/mp4",
          ".m4a": "audio/mp4",
        };
        const contentType = mimeMap[ext] || "application/octet-stream";

        // Get file stats for Content-Length
        const stat = fs.statSync(audioPath);

        res.setHeader("Content-Type", contentType);
        res.setHeader("Content-Length", stat.size);
        if (audioFileName) {
          res.setHeader(
            "Content-Disposition",
            `inline; filename="${audioFileName}"`
          );
        }

        // Stream the file
        const readStream = fs.createReadStream(audioPath);
        readStream.pipe(res);

        readStream.on("error", (streamErr) => {
          console.error("Audio stream error:", streamErr);
          if (!res.headersSent) {
            res.status(500).json({
              error: {
                type: "ServerError",
                code: "INTERNAL_ERROR",
                message: "Failed to stream audio file",
              },
            });
          }
        });
      } catch (err) {
        console.error("GET /api/.../audio error:", err);
        res.status(500).json({
          error: {
            type: "ServerError",
            code: "INTERNAL_ERROR",
            message: "Failed to retrieve audio file",
          },
        });
      }
    }
  );

  return router;
};
