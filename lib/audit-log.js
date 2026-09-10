/**
 * Audit Log — the write path for the user-facing audit trail.
 *
 * Every route that mutates something calls `recordAuditEvent` to append a row to
 * `audit_log`. Rows flagged `notifiable` additionally feed the notification bell.
 *
 * Deliberately separate from `lib/audit-logger.js`. That module emits structured
 * *authentication security* events to stdout for log aggregators and stores
 * nothing; this module persists *editorial activity* to the database for the UI
 * to render. The two share only the IP redaction rule, which is imported from
 * there rather than reimplemented so the two cannot drift apart.
 *
 * @module lib/audit-log
 */

"use strict";

const { truncateIp } = require("./audit-logger");
const { broadcast } = require("./ws-server");

/**
 * Categories accepted by the `audit_log_category_check` constraint.
 * @type {readonly string[]}
 */
const AUDIT_CATEGORIES = Object.freeze([
  "record",
  "sitting",
  "template",
  "user",
  "auth",
  "system",
  "export",
]);

/**
 * Severities accepted by the `audit_log_severity_check` constraint.
 * @type {readonly string[]}
 */
const AUDIT_SEVERITIES = Object.freeze(["info", "warning", "critical"]);

/** Fallbacks used when a caller supplies a value outside the allowlists. */
const DEFAULT_CATEGORY = "system";
const DEFAULT_SEVERITY = "info";

/** Actor name recorded for events with no authenticated actor. */
const SYSTEM_ACTOR_NAME = "System";

/**
 * Metadata keys that must never reach the database, matched case-insensitively
 * against the key name. `metadata` is free-form jsonb, so without a denylist a
 * caller could spread a request body straight into it and persist a password or
 * a bearer token into a table Supervisors can read and export to CSV.
 *
 * Substring matching on purpose: it catches `accessToken`, `refresh_token`,
 * `passwordHash`, `x-authorization`, and so on from one entry each.
 */
const SENSITIVE_KEY_PATTERNS = Object.freeze([
  "password",
  "passwd",
  "secret",
  "token",
  "authorization",
  "auth_header",
  "cookie",
  "session",
  "credential",
  "apikey",
  "api_key",
  "privatekey",
  "private_key",
  "hash",
  "jwt",
  "bearer",
]);

/** Depth cap for the metadata scrub, so an absurdly nested object cannot stall the write. */
const MAX_METADATA_DEPTH = 4;

/**
 * Reduces an IP address to a coarse network prefix: IPv4 to its /24, IPv6 to
 * its /48. Never returns a full address.
 *
 * Any already-redacted value is accepted and re-normalised — the trailing mask
 * is stripped before truncation — so this is safe to apply to a value that has
 * already been through it, and callers cannot smuggle a full address past it by
 * appending "/24" themselves.
 *
 * @param {string|null|undefined} ip - Raw or already-redacted address
 * @returns {string|null} The prefix, or null when the input is unusable
 */
function redactIpPrefix(ip) {
  if (ip === null || ip === undefined) return null;
  if (typeof ip !== "string") return null;

  const withoutMask = ip.trim().replace(/\/\d{1,3}$/, "");
  if (!withoutMask) return null;

  // truncateIp is the single implementation of the /24 + /48 rule (lib/audit-logger.js).
  const prefix = truncateIp(withoutMask);

  // The column is nullable, so an unparseable address is recorded as absent
  // rather than as the literal string "unknown".
  return prefix === "unknown" ? null : prefix;
}

/**
 * Whether a metadata key names something that must not be persisted.
 * @param {string} key
 * @returns {boolean}
 */
function isSensitiveKey(key) {
  const normalised = String(key).toLowerCase().replace(/[-\s]/g, "_");
  return SENSITIVE_KEY_PATTERNS.some((pattern) => normalised.includes(pattern));
}

/**
 * Removes denylisted keys from a metadata object, recursively.
 *
 * Always returns a plain object: a non-object metadata value is wrapped rather
 * than stored raw, because the column is jsonb and the UI reads it as a map.
 *
 * @param {*} metadata - Caller-supplied metadata
 * @param {number} [depth=0] - Current recursion depth
 * @returns {Object} Scrubbed metadata safe to persist
 */
function sanitizeMetadata(metadata, depth = 0) {
  if (metadata === null || metadata === undefined) return {};
  if (typeof metadata !== "object") return { value: metadata };
  if (depth >= MAX_METADATA_DEPTH) return {};

  if (Array.isArray(metadata)) {
    return {
      items: metadata.map((item) =>
        item !== null && typeof item === "object" ? sanitizeMetadata(item, depth + 1) : item
      ),
    };
  }

  const clean = {};
  for (const [key, value] of Object.entries(metadata)) {
    if (isSensitiveKey(key)) continue;
    clean[key] =
      value !== null && typeof value === "object" ? sanitizeMetadata(value, depth + 1) : value;
  }
  return clean;
}

/**
 * Resolves the actor columns from a `req.user` object.
 *
 * Mapped defensively: `req.user` is built in three places (the Cognito
 * middleware, the legacy JWT branch in server.js, and test doubles) and not all
 * of them carry every field. Anything missing degrades to the System actor
 * rather than throwing.
 *
 * `email` is only a display fallback for the name — the audit trail is shown to
 * roles that already see the user directory, so it exposes nothing new. It is
 * never used as the identity key, because an email can be reassigned.
 *
 * @param {Object|null|undefined} actor - The `req.user` object, or null
 * @returns {{actorId: string|null, actorName: string, actorRole: string|null}}
 */
function resolveActor(actor) {
  if (!actor || typeof actor !== "object") {
    return { actorId: null, actorName: SYSTEM_ACTOR_NAME, actorRole: null };
  }

  const rawId = actor.userId || actor.sub || actor.id || null;
  const rawName = actor.name || actor.email || null;
  const rawRole = actor.role || null;

  return {
    actorId: rawId ? String(rawId) : null,
    actorName: rawName ? String(rawName) : SYSTEM_ACTOR_NAME,
    actorRole: rawRole ? String(rawRole) : null,
  };
}

/**
 * Converts a snake_case DB row to a camelCase audit event for the API response.
 *
 * `ip_prefix` is deliberately NOT exposed. It is retained in the table for
 * forensics — correlating a suspicious edit with a coarse network — but no UI
 * surface needs it, and leaving it out of the mapper means it cannot leak
 * through the listing, the facets, or the CSV export by accident.
 *
 * @param {Object} row - Database row
 * @returns {Object} camelCase audit event
 */
function formatAuditEvent(row) {
  return {
    id: row.id,
    actorId: row.actor_id,
    actorName: row.actor_name,
    actorRole: row.actor_role,
    action: row.action,
    category: row.category,
    entityType: row.entity_type,
    entityId: row.entity_id,
    entityLabel: row.entity_label,
    sittingId: row.sitting_id,
    sittingTitle: row.sitting_title,
    summary: row.summary,
    metadata: row.metadata,
    severity: row.severity,
    notifiable: row.notifiable,
    createdAt: row.created_at,
  };
}

/**
 * Appends an event to the audit trail and broadcasts it to connected clients.
 *
 * ## This function never throws and never rejects.
 *
 * Auditing is observational: it records that something happened, it does not
 * decide whether it may happen. If the insert fails — constraint violation,
 * connection drop, malformed metadata — the user's action has already
 * succeeded, and surfacing that failure would turn a completed edit into a 500
 * and invite the caller to retry a write that already landed. So every failure
 * path is caught, logged with the action for traceability, and resolved
 * normally. Callers may safely `await` this or fire it and forget it.
 *
 * For the same reason `category` and `severity` are coerced to safe defaults
 * instead of being rejected: an out-of-allowlist value would be refused by the
 * CHECK constraint and lose the event entirely. A degraded record under
 * 'system' / 'info' is strictly better than no record at all.
 *
 * @param {Object} db - Database client with query(text, params)
 * @param {Object} event - The event to record
 * @param {Object|null} [event.actor] - The `req.user` object, or null for system events
 * @param {string} event.action - Machine-readable action key (e.g. "record.edited")
 * @param {string} event.category - One of AUDIT_CATEGORIES
 * @param {string} [event.entityType] - Entity kind (e.g. "record")
 * @param {string|number} [event.entityId] - Entity identifier
 * @param {string} [event.entityLabel] - Human-readable entity name
 * @param {string|number} [event.sittingId] - Owning sitting, when applicable
 * @param {string} [event.sittingTitle] - Owning sitting's title
 * @param {string} event.summary - One-line human-readable description
 * @param {Object} [event.metadata] - Extra structured context (scrubbed before storage)
 * @param {string} [event.severity] - One of AUDIT_SEVERITIES
 * @param {boolean} [event.notifiable] - Whether this event feeds the notification bell
 * @param {string} [event.ipPrefix] - Client address; redacted to a /24 or /48 prefix
 * @returns {Promise<Object|null>} The formatted event, or null when the write failed
 */
async function recordAuditEvent(db, event) {
  const safeEvent = event && typeof event === "object" ? event : {};
  const action = safeEvent.action ? String(safeEvent.action) : "unknown";

  try {
    const { actorId, actorName, actorRole } = resolveActor(safeEvent.actor);

    const category = AUDIT_CATEGORIES.includes(safeEvent.category)
      ? safeEvent.category
      : DEFAULT_CATEGORY;
    const severity = AUDIT_SEVERITIES.includes(safeEvent.severity)
      ? safeEvent.severity
      : DEFAULT_SEVERITY;

    const result = await db.query(
      `INSERT INTO audit_log (
         actor_id, actor_name, actor_role, action, category,
         entity_type, entity_id, entity_label, sitting_id, sitting_title,
         summary, metadata, severity, notifiable, ip_prefix
       )
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, $14, $15)
       RETURNING *`,
      [
        actorId,
        actorName,
        actorRole,
        action,
        category,
        nullableText(safeEvent.entityType),
        nullableText(safeEvent.entityId),
        nullableText(safeEvent.entityLabel),
        safeEvent.sittingId === undefined ? null : safeEvent.sittingId,
        nullableText(safeEvent.sittingTitle),
        safeEvent.summary === undefined || safeEvent.summary === null
          ? ""
          : String(safeEvent.summary),
        JSON.stringify(sanitizeMetadata(safeEvent.metadata)),
        severity,
        safeEvent.notifiable === true,
        redactIpPrefix(safeEvent.ipPrefix),
      ]
    );

    if (!result || !Array.isArray(result.rows) || result.rows.length === 0) return null;

    const formatted = formatAuditEvent(result.rows[0]);

    broadcast("audit:created", formatted);
    if (formatted.notifiable) {
      broadcast("notification:created", formatted);
    }

    return formatted;
  } catch (err) {
    // Swallowed on purpose — see the contract above. The action is included so
    // a dropped event can still be traced back to the operation that raised it.
    console.error(`[audit] failed to record event: action=${action}`, err);
    return null;
  }
}

/**
 * Coerces an optional value to a text column value, mapping absent to NULL.
 * @param {*} value
 * @returns {string|null}
 */
function nullableText(value) {
  return value === undefined || value === null ? null : String(value);
}

module.exports = {
  recordAuditEvent,
  formatAuditEvent,
  redactIpPrefix,
  sanitizeMetadata,
  AUDIT_CATEGORIES,
  AUDIT_SEVERITIES,
};
