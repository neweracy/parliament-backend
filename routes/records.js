/**
 * Records Routes
 *
 * Express router for Hansard record CRUD endpoints.
 * Records belong to a sitting and represent individual transcription units.
 *
 * @module routes/records
 */

"use strict";

const express = require("express");

/**
 * Converts a snake_case DB row to a camelCase record object for the API response.
 * @param {Object} row - Database row
 * @returns {Object} camelCase record object
 */
function formatRecord(row) {
  return {
    id: row.id,
    sittingId: row.sitting_id,
    title: row.title,
    date: row.date,
    duration: row.duration,
    durationHours: row.duration_hours,
    language: row.language,
    audioFileName: row.audio_file_name,
    audioPath: row.audio_path,
    status: row.status,
    progress: row.progress,
    visibility: row.visibility,
    assigneeName: row.assignee_name,
    assigneeAvatar: row.assignee_avatar,
    assigneeRole: row.assignee_role,
    startTime: row.start_time,
    endTime: row.end_time,
    description: row.description,
    error: row.error,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

/**
 * Converts a DB row (with joined sitting fields) to a camelCase assigned-record object.
 * Extends formatRecord with parent sitting metadata.
 * @param {Object} row - Database row from hansard_record INNER JOIN sitting
 * @returns {Object} camelCase assigned record object
 */
function formatAssignedRecord(row) {
  return {
    ...formatRecord(row),
    sittingTitle: row.sitting_title,
    sittingPriority: row.sitting_priority,
  };
}

/**
 * Creates the Records router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function recordsRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * POST /api/sittings/:sittingId/records
   *
   * Creates a new Hansard record within the specified sitting.
   * Returns the created record with a server-generated ID.
   */
  router.post("/api/sittings/:sittingId/records", requireSession, express.json(), async (req, res) => {
    try {
      const { sittingId } = req.params;
      const {
        title,
        date,
        language,
        startTime,
        endTime,
        description,
        visibility,
      } = req.body;

      // Validate required fields
      if (!title || !date) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_REQUIRED_FIELDS",
            message: "title and date are required",
          },
        });
      }

      // Verify the sitting exists
      const sittingResult = await db.query(
        "SELECT id FROM sitting WHERE id = $1",
        [sittingId]
      );

      if (sittingResult.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SITTING_NOT_FOUND",
            message: "Sitting with id " + sittingId + " not found",
          },
        });
      }

      const result = await db.query(
        "INSERT INTO hansard_record (sitting_id, title, date, language, start_time, end_time, description, visibility) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *",
        [
          sittingId,
          title,
          date,
          language || "English",
          startTime || null,
          endTime || null,
          description || null,
          visibility || "Public",
        ]
      );

      res.status(201).json(formatRecord(result.rows[0]));
    } catch (err) {
      console.error("POST /api/sittings/:sittingId/records error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to create record",
        },
      });
    }
  });

  /**
   * GET /api/sittings/:sittingId/records/:id
   *
   * Returns a single Hansard record by ID within the specified sitting.
   */
  router.get("/api/sittings/:sittingId/records/:id", requireSession, async (req, res) => {
    try {
      const { sittingId, id } = req.params;

      const result = await db.query(
        "SELECT * FROM hansard_record WHERE id = $1 AND sitting_id = $2",
        [id, sittingId]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "RECORD_NOT_FOUND",
            message: "Record with id " + id + " not found in sitting " + sittingId,
          },
        });
      }

      res.json(formatRecord(result.rows[0]));
    } catch (err) {
      console.error("GET /api/sittings/:sittingId/records/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve record",
        },
      });
    }
  });

  /**
   * PATCH /api/sittings/:sittingId/records/:id
   *
   * Partial update of mutable record fields.
   * Allowed fields: title, status, assigneeName, assigneeAvatar, assigneeRole, visibility
   *
   * Validates that Published status requires a non-empty corrected transcript.
   */
  router.patch("/api/sittings/:sittingId/records/:id", requireSession, express.json(), async (req, res) => {
    try {
      const { sittingId, id } = req.params;
      const { title, status, assigneeName, assigneeAvatar, assigneeRole, visibility } = req.body;

      // Validate Published status transition requires a non-empty transcript
      if (status === "Published") {
        const transcriptCheck = await db.query(
          "SELECT 1 FROM transcript WHERE record_id = $1 AND corrected_text IS NOT NULL AND corrected_text != ''",
          [id]
        );

        if (transcriptCheck.rows.length === 0) {
          return res.status(422).json({
            error: {
              type: "ValidationError",
              code: "INVALID_STATUS_TRANSITION",
              message: "Cannot publish a record without a non-empty corrected transcript",
            },
          });
        }
      }

      // Build SET clause dynamically from provided fields
      const updates = [];
      const params = [];
      let paramIndex = 1;

      if (title !== undefined) {
        updates.push("title = $" + paramIndex++);
        params.push(title);
      }
      if (status !== undefined) {
        updates.push("status = $" + paramIndex++);
        params.push(status);
      }
      if (assigneeName !== undefined) {
        updates.push("assignee_name = $" + paramIndex++);
        params.push(assigneeName);
      }
      if (assigneeAvatar !== undefined) {
        updates.push("assignee_avatar = $" + paramIndex++);
        params.push(assigneeAvatar);
      }
      if (assigneeRole !== undefined) {
        updates.push("assignee_role = $" + paramIndex++);
        params.push(assigneeRole);
      }
      if (visibility !== undefined) {
        updates.push("visibility = $" + paramIndex++);
        params.push(visibility);
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

      const whereId = "$" + paramIndex;
      const whereSittingId = "$" + (paramIndex + 1);
      const result = await db.query(
        "UPDATE hansard_record SET " + updates.join(", ") + " WHERE id = " + whereId + " AND sitting_id = " + whereSittingId + " RETURNING *",
        [...params, id, sittingId]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "RECORD_NOT_FOUND",
            message: "Record with id " + id + " not found in sitting " + sittingId,
          },
        });
      }

      res.json(formatRecord(result.rows[0]));
    } catch (err) {
      console.error("PATCH /api/sittings/:sittingId/records/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to update record",
        },
      });
    }
  });

  /**
   * GET /api/records/assigned
   *
   * Returns all Hansard records assigned to the authenticated user.
   * User identity is passed via the x-user-name header.
   */
  router.get("/api/records/assigned", requireSession, async (req, res) => {
    try {
      const userName = req.headers["x-user-name"];

      if (!userName) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_USER_IDENTITY",
            message: "x-user-name header is required to identify the authenticated user",
          },
        });
      }

      const result = await db.query(
        `SELECT hr.*, s.title AS sitting_title, s.priority AS sitting_priority
         FROM hansard_record hr
         INNER JOIN sitting s ON s.id = hr.sitting_id
         WHERE hr.assignee_name = $1
         ORDER BY hr.created_at DESC`,
        [userName]
      );

      res.json({ data: result.rows.map(formatAssignedRecord) });
    } catch (err) {
      console.error("GET /api/records/assigned error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve assigned records",
        },
      });
    }
  });

  return router;
};
