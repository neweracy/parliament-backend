/**
 * Sittings Routes
 *
 * Express router for parliamentary sitting CRUD endpoints.
 * Mounts at /api/sittings
 *
 * @module routes/sittings
 */

"use strict";

const express = require("express");

const requirePermission = require("../middleware/require-permission");

/**
 * Converts a snake_case DB row to a camelCase sitting object for the API response.
 * @param {Object} row - Database row
 * @returns {Object} camelCase sitting object
 */
function formatSitting(row) {
  return {
    id: row.id,
    title: row.title,
    description: row.description,
    sessionType: row.session_type,
    committee: row.committee,
    presidingOfficer: row.presiding_officer,
    parliament: row.parliament,
    dateFrom: row.date_from,
    dateTo: row.date_to,
    status: row.status,
    priority: row.priority,
    participants: row.participants,
    topic: row.topic,
    orderPaperRef: row.order_paper_ref,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

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
 * Creates the Sittings router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function sittingsRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * GET /api/sittings
   *
   * Returns a paginated list of sittings with optional filters.
   * Query params: page (default 1), pageSize (default 20), status, sessionType, dateFrom, dateTo
   * Excludes Archived sittings by default unless status=Archived is explicitly requested.
   */
  router.get("/api/sittings", requireSession, requirePermission("view_records"), async (req, res) => {
    try {
      const page = Math.max(1, parseInt(req.query.page, 10) || 1);
      const pageSize = Math.max(1, Math.min(100, parseInt(req.query.pageSize, 10) || 20));
      const offset = (page - 1) * pageSize;

      const { status, sessionType, dateFrom, dateTo } = req.query;

      const conditions = [];
      const params = [];
      let paramIndex = 1;

      // Exclude Archived by default unless specifically filtered
      if (status) {
        conditions.push(`status = $${paramIndex++}`);
        params.push(status);
      } else {
        conditions.push(`status != $${paramIndex++}`);
        params.push("Archived");
      }

      if (sessionType) {
        conditions.push(`session_type = $${paramIndex++}`);
        params.push(sessionType);
      }

      if (dateFrom) {
        conditions.push(`date_from >= $${paramIndex++}`);
        params.push(dateFrom);
      }

      if (dateTo) {
        conditions.push(`date_to <= $${paramIndex++}`);
        params.push(dateTo);
      }

      const whereClause = conditions.length > 0
        ? `WHERE ${conditions.join(" AND ")}`
        : "";

      // Get total count
      const countResult = await db.query(
        `SELECT COUNT(*) AS total FROM sitting ${whereClause}`,
        params
      );
      const total = parseInt(countResult.rows[0].total, 10);

      // Get paginated data
      const dataResult = await db.query(
        `SELECT * FROM sitting ${whereClause} ORDER BY created_at DESC LIMIT $${paramIndex++} OFFSET $${paramIndex++}`,
        [...params, pageSize, offset]
      );

      const sittings = dataResult.rows.map(formatSitting);

      // Attach records to each sitting in one batched query. The Registry cards
      // summarise record counts, total duration, and languages, so a sitting
      // returned without its records renders as "0 records / 0m total" even
      // when records exist.
      if (sittings.length > 0) {
        const sittingIds = sittings.map((s) => s.id);
        const recordsResult = await db.query(
          "SELECT * FROM hansard_record WHERE sitting_id = ANY($1) ORDER BY created_at ASC",
          [sittingIds]
        );

        const recordsBySitting = new Map(sittingIds.map((id) => [String(id), []]));
        for (const row of recordsResult.rows) {
          const bucket = recordsBySitting.get(String(row.sitting_id));
          if (bucket) bucket.push(formatRecord(row));
        }

        for (const sitting of sittings) {
          sitting.records = recordsBySitting.get(String(sitting.id)) || [];
        }
      }

      res.json({
        data: sittings,
        total,
        page,
        pageSize,
      });
    } catch (err) {
      console.error("GET /api/sittings error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve sittings",
        },
      });
    }
  });

  /**
   * POST /api/sittings
   *
   * Creates a new sitting and returns the created object with a server-generated ID.
   */
  router.post("/api/sittings", requireSession, requirePermission("create_sitting"), express.json(), async (req, res) => {
    try {
      const {
        title,
        description,
        sessionType,
        committee,
        presidingOfficer,
        parliament,
        dateFrom,
        dateTo,
        priority,
        participants,
        topic,
        orderPaperRef,
      } = req.body;

      // Validate required fields
      if (!title || !sessionType || !presidingOfficer || !dateFrom || !dateTo) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_REQUIRED_FIELDS",
            message: "title, sessionType, presidingOfficer, dateFrom, and dateTo are required",
          },
        });
      }

      const result = await db.query(
        `INSERT INTO sitting (title, description, session_type, committee, presiding_officer, parliament, date_from, date_to, priority, participants, topic, order_paper_ref)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
         RETURNING *`,
        [
          title,
          description || null,
          sessionType,
          committee || null,
          presidingOfficer,
          parliament || null,
          dateFrom,
          dateTo,
          priority || "Medium",
          participants || 0,
          topic || null,
          orderPaperRef || null,
        ]
      );

      res.status(201).json(formatSitting(result.rows[0]));
    } catch (err) {
      console.error("POST /api/sittings error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to create sitting",
        },
      });
    }
  });

  /**
   * GET /api/sittings/:id
   *
   * Returns a single sitting with its associated hansard records.
   */
  router.get("/api/sittings/:id", requireSession, requirePermission("view_records"), async (req, res) => {
    try {
      const { id } = req.params;

      const sittingResult = await db.query(
        "SELECT * FROM sitting WHERE id = $1",
        [id]
      );

      if (sittingResult.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SITTING_NOT_FOUND",
            message: `Sitting with id ${id} not found`,
          },
        });
      }

      const recordsResult = await db.query(
        "SELECT * FROM hansard_record WHERE sitting_id = $1 ORDER BY created_at ASC",
        [id]
      );

      const sitting = formatSitting(sittingResult.rows[0]);
      sitting.records = recordsResult.rows.map(formatRecord);

      res.json(sitting);
    } catch (err) {
      console.error("GET /api/sittings/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve sitting",
        },
      });
    }
  });

  /**
   * PATCH /api/sittings/:id
   *
   * Partial update of mutable sitting fields.
   * Allowed fields: title, description, status, priority, presidingOfficer
   */
  router.patch("/api/sittings/:id", requireSession, requirePermission("create_sitting"), express.json(), async (req, res) => {
    try {
      const { id } = req.params;
      const { title, description, status, priority, presidingOfficer } = req.body;

      // Build SET clause dynamically from provided fields
      const updates = [];
      const params = [];
      let paramIndex = 1;

      if (title !== undefined) {
        updates.push(`title = $${paramIndex++}`);
        params.push(title);
      }
      if (description !== undefined) {
        updates.push(`description = $${paramIndex++}`);
        params.push(description);
      }
      if (status !== undefined) {
        updates.push(`status = $${paramIndex++}`);
        params.push(status);
      }
      if (priority !== undefined) {
        updates.push(`priority = $${paramIndex++}`);
        params.push(priority);
      }
      if (presidingOfficer !== undefined) {
        updates.push(`presiding_officer = $${paramIndex++}`);
        params.push(presidingOfficer);
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
      updates.push(`updated_at = now()`);

      const result = await db.query(
        `UPDATE sitting SET ${updates.join(", ")} WHERE id = $${paramIndex} RETURNING *`,
        [...params, id]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SITTING_NOT_FOUND",
            message: `Sitting with id ${id} not found`,
          },
        });
      }

      res.json(formatSitting(result.rows[0]));
    } catch (err) {
      console.error("PATCH /api/sittings/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to update sitting",
        },
      });
    }
  });

  /**
   * DELETE /api/sittings/:id
   *
   * Soft-deletes a sitting by setting its status to Archived.
   * Returns 204 No Content on success.
   */
  router.delete("/api/sittings/:id", requireSession, requirePermission("create_sitting"), express.json(), async (req, res) => {
    try {
      const { id } = req.params;

      const result = await db.query(
        "UPDATE sitting SET status = 'Archived', updated_at = now() WHERE id = $1 RETURNING id",
        [id]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({
          error: {
            type: "NotFoundError",
            code: "SITTING_NOT_FOUND",
            message: `Sitting with id ${id} not found`,
          },
        });
      }

      res.status(204).send();
    } catch (err) {
      console.error("DELETE /api/sittings/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to delete sitting",
        },
      });
    }
  });

  return router;
};
