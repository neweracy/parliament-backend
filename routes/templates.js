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
 * Builds the 409 envelope returned when a create or update would produce a row
 * whose content is indistinguishable from an existing template.
 *
 * @param {Object} existing - The colliding row ({id, name})
 * @returns {Object} Error response body
 */
function duplicateTemplateError(existing) {
  return {
    error: {
      type: "ConflictError",
      code: "DUPLICATE_TEMPLATE",
      message: `An identical template already exists ("${existing.name}"). Change at least one field to save a new template.`,
    },
  };
}

/**
 * Applies the storage defaults to every content field, so the values compared by
 * the duplicate check are exactly the values the row will end up holding.
 *
 * Single source of truth for those defaults: both the duplicate check and the
 * INSERT read from this, so the two cannot drift apart.
 *
 * @param {Object} body - Request body
 * @returns {Object} Resolved content values
 */
function resolveTemplateFields(body) {
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
  } = body;

  return {
    name: typeof name === "string" ? name.trim() : name,
    description: description || null,
    type: type || "Custom",
    iconKey: iconKey || "file-text",
    headerLines: headerLines || [],
    sections: sections || [],
    speakerFormat: speakerFormat || DEFAULT_SPEAKER_FORMAT,
    showTimestamps: showTimestamps === undefined ? false : showTimestamps,
    showConstituency: showConstituency === undefined ? true : showConstituency,
    paragraphNumbering: paragraphNumbering === undefined ? false : paragraphNumbering,
  };
}

/**
 * Finds an existing template whose every content attribute matches the given
 * values. Server-owned fields (is_default, usage_count, timestamps) are
 * excluded, so two templates differing only in default status or usage are
 * considered identical.
 *
 * @param {Object} db
 * @param {Object} fields - Resolved content values (post-defaulting, post-trim)
 * @param {string|number|null} excludeId - Row to ignore, for PATCH self-comparison
 * @returns {Promise<Object|null>} The colliding row, or null
 */
async function findIdenticalTemplate(db, fields, excludeId = null) {
  const conditions = [
    "name = $1",
    // description is nullable and NULL = NULL is never true, so a plain
    // equality test would let two description-less duplicates through.
    // COALESCE folds NULL and '' together on both sides.
    "COALESCE(description, '') = COALESCE($2, '')",
    "type = $3",
    "icon_key = $4",
    // Cast to jsonb so the comparison is semantic: the same members in a
    // different key order are equal, which a text comparison would miss.
    "header_lines = $5::jsonb",
    "sections = $6::jsonb",
    "speaker_format = $7",
    "show_timestamps = $8",
    "show_constituency = $9",
    "paragraph_numbering = $10",
  ];

  const params = [
    // Compare the trimmed name, matching what the INSERT stores.
    typeof fields.name === "string" ? fields.name.trim() : fields.name,
    fields.description === undefined ? null : fields.description,
    fields.type,
    fields.iconKey,
    JSON.stringify(fields.headerLines),
    JSON.stringify(fields.sections),
    fields.speakerFormat,
    fields.showTimestamps,
    fields.showConstituency,
    fields.paragraphNumbering,
  ];

  if (excludeId !== null && excludeId !== undefined) {
    params.push(excludeId);
    conditions.push(`id <> $${params.length}`);
  }

  const result = await db.query(
    `SELECT id, name FROM template WHERE ${conditions.join(" AND ")} LIMIT 1`,
    params
  );

  return result.rows.length > 0 ? result.rows[0] : null;
}

/**
 * Escapes the LIKE metacharacters in a literal so it can be embedded in a
 * pattern. Without this a template named "Report_1" would also match
 * "ReportX1 (Copy 2)", because `_` is a single-character wildcard.
 *
 * Pairs with an `ESCAPE '\'` clause on the LIKE.
 *
 * @param {string} value
 * @returns {string} The literal with \, % and _ backslash-escaped
 */
function escapeLikeLiteral(value) {
  return String(value).replace(/[\\%_]/g, "\\$&");
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
   *
   * A payload whose resolved content matches an existing template on all ten
   * content attributes is rejected with 409 DUPLICATE_TEMPLATE; changing any one
   * of them (a different name is enough) makes it distinct.
   */
  router.post("/api/templates", requireSession, requirePermission("manage_templates"), express.json(), async (req, res) => {
    try {
      const { name } = req.body;

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

      // Resolve the values the row would actually get BEFORE looking for a
      // duplicate, so an omitted field is compared against the default it will
      // be stored with rather than against nothing.
      const fields = resolveTemplateFields(req.body);

      const existing = await findIdenticalTemplate(db, fields);
      if (existing) {
        return res.status(409).json(duplicateTemplateError(existing));
      }

      const result = await db.query(
        `INSERT INTO template (name, description, type, icon_key, header_lines, sections, speaker_format, show_timestamps, show_constituency, paragraph_numbering)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         RETURNING *`,
        [
          fields.name,
          fields.description,
          fields.type,
          fields.iconKey,
          JSON.stringify(fields.headerLines),
          JSON.stringify(fields.sections),
          fields.speakerFormat,
          fields.showTimestamps,
          fields.showConstituency,
          fields.paragraphNumbering,
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
   *
   * The patch is merged over the current row and rejected with 409
   * DUPLICATE_TEMPLATE when the result would be identical to another template.
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

      // Read the current row up front: the duplicate check needs the fields the
      // patch does not carry, and it also yields the 404 before any write.
      const current = await db.query("SELECT * FROM template WHERE id = $1", [id]);

      if (current.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      const row = current.rows[0];
      const merged = {
        name: name === undefined ? row.name : name,
        description: description === undefined ? row.description : description,
        type: type === undefined ? row.type : type,
        iconKey: iconKey === undefined ? row.icon_key : iconKey,
        headerLines: headerLines === undefined ? row.header_lines : headerLines,
        sections: sections === undefined ? row.sections : sections,
        speakerFormat: speakerFormat === undefined ? row.speaker_format : speakerFormat,
        showTimestamps: showTimestamps === undefined ? row.show_timestamps : showTimestamps,
        showConstituency: showConstituency === undefined ? row.show_constituency : showConstituency,
        paragraphNumbering:
          paragraphNumbering === undefined ? row.paragraph_numbering : paragraphNumbering,
      };

      // Without this the no-duplicates rule is trivially bypassable: create with
      // one field different, then patch that field back. The row being patched is
      // excluded, so a no-op patch never collides with itself.
      const duplicate = await findIdenticalTemplate(db, merged, id);
      if (duplicate) {
        return res.status(409).json(duplicateTemplateError(duplicate));
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
   * Copies a template under the first free name in the sequence "<name> (Copy)",
   * "<name> (Copy 2)", "<name> (Copy 3)", … The copy is never the default and
   * starts with a zeroed usage count.
   *
   * Deliberately no longer a single INSERT ... SELECT: the new name has to be
   * computed against the names already taken, which needs a read before the
   * write. A fixed "<name> (Copy)" would be rejected by the identical-content
   * check the second time the same source is duplicated. The trade-off is the
   * loss of the previous single-statement atomicity — the source row could change
   * or a competing name could be claimed between the two round-trips — accepted
   * here in exchange for duplicate always succeeding.
   */
  router.post("/api/templates/:id/duplicate", requireSession, requirePermission("manage_templates"), async (req, res) => {
    try {
      const { id } = req.params;

      const source = await db.query("SELECT * FROM template WHERE id = $1", [id]);

      if (source.rows.length === 0) {
        return res.status(404).json(notFoundError(id));
      }

      const row = source.rows[0];

      // One query for every name already in the sequence, rather than a probe
      // per candidate. ESCAPE pairs with escapeLikeLiteral so % and _ in the
      // source name stay literal.
      const firstCandidate = `${row.name} (Copy)`;
      const taken = await db.query(
        `SELECT name FROM template WHERE name = $1 OR name LIKE $2 ESCAPE '\\'`,
        [firstCandidate, `${escapeLikeLiteral(row.name)} (Copy %)`]
      );

      const takenNames = new Set(taken.rows.map((r) => r.name));
      let copyName = firstCandidate;
      for (let suffix = 2; takenNames.has(copyName); suffix++) {
        copyName = `${row.name} (Copy ${suffix})`;
      }

      const result = await db.query(
        `INSERT INTO template (name, description, type, icon_key, is_default, usage_count, header_lines, sections, speaker_format, show_timestamps, show_constituency, paragraph_numbering)
         VALUES ($1, $2, $3, $4, false, 0, $5::jsonb, $6::jsonb, $7, $8, $9, $10)
         RETURNING *`,
        [
          copyName,
          row.description,
          row.type,
          row.icon_key,
          JSON.stringify(row.header_lines),
          JSON.stringify(row.sections),
          row.speaker_format,
          row.show_timestamps,
          row.show_constituency,
          row.paragraph_numbering,
        ]
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
