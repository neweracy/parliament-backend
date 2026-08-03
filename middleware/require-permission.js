'use strict';

const { getPermissions, reloadIfStale } = require('../lib/rbac-config');

/**
 * RBAC enforcement middleware factory.
 *
 * Returns Express middleware that checks whether the authenticated user
 * (attached to `req.user` by cognitoAuth or requireSession) has the
 * specified operation permission.
 *
 * @param {string} operation - Permission identifier (e.g., 'system_config', 'manage_users')
 * @returns {Function} Express middleware (req, res, next)
 */
function requirePermission(operation) {
  return async function requirePermissionMiddleware(req, res, next) {
    // Refresh RBAC cache if stale (non-blocking on failure)
    try {
      await reloadIfStale();
    } catch (_err) {
      // Cache staleness check failed — proceed with existing cache
    }

    const user = req.user;
    if (!user) {
      return res.status(403).json({
        error: {
          type: 'AuthorizationError',
          code: 'INSUFFICIENT_PERMISSIONS',
          message: `Unauthenticated request does not have the '${operation}' permission required for this operation`,
        },
      });
    }

    // Check permissions from the user object (set by auth middleware)
    const userPermissions = user.permissions || [];
    if (userPermissions.includes(operation)) {
      return next();
    }

    // Also check the RBAC config in case permissions were updated
    const rolePermissions = getPermissions(user.role);
    if (rolePermissions.includes(operation)) {
      return next();
    }

    return res.status(403).json({
      error: {
        type: 'AuthorizationError',
        code: 'INSUFFICIENT_PERMISSIONS',
        message: `Role '${user.role}' does not have the '${operation}' permission required for this operation`,
      },
    });
  };
}

module.exports = requirePermission;
