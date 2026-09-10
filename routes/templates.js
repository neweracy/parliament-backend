/**
 * Templates Routes
 *
 * Express router for Hansard document template CRUD endpoints.
 * Mounts at /api/templates
 *
 * @module routes/templates
 */

"use strict";

const express = require("express");

const requirePermission = require("../middleware/require-permission");
const { broadcast } = require("../lib/ws-server");

/** Template types accepted by the `template_type_check` constraint. */
const VALID_TYPES = ["Plenary", "Committee", "Special", "Custom"];

/** Icon keys accepted by the `template_icon_key_check` constraint. */
const VALID_ICON_KEYS = ["book-open", "users", "gavel", "file-text"];

/** Default speaker attribution format applied when the caller omits one. */
const DEFAULT_SPEAKER_FORMAT = "{name} ({constituency}):";

/**
 * Converts a snake_case DB row to a camelCase template object for the API response.
 * @param {Object} row - Database row
 * @returns {Object} camelCase template object
 */
function formatTemplate(row) {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    type: row.type,
    iconKey: row.icon_key,
    isDefault: row.is_default,
    usageCount: row.usage_count,
    headerLines: row.header_lines,
    sections: row.sections,
    speakerFormat: row.speaker_format,
    showTimestamps: row.show_timestamps,
    showConstituency: row.show_constituency,
    paragraphNumbering: row.paragraph_numbering,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

/**
 * Validates the optional type / iconKey / jsonb-array fields shared by POST and PATCH.
 *
 * Only fields that are present (`!== undefined`) are checked, so the same
 * helper serves full creates and partial updates.
 *
 * @param {Object} body - Request body
 * @returns {{code: string, message: string}|null} The first violation, or null when valid
 */
function validateTemplateFields(body) {
  const { type, iconKey, headerLines, sections } = body;

  if (type !== undefined && !VALID_TYPES.includes(type)) {
    return {
      code: "INVALID_TYPE",
      message: `type must be one of ${VALID_TYPES.join(", ")}`,
    };
  }

  if (iconKey !== undefined && !VALID_ICON_KEYS.includes(iconKey)) {
    return {
      code: "INVALID_ICON_KEY",
      message: `iconKey must be one of ${VALID_ICON_KEYS.join(", ")}`,
    };
  }

  const headerLinesInvalid = headerLines !== undefined && !Array.isArray(headerLines);
  const sectionsInvalid = sections !== undefined && !Array.isArray(sections);
  if (headerLinesInvalid || sectionsInvalid) {
    return {
      code: "INVALID_PAYLOAD",
      message: "headerLines and sections must be arrays",
    };
  }

  return null;
}

/**
 * Builds the 404 envelope used by every by-id route.
 * @param {string|number} id - The requested template id
 * @returns {Object} Error response body
 */
function notFoundError(id) {
  return {
    error: {
      type: "NotFoundError",
      code: "TEMPLATE_NOT_FOUND",
      message: `Template with id ${id} not found`,
    },
  };
}

/**
 * Creates the Templates router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function templatesRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * GET /api/templates
   *
   * Returns every template, default first, then most-used, then alphabetical.
   * Query params: search (matches name or description), type
   */
  router.get("/api/templates", requireSession, requirePermission("view_records"), async (req, res) => {
    try {
      const { search, type } = req.query;

      const conditions = [];
      const params = [];
      let paramIndex = 1;

      const trimmedSearch = typeof search === "string" ? search.trim() : "";
      if (trimmedSearch) {
        conditions.push(`(name ILIKE $${paramIndex} OR COALESCE(description, '') ILIKE $${paramIndex})`);
        params.push(`%${trimmedSearch}%`);
        paramIndex++;
      }

      if (type) {
        conditions.push(`type = $${paramIndex++}`);
        params.push(type);
      }

      const whereClause = conditions.length > 0
        ? `WHERE ${conditions.join(" AND ")}`
        : "";

      const result = await db.query(
        `SELECT * FROM template ${whereClause} ORDER BY is_default DESC, usage_count DESC, name ASC`,
        params
      );

      res.json({
        data: result.rows.map(formatTemplate),
        total: result.rows.length,
      });
    } catch (err) {
      console.error("GET /api/templates error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve templates",
        },
      });
    }
  });

  /**
   * GET /api/templates/:id
   *
   * Returns a single template.
   */
  router.get("/api/templates/:id", requireSession, requirePermission("view_records"), async (req, res) => {
    try {
      const { id } = req.params;

      const result = await db.query("SELECT * FROM template WHERE id = $1", [id]);

      if (result.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      res.json(formatTemplate(result.rows[0]));
    } catch (err) {
      console.error("GET /api/templates/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve template",
        },
      });
    }
  });

  /**
   * POST /api/templates
   *
   * Creates a new template. New templates are never the default — promoting one
   * goes through POST /api/templates/:id/set-default so the single-default
   * invariant stays in one place.
   */
  router.post("/api/templates", requireSession, requirePermission("manage_templates"), express.json(), async (req, res) => {
    try {
      const {
        name,
        description,
        type,
        iconKey,
        headerLines,
        sections,
        speakerFormat,
        showTimestamps,
        showConstituency,
        paragraphNumbering,
      } = req.body;

      if (!name || typeof name !== "string" || !name.trim()) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: "MISSING_REQUIRED_FIELDS",
            message: "name is required",
          },
        });
      }

      const violation = validateTemplateFields(req.body);
      if (violation) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: violation.code,
            message: violation.message,
          },
        });
      }

      const result = await db.query(
        `INSERT INTO template (name, description, type, icon_key, header_lines, sections, speaker_format, show_timestamps, show_constituency, paragraph_numbering)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         RETURNING *`,
        [
          name.trim(),
          description || null,
          type || "Custom",
          iconKey || "file-text",
          JSON.stringify(headerLines || []),
          JSON.stringify(sections || []),
          speakerFormat || DEFAULT_SPEAKER_FORMAT,
          showTimestamps === undefined ? false : showTimestamps,
          showConstituency === undefined ? true : showConstituency,
          paragraphNumbering === undefined ? false : paragraphNumbering,
        ]
      );

      res.status(201).json(formatTemplate(result.rows[0]));

      // Broadcast live update
      broadcast("template:created", formatTemplate(result.rows[0]));
    } catch (err) {
      console.error("POST /api/templates error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to create template",
        },
      });
    }
  });

  /**
   * PATCH /api/templates/:id
   *
   * Partial update of mutable template fields. `is_default` is deliberately not
   * patchable here — use POST /api/templates/:id/set-default.
   */
  router.patch("/api/templates/:id", requireSession, requirePermission("manage_templates"), express.json(), async (req, res) => {
    try {
      const { id } = req.params;
      const {
        name,
        description,
        type,
        iconKey,
        headerLines,
        sections,
        speakerFormat,
        showTimestamps,
        showConstituency,
        paragraphNumbering,
      } = req.body;

      const violation = validateTemplateFields(req.body);
      if (violation) {
        return res.status(400).json({
          error: {
            type: "ValidationError",
            code: violation.code,
            message: violation.message,
          },
        });
      }

      // Build SET clause dynamically from provided fields
      const updates = [];
      const params = [];
      let paramIndex = 1;

      if (name !== undefined) {
        updates.push(`name = $${paramIndex++}`);
        params.push(name);
      }
      if (description !== undefined) {
        updates.push(`description = $${paramIndex++}`);
        params.push(description);
      }
      if (type !== undefined) {
        updates.push(`type = $${paramIndex++}`);
        params.push(type);
      }
      if (iconKey !== undefined) {
        updates.push(`icon_key = $${paramIndex++}`);
        params.push(iconKey);
      }
      if (headerLines !== undefined) {
        updates.push(`header_lines = $${paramIndex++}`);
        params.push(JSON.stringify(headerLines));
      }
      if (sections !== undefined) {
        updates.push(`sections = $${paramIndex++}`);
        params.push(JSON.stringify(sections));
      }
      if (speakerFormat !== undefined) {
        updates.push(`speaker_format = $${paramIndex++}`);
        params.push(speakerFormat);
      }
      if (showTimestamps !== undefined) {
        updates.push(`show_timestamps = $${paramIndex++}`);
        params.push(showTimestamps);
      }
      if (showConstituency !== undefined) {
        updates.push(`show_constituency = $${paramIndex++}`);
        params.push(showConstituency);
      }
      if (paragraphNumbering !== undefined) {
        updates.push(`paragraph_numbering = $${paramIndex++}`);
        params.push(paragraphNumbering);
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
        `UPDATE template SET ${updates.join(", ")} WHERE id = $${paramIndex} RETURNING *`,
        [...params, id]
      );

      if (result.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      res.json(formatTemplate(result.rows[0]));

      // Broadcast live update
      broadcast("template:updated", formatTemplate(result.rows[0]));
    } catch (err) {
      console.error("PATCH /api/templates/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to update template",
        },
      });
    }
  });

  /**
   * DELETE /api/templates/:id
   *
   * Permanently removes a template. The default template is protected: deleting
   * it would leave the system with no default at all, so the caller has to
   * promote another template first.
   * Returns 204 No Content on success.
   */
  router.delete("/api/templates/:id", requireSession, requirePermission("manage_templates"), async (req, res) => {
    try {
      const { id } = req.params;

      const existing = await db.query("SELECT is_default FROM template WHERE id = $1", [id]);

      if (existing.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      if (existing.rows[0].is_default) {
        return res.status(409).json({
          error: {
            type: "ConflictError",
            code: "CANNOT_DELETE_DEFAULT",
            message: "The default template cannot be deleted. Set another template as default first.",
          },
        });
      }

      const result = await db.query("DELETE FROM template WHERE id = $1 RETURNING id", [id]);

      if (result.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      res.status(204).send();

      // Broadcast live update
      broadcast("template:deleted", { id: Number(id) });
    } catch (err) {
      console.error("DELETE /api/templates/:id error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to delete template",
        },
      });
    }
  });

  /**
   * POST /api/templates/:id/duplicate
   *
   * Copies a template under a "<name> (Copy)" name. The copy is never the
   * default and starts with a zeroed usage count.
   *
   * A single INSERT ... SELECT does the read and the write in one statement, so
   * the copy cannot be built from a row that changed or was deleted between two
   * round-trips. No matching source row means the statement inserts nothing,
   * which is how the 404 is detected.
   */
  router.post("/api/templates/:id/duplicate", requireSession, requirePermission("manage_templates"), async (req, res) => {
    try {
      const { id } = req.params;

      const result = await db.query(
        `INSERT INTO template (name, description, type, icon_key, is_default, usage_count, header_lines, sections, speaker_format, show_timestamps, show_constituency, paragraph_numbering)
         SELECT name || $2, description, type, icon_key, false, 0, header_lines, sections, speaker_format, show_timestamps, show_constituency, paragraph_numbering
         FROM template WHERE id = $1
         RETURNING *`,
        [id, " (Copy)"]
      );

      if (result.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      res.status(201).json(formatTemplate(result.rows[0]));

      // Broadcast live update
      broadcast("template:created", formatTemplate(result.rows[0]));
    } catch (err) {
      console.error("POST /api/templates/:id/duplicate error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to duplicate template",
        },
      });
    }
  });

  /**
   * POST /api/templates/:id/set-default
   *
   * Promotes a template to be the default, demoting whichever template held it.
   */
  router.post("/api/templates/:id/set-default", requireSession, requirePermission("manage_templates"), async (req, res) => {
    try {
      const { id } = req.params;

      const existing = await db.query("SELECT id FROM template WHERE id = $1", [id]);

      if (existing.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      // Order matters: clear the incumbent default BEFORE setting the new one.
      // `ux_template_single_default` is a partial unique index keyed on the
      // constant expression ((true)) WHERE is_default, so any two rows with
      // is_default = true collide. Setting first and clearing second would
      // violate the index at the intermediate state.
      //
      // `db` exposes only query() — there is no transaction helper here — so the
      // ordering, not a transaction, is what guarantees the invariant holds at
      // every intermediate state. Clear-then-set can transiently leave zero
      // defaults (recoverable, and never rejected by the index); set-then-clear
      // would be rejected outright.
      await db.query(
        "UPDATE template SET is_default = false, updated_at = now() WHERE is_default AND id <> $1",
        [id]
      );

      const result = await db.query(
        "UPDATE template SET is_default = true, updated_at = now() WHERE id = $1 RETURNING *",
        [id]
      );

      if (result.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      res.json(formatTemplate(result.rows[0]));

      // Broadcast live update
      broadcast("template:updated", formatTemplate(result.rows[0]));
    } catch (err) {
      console.error("POST /api/templates/:id/set-default error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to set default template",
        },
      });
    }
  });

  /**
   * POST /api/templates/:id/use
   *
   * Records that a template was applied to a document by incrementing its usage
   * counter. Requires only view_records — applying a template is ordinary
   * editorial work, not template management.
   */
  router.post("/api/templates/:id/use", requireSession, requirePermission("view_records"), async (req, res) => {
    try {
      const { id } = req.params;

      const result = await db.query(
        "UPDATE template SET usage_count = usage_count + 1, updated_at = now() WHERE id = $1 RETURNING *",
        [id]
      );

      if (result.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      res.json(formatTemplate(result.rows[0]));

      // Broadcast live update
      broadcast("template:updated", formatTemplate(result.rows[0]));
    } catch (err) {
      console.error("POST /api/templates/:id/use error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to record template usage",
        },
      });
    }
  });

  return router;
};
