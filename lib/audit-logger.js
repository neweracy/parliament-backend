"use strict";

/**
 * Audit Logger — Structured, redacted authentication security events.
 *
 * Records exactly one logical audit event per Terminal_Outcome. Events never
 * contain passwords, password hashes, raw email addresses, full IP addresses,
 * full user-agent strings, JWTs, authorization headers, cookies, or request
 * bodies. Source metadata is coarse (IPv4 /24 prefix, IPv6 /48 prefix, or a
 * keyed digest). An idempotent event ID prevents duplicates on retry.
 *
 * @module lib/audit-logger
 */

const crypto = require("crypto");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Allowed Audit_Reason enumeration values. */
const VALID_REASONS = Object.freeze([
  "success",
  "invalid_credentials",
  "inactive",
  "missing_hash",
  "rate_limited",
  "validation",
  "dependency_error",
  "timeout",
]);

const VALID_REASONS_SET = new Set(VALID_REASONS);

/** Allowed audit event categories. */
const VALID_EVENTS = Object.freeze([
  "login_success",
  "login_failure",
  "login_rate_limited",
]);

const VALID_EVENTS_SET = new Set(VALID_EVENTS);

/** Pattern for a valid Canonical_Request_ID: 16–64 ASCII alphanumeric, hyphen, underscore. */
const REQUEST_ID_PATTERN = /^[a-zA-Z0-9_-]{16,64}$/;

// ---------------------------------------------------------------------------
// IP Truncation
// ---------------------------------------------------------------------------

/**
 * Truncate an IPv4 address to /24 (remove last octet).
 * Truncate an IPv6 address to /48 (keep first 3 groups).
 *
 * Never returns the full address. Returns 'unknown' for unparseable input.
 *
 * @param {string|undefined|null} ip - Raw IP address
 * @returns {string} Coarse network prefix or 'unknown'
 */
function truncateIp(ip) {
  if (!ip || typeof ip !== "string") {
    return "unknown";
  }

  const trimmed = ip.trim();
  if (!trimmed) {
    return "unknown";
  }

  // Handle IPv4-mapped IPv6 (e.g. ::ffff:192.168.1.100)
  const mappedMatch = trimmed.match(/::ffff:(\d+\.\d+\.\d+)\.\d+$/i);
  if (mappedMatch) {
    return mappedMatch[1] + ".0/24";
  }

  // Plain IPv4: exactly 4 dotted-decimal groups
  const v4Match = trimmed.match(/^(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}$/);
  if (v4Match) {
    return v4Match[1] + ".0/24";
  }

  // IPv6: keep first 3 groups (48 bits)
  if (trimmed.includes(":")) {
    // Expand abbreviated IPv6 for consistent truncation
    const expanded = expandIPv6(trimmed);
    if (expanded) {
      const groups = expanded.split(":");
      return groups.slice(0, 3).join(":") + "::/48";
    }
    // Fallback: simple split for well-formed addresses
    const parts = trimmed.split(":");
    if (parts.length >= 3) {
      return parts.slice(0, 3).join(":") + "::/48";
    }
  }

  return "unknown";
}

/**
 * Expand an abbreviated IPv6 address to full 8-group form.
 * Returns null if the input is not valid IPv6.
 *
 * @param {string} addr
 * @returns {string|null}
 */
function expandIPv6(addr) {
  // Remove zone ID if present
  const zoneIdx = addr.indexOf("%");
  const clean = zoneIdx >= 0 ? addr.slice(0, zoneIdx) : addr;

  // Handle :: expansion
  let parts;
  if (clean.includes("::")) {
    const [left, right] = clean.split("::");
    const leftParts = left ? left.split(":") : [];
    const rightParts = right ? right.split(":") : [];
    const missing = 8 - leftParts.length - rightParts.length;
    if (missing < 0) return null;
    parts = [...leftParts, ...Array(missing).fill("0"), ...rightParts];
  } else {
    parts = clean.split(":");
  }

  if (parts.length !== 8) return null;
  return parts.map((p) => p.padStart(4, "0")).join(":");
}

// ---------------------------------------------------------------------------
// User-Agent Family Extraction
// ---------------------------------------------------------------------------

/**
 * Extract a coarse user-agent family (browser name) without leaking the full
 * UA string. Returns undefined for absent/empty input.
 *
 * @param {string|undefined|null} ua - Raw User-Agent header value
 * @returns {string|undefined} Coarse browser family or undefined
 */
function parseUaFamily(ua) {
  if (!ua || typeof ua !== "string") return undefined;

  // Order matters: Edge includes Chrome, Chrome includes Safari
  if (ua.includes("Edg")) return "Edge";
  if (ua.includes("Firefox")) return "Firefox";
  if (ua.includes("Chrome")) return "Chrome";
  if (ua.includes("Safari")) return "Safari";
  if (ua.includes("curl")) return "curl";
  return "Other";
}

// ---------------------------------------------------------------------------
// Canonical Request ID
// ---------------------------------------------------------------------------

/**
 * Validate an inbound request ID or generate a new one.
 *
 * @param {string|undefined|null} inboundId - The request-supplied ID
 * @returns {string} A valid Canonical_Request_ID (16–64 chars)
 */
function resolveRequestId(inboundId) {
  if (
    inboundId &&
    typeof inboundId === "string" &&
    REQUEST_ID_PATTERN.test(inboundId)
  ) {
    return inboundId;
  }
  // Generate a 32-character hex string (16 random bytes)
  return crypto.randomBytes(16).toString("hex");
}

// ---------------------------------------------------------------------------
// Canonical User ID Validation
// ---------------------------------------------------------------------------

/**
 * Validate a Canonical_User_ID. Must be a non-empty string of 1–128 UTF-8
 * bytes. Invalid identifiers are omitted from audit events.
 *
 * @param {string|undefined|null} userId
 * @returns {string|undefined} Valid user ID or undefined
 */
function validateUserId(userId) {
  if (!userId || typeof userId !== "string") return undefined;
  if (userId.trim().length === 0) return undefined;
  if (Buffer.byteLength(userId, "utf8") > 128) return undefined;
  return userId;
}

// ---------------------------------------------------------------------------
// Idempotent Event ID
// ---------------------------------------------------------------------------

/**
 * Generate a deterministic event ID for idempotency. Given the same request
 * ID, event category, and reason, the same event ID is produced. This
 * prevents duplicate audit records on retry.
 *
 * @param {string} requestId - Canonical_Request_ID
 * @param {string} event - Event category
 * @param {string} reason - Audit_Reason
 * @returns {string} A 32-character hex digest (deterministic)
 */
function generateEventId(requestId, event, reason) {
  return crypto
    .createHash("sha256")
    .update(`${requestId}:${event}:${reason}`)
    .digest("hex")
    .slice(0, 32);
}

// ---------------------------------------------------------------------------
// Audit Event Construction
// ---------------------------------------------------------------------------

/**
 * Create a structured audit event object. Never includes sensitive data.
 *
 * Returns null if the event or reason is not in the approved enumeration.
 *
 * @param {object} params
 * @param {string} params.event - Event category (login_success, login_failure, login_rate_limited)
 * @param {string} params.reason - Audit_Reason enum value
 * @param {string} [params.requestId] - Inbound request ID (validated/generated)
 * @param {string} [params.userId] - Known Canonical_User_ID (validated; omitted if invalid)
 * @param {string} [params.ip] - Raw client IP (truncated to /24 or /48)
 * @param {string} [params.userAgent] - Raw User-Agent (reduced to family)
 * @returns {object|null} The structured audit event, or null if inputs invalid
 */
function createAuditEvent({ event, reason, requestId, userId, ip, userAgent }) {
  // Validate event category
  if (!VALID_EVENTS_SET.has(event)) return null;

  // Validate reason
  if (!VALID_REASONS_SET.has(reason)) return null;

  const resolvedRequestId = resolveRequestId(requestId);

  const auditEvent = {
    eventId: generateEventId(resolvedRequestId, event, reason),
    event,
    timestamp: new Date().toISOString(), // RFC 3339 UTC with trailing Z
    requestId: resolvedRequestId,
    reason,
    source: {
      ipPrefix: truncateIp(ip),
    },
  };

  // Include user-agent family only if parseable
  const family = parseUaFamily(userAgent);
  if (family) {
    auditEvent.source.userAgentFamily = family;
  }

  // Include userId only if valid (1–128 UTF-8 bytes, non-empty)
  const validatedUserId = validateUserId(userId);
  if (validatedUserId) {
    auditEvent.userId = validatedUserId;
  }

  return auditEvent;
}

// ---------------------------------------------------------------------------
// Audit Event Logging
// ---------------------------------------------------------------------------

/**
 * Log a structured audit event. Output goes to structured stdout as JSON.
 *
 * The event is self-describing (prefixed with `_audit: true`) so log
 * aggregators can identify and route audit records separately.
 *
 * If the audit write itself fails, emits a sanitized operational failure
 * signal with the Canonical_Request_ID but no credentials, tokens, hashes,
 * raw addresses, or request bodies.
 *
 * @param {object} eventData - Parameters for createAuditEvent
 * @returns {object|null} The created audit event, or null if invalid
 */
function logAuditEvent(eventData) {
  const auditEvent = createAuditEvent(eventData);
  if (!auditEvent) return null;

  try {
    const output = JSON.stringify({ _audit: true, ...auditEvent });
    console.log(output);
  } catch (_err) {
    // Sanitized operational failure signal with request ID only
    console.error(
      JSON.stringify({
        _audit_failure: true,
        requestId: auditEvent.requestId,
        error: "audit_write_failed",
      })
    );
  }

  return auditEvent;
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  logAuditEvent,
  createAuditEvent,
  truncateIp,
  validateUserId,
  resolveRequestId,
  generateEventId,
  parseUaFamily,
  VALID_REASONS,
  VALID_EVENTS,
  // Exported for testing
  _internals: {
    VALID_REASONS_SET,
    VALID_EVENTS_SET,
    REQUEST_ID_PATTERN,
    expandIPv6,
  },
};
