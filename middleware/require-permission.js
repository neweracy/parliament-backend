'use strict';

const { getPermissions, reloadIfStale } = require('../lib/rbac-config');
const { ROLE_PERMISSIONS } = require('./cognito-auth');

/**
 * The set of roles the system recognizes as valid authenticated identities.
 * An allowlisted role with no ROLE_PERMISSIONS mapping is a configuration
 * anomaly that triggers a monitored signal.
 */
const ALLOWLISTED_ROLES = new Set([
  'Admin',
  'Chief Editor',
  'Supervisor',
  'Editor',
  'Viewer',
]);

/**
 * Resolve permissions for a role from the server-owned mappings.
 * Priority: dynamic rbac-config cache (DB), then static ROLE_PERMISSIONS.
 * Never consults JWT claims, request bodies, query params, or headers.
 *
 * @param {string} role - The verified role
 * @returns {string[]} Permissions array (may be empty if role has no mapping)
 */
function resolveServerPermissions(role) {
  // Try dynamic database-backed cache first
  const dynamicPerms = getPermissions(role);
  if (dynamicPerms.length > 0) {
    return dynamicPerms;
  }
  // Fall back to static server-owned mapping
  return ROLE_PERMISSIONS[role] || [];
}

/**
 * RBAC enforcement middleware factory (hardened).
 *
 * Returns Express middleware that:
 * 1. Returns 401 when no valid authenticated identity exists on req.user.
 * 2. Derives permissions exclusively from the server-owned ROLE_PERMISSIONS
 *    map — never from JWT claims, request bodies, query params, or headers.
 * 3. Returns 403 before handler execution when the operation is not granted.
 * 4. Emits a monitored signal when an allowlisted role has no mapping.
 *
 * @param {string} operation - Permission identifier (e.g., 'system_config', 'manage_users')
 * @returns {Function} Express middleware (req, res, next)
 */
function requirePermission(operation) {
  return async function requirePermissionMiddleware(req, res, next) {
    const user = req.user;

    // 1. No authenticated identity → 401
    if (!user || !user.role) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authentication required',
        },
      });
    }

    // Refresh RBAC cache if stale (non-blocking on failure)
    try {
      await reloadIfStale();
    } catch (_err) {
      // Cache staleness check failed — proceed with existing cache
    }

    // 2. Derive permissions exclusively from server-side ROLE_PERMISSIONS.
    //    Ignore user.permissions (which may have been set from JWT claims),
    //    request bodies, query params, and headers entirely.
    const rolePermissions = resolveServerPermissions(user.role);

    // 3. Emit monitored signal when an allowlisted role has no mapping
    if (ALLOWLISTED_ROLES.has(user.role) && rolePermissions.length === 0) {
      console.error(
        `[rbac] MONITORED: Allowlisted role '${user.role}' has no entry in ROLE_PERMISSIONS map. ` +
        'This indicates an incomplete server configuration.'
      );
    }

    // 4. Check if the operation is granted by the server-side map
    if (rolePermissions.includes(operation)) {
      return next();
    }

    // 5. Deny — 403 without executing handler or any protected side effect
    return res.status(403).json({
      error: {
        type: 'AuthorizationError',
        code: 'INSUFFICIENT_PERMISSIONS',
        message: 'You do not have permission to perform this action',
      },
    });
  };
}

module.exports = requirePermission;
