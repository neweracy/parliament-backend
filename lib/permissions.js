/**
 * Canonical role → permission matrix.
 *
 * THE single source of truth for authorization in the Gateway. Both the login
 * response payload (routes/auth.js) and the enforcement fallback
 * (middleware/cognito-auth.js) import from here, so the permissions a client is
 * told it has can never drift from the permissions the server actually grants.
 *
 * The `rbac_config` database table is authoritative at runtime — see
 * lib/rbac-config.js and middleware/require-permission.js. This module is the
 * fallback used when that table has no row for a role, and it is the reference
 * the table should be seeded from (scripts/sync-rbac-config.sql).
 *
 * ## Hierarchy
 *
 * Permissions are strictly monotonic: every role holds a superset of the role
 * below it.
 *
 *   Viewer (3) ⊂ Editor (8) ⊂ Supervisor (12) ⊂ Chief Editor (17) ⊂ Admin (18)
 *
 * A previous revision violated this — Supervisor held `approve_certification`
 * while Chief Editor did not, meaning a promotion could remove a capability.
 * ROLE_ORDER plus assertMonotonicHierarchy() below make that class of bug a
 * startup failure rather than a silent inversion.
 *
 * @module lib/permissions
 */

"use strict";

// ---------------------------------------------------------------------------
// Permission tiers — each tier adds to the one before it
// ---------------------------------------------------------------------------

/**
 * Read-only access to published material. The floor for any authenticated user.
 */
const VIEWER_PERMISSIONS = [
  "view_records",
  "search_hansard",
  "export_published",
];

/**
 * Transcript production work: uploading audio and editing toward review.
 */
const EDITOR_PERMISSIONS = [
  ...VIEWER_PERMISSIONS,
  "upload_audio",
  "edit_record",
  "rename_speakers",
  "submit_for_review",
  "export_drafts",
];

/**
 * Quality control: reviewing submitted work and approving certification.
 */
const SUPERVISOR_PERMISSIONS = [
  ...EDITOR_PERMISSIONS,
  "review_record",
  "approve_certification",
  "view_audit_trail",
  "export_hansard",
];

/**
 * Editorial management: creating sittings, assigning work, certifying records,
 * managing templates, and administering user accounts.
 */
const CHIEF_EDITOR_PERMISSIONS = [
  ...SUPERVISOR_PERMISSIONS,
  "create_sitting",
  "assign_editor",
  "certify_record",
  "manage_templates",
  "manage_users",
];

/**
 * Full access. `system_config` is deliberately Admin-only: it governs the
 * transcription engine, the custom dictionary, and export configuration, which
 * are system-wide settings rather than editorial decisions.
 */
const ADMIN_PERMISSIONS = [
  ...CHIEF_EDITOR_PERMISSIONS,
  "system_config",
];

// ---------------------------------------------------------------------------
// The matrix
// ---------------------------------------------------------------------------

/**
 * Role → permissions. Frozen so no caller can mutate shared authorization
 * state at runtime.
 *
 * @type {Readonly<Record<string, readonly string[]>>}
 */
const ROLE_PERMISSIONS = Object.freeze({
  Admin: Object.freeze(ADMIN_PERMISSIONS),
  "Chief Editor": Object.freeze(CHIEF_EDITOR_PERMISSIONS),
  Supervisor: Object.freeze(SUPERVISOR_PERMISSIONS),
  Editor: Object.freeze(EDITOR_PERMISSIONS),
  Viewer: Object.freeze(VIEWER_PERMISSIONS),
});

/**
 * Roles ordered least to most privileged. Used to verify the hierarchy and to
 * compare two roles.
 *
 * @type {readonly string[]}
 */
const ROLE_ORDER = Object.freeze([
  "Viewer",
  "Editor",
  "Supervisor",
  "Chief Editor",
  "Admin",
]);

/**
 * Every role the system accepts as a valid authenticated identity.
 *
 * @type {ReadonlySet<string>}
 */
const ALLOWLISTED_ROLES = new Set(ROLE_ORDER);

/**
 * Every permission identifier the system defines. Useful for validating that a
 * requirePermission() call names a real permission rather than a typo, which
 * would otherwise deny everyone silently.
 *
 * @type {ReadonlySet<string>}
 */
const ALL_PERMISSIONS = new Set(ADMIN_PERMISSIONS);

// ---------------------------------------------------------------------------
// Accessors
// ---------------------------------------------------------------------------

/**
 * Returns the permissions for a role.
 *
 * Fails closed: an unrecognized role gets an empty array rather than the Viewer
 * defaults, so a typo in a role name cannot silently grant access.
 *
 * @param {string} role
 * @returns {readonly string[]}
 */
function getPermissionsForRole(role) {
  return ROLE_PERMISSIONS[role] || [];
}

/**
 * Whether a role holds a permission.
 *
 * @param {string} role
 * @param {string} permission
 * @returns {boolean}
 */
function roleHasPermission(role, permission) {
  return getPermissionsForRole(role).includes(permission);
}

/**
 * Whether a string names a real permission.
 *
 * @param {string} permission
 * @returns {boolean}
 */
function isKnownPermission(permission) {
  return ALL_PERMISSIONS.has(permission);
}

// ---------------------------------------------------------------------------
// Hierarchy verification
// ---------------------------------------------------------------------------

/**
 * Asserts that each role's permissions are a superset of the role below it.
 *
 * Called at module load, so a hierarchy inversion introduced by an edit fails
 * fast at startup instead of surfacing later as a role that loses a capability
 * when promoted.
 *
 * @throws {Error} When a lower role holds a permission a higher role does not.
 */
function assertMonotonicHierarchy() {
  for (let i = 1; i < ROLE_ORDER.length; i += 1) {
    const lowerRole = ROLE_ORDER[i - 1];
    const higherRole = ROLE_ORDER[i];

    const higherPermissions = new Set(getPermissionsForRole(higherRole));
    const missing = getPermissionsForRole(lowerRole).filter(
      (permission) => !higherPermissions.has(permission)
    );

    if (missing.length > 0) {
      throw new Error(
        `[permissions] Hierarchy inversion: '${higherRole}' is missing ` +
          `permission(s) held by '${lowerRole}': ${missing.join(", ")}. ` +
          "Each role must be a superset of the role below it."
      );
    }
  }
}

assertMonotonicHierarchy();

module.exports = {
  ROLE_PERMISSIONS,
  ROLE_ORDER,
  ALLOWLISTED_ROLES,
  ALL_PERMISSIONS,
  getPermissionsForRole,
  roleHasPermission,
  isKnownPermission,
  assertMonotonicHierarchy,
};
