/**
 * User Management Routes
 *
 * Express router for user CRUD operations.
 * Interacts with AWS Cognito for identity management and the local
 * `users` table for metadata/caching.
 *
 * Mounts at /api/users
 *
 * @module routes/users
 */

'use strict';

const crypto = require('crypto');
const express = require('express');
const {
  CognitoIdentityProviderClient,
  AdminCreateUserCommand,
  AdminAddUserToGroupCommand,
  AdminRemoveUserFromGroupCommand,
  AdminListGroupsForUserCommand,
  AdminDisableUserCommand,
  AdminEnableUserCommand,
} = require('@aws-sdk/client-cognito-identity-provider');

const requirePermission = require('../middleware/require-permission');

// ─── Constants ───────────────────────────────────────────────────────────────

/** Valid roles in the system (ordered by precedence, highest first). */
const VALID_ROLES = ['Admin', 'Chief Editor', 'Supervisor', 'Editor', 'Viewer'];

/** Maps role names to Cognito group names. */
const ROLE_TO_GROUP = {
  'Admin': 'admin',
  'Chief Editor': 'chief-editor',
  'Supervisor': 'supervisor',
  'Editor': 'editor',
  'Viewer': 'viewer',
};

/** Roles that a Chief Editor is allowed to manage. */
const CHIEF_EDITOR_MANAGEABLE_ROLES = new Set(['Supervisor', 'Editor', 'Viewer']);

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Determines if a Cognito error is a network/service-level failure.
 * @param {Error} err
 * @returns {boolean}
 */
function isCognitoServiceError(err) {
  const name = err.name || '';
  return (
    name === 'ServiceUnavailableException' ||
    name === 'InternalErrorException' ||
    name === 'TooManyRequestsException' ||
    name === 'NetworkError' ||
    err.code === 'ECONNREFUSED' ||
    err.code === 'ETIMEDOUT' ||
    err.code === 'ENOTFOUND' ||
    err.message?.includes('fetch failed')
  );
}

/**
 * Creates the Users router.
 *
 * @param {Function} authMiddleware - JWT/session auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function usersRoutes(authMiddleware, db) {
  const router = express.Router();

  // Initialize Cognito client
  const cognitoRegion = process.env.COGNITO_REGION || 'us-east-1';
  const userPoolId = process.env.COGNITO_USER_POOL_ID;

  const cognito = new CognitoIdentityProviderClient({ region: cognitoRegion });

  // ─── GET /api/users ──────────────────────────────────────────────────────

  /**
   * List users from local users table.
   * Supports search by name, email, or role via ?search query param.
   */
  router.get(
    '/api/users',
    authMiddleware,
    requirePermission('manage_users'),
    async (req, res) => {
      try {
        const { search } = req.query;

        let queryText;
        let params;

        if (search && search.trim()) {
          const searchTerm = `%${search.trim()}%`;
          queryText = `
            SELECT id, email, name, role, status, department, last_active, created_at, updated_at
            FROM users
            WHERE name ILIKE $1 OR email ILIKE $1 OR role ILIKE $1
            ORDER BY created_at DESC
          `;
          params = [searchTerm];
        } else {
          queryText = `
            SELECT id, email, name, role, status, department, last_active, created_at, updated_at
            FROM users
            ORDER BY created_at DESC
          `;
          params = [];
        }

        const result = await db.query(queryText, params);

        const users = result.rows.map((row) => ({
          id: row.id,
          email: row.email,
          name: row.name,
          role: row.role,
          status: row.status,
          department: row.department,
          lastActive: row.last_active,
          createdAt: row.created_at,
          updatedAt: row.updated_at,
        }));

        res.json({ users, total: users.length });
      } catch (err) {
        console.error('GET /api/users error:', err);
        res.status(500).json({
          error: {
            type: 'ServerError',
            code: 'INTERNAL_ERROR',
            message: 'Failed to retrieve users',
          },
        });
      }
    }
  );

  // ─── POST /api/users/invite ──────────────────────────────────────────────

  /**
   * Invite a new user.
   * Creates the user in Cognito User Pool first, then in the local table.
   * Never creates locally if Cognito fails.
   */
  router.post(
    '/api/users/invite',
    authMiddleware,
    requirePermission('manage_users'),
    express.json(),
    async (req, res) => {
      try {
        const { email, name, role, department } = req.body;

        // Validate required fields
        if (!email || !name || !role) {
          return res.status(422).json({
            error: {
              type: 'ValidationError',
              code: 'VALIDATION_ERROR',
              message: 'email, name, and role are required fields',
            },
          });
        }

        // Validate role
        if (!VALID_ROLES.includes(role)) {
          return res.status(422).json({
            error: {
              type: 'ValidationError',
              code: 'VALIDATION_ERROR',
              message: `Invalid role: '${role}'. Allowed values: ${VALID_ROLES.join(', ')}`,
            },
          });
        }

        // Check if user already exists locally
        const existingUser = await db.query(
          'SELECT id FROM users WHERE email = $1',
          [email.toLowerCase()]
        );
        if (existingUser.rows.length > 0) {
          return res.status(409).json({
            error: {
              type: 'ConflictError',
              code: 'USER_ALREADY_EXISTS',
              message: `A user with email '${email}' already exists`,
            },
          });
        }

        // Create user in Cognito first — never apply locally if Cognito fails
        let cognitoSub;
        try {
          const createUserCmd = new AdminCreateUserCommand({
            UserPoolId: userPoolId,
            Username: email,
            UserAttributes: [
              { Name: 'email', Value: email },
              { Name: 'email_verified', Value: 'true' },
              { Name: 'name', Value: name },
              ...(department ? [{ Name: 'custom:department', Value: department }] : []),
            ],
            DesiredDeliveryMediums: ['EMAIL'],
          });

          const createResult = await cognito.send(createUserCmd);
          cognitoSub = createResult.User?.Attributes?.find(
            (attr) => attr.Name === 'sub'
          )?.Value;

          // Add user to the appropriate Cognito group
          const groupName = ROLE_TO_GROUP[role];
          if (groupName) {
            const addToGroupCmd = new AdminAddUserToGroupCommand({
              UserPoolId: userPoolId,
              Username: email,
              GroupName: groupName,
            });
            await cognito.send(addToGroupCmd);
          }
        } catch (cognitoErr) {
          // Handle "user already exists in Cognito"
          if (cognitoErr.name === 'UsernameExistsException') {
            return res.status(409).json({
              error: {
                type: 'ConflictError',
                code: 'USER_ALREADY_EXISTS',
                message: `A user with email '${email}' already exists in the identity provider`,
              },
            });
          }

          // Service-level failure
          if (isCognitoServiceError(cognitoErr)) {
            console.error('Cognito service error during invite:', cognitoErr);
            return res.status(502).json({
              error: {
                type: 'ServerError',
                code: 'COGNITO_UNAVAILABLE',
                message: 'Identity provider is unavailable. Please try again later.',
              },
            });
          }

          // Other unexpected errors
          console.error('Cognito error during invite:', cognitoErr);
          return res.status(502).json({
            error: {
              type: 'ServerError',
              code: 'COGNITO_UNAVAILABLE',
              message: 'Failed to create user in identity provider',
            },
          });
        }

        // Cognito succeeded — create local record
        const userId = cognitoSub || crypto.randomUUID();
        const insertResult = await db.query(
          `INSERT INTO users (id, email, name, role, status, department, created_at, updated_at)
           VALUES ($1, $2, $3, $4, 'Pending', $5, now(), now())
           RETURNING *`,
          [userId, email.toLowerCase(), name, role, department || null]
        );

        const user = insertResult.rows[0];
        res.status(201).json({
          id: user.id,
          email: user.email,
          name: user.name,
          role: user.role,
          status: user.status,
          department: user.department,
          createdAt: user.created_at,
          updatedAt: user.updated_at,
        });
      } catch (err) {
        console.error('POST /api/users/invite error:', err);
        res.status(500).json({
          error: {
            type: 'ServerError',
            code: 'INTERNAL_ERROR',
            message: 'Failed to invite user',
          },
        });
      }
    }
  );

  // ─── PATCH /api/users/:userId/role ───────────────────────────────────────

  /**
   * Change a user's role.
   * Updates Cognito group membership first, then updates the local record.
   * Never applies locally if Cognito fails.
   * Chief Editor can only manage Supervisor/Editor/Viewer.
   */
  router.patch(
    '/api/users/:userId/role',
    authMiddleware,
    requirePermission('manage_users'),
    express.json(),
    async (req, res) => {
      try {
        const { userId } = req.params;
        const { role: newRole } = req.body;

        // Validate new role
        if (!newRole || !VALID_ROLES.includes(newRole)) {
          return res.status(422).json({
            error: {
              type: 'ValidationError',
              code: 'VALIDATION_ERROR',
              message: `Invalid role: '${newRole}'. Allowed values: ${VALID_ROLES.join(', ')}`,
            },
          });
        }

        // Fetch target user from local DB
        const userResult = await db.query(
          'SELECT id, email, role FROM users WHERE id = $1',
          [userId]
        );
        if (userResult.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: 'NotFoundError',
              code: 'USER_NOT_FOUND',
              message: `User with id '${userId}' not found`,
            },
          });
        }

        const targetUser = userResult.rows[0];
        const currentRole = targetUser.role;
        const requestingUserRole = req.user?.role;

        // Role hierarchy enforcement:
        // Chief Editor can only manage users with current role in {Supervisor, Editor, Viewer}
        if (requestingUserRole === 'Chief Editor') {
          // Cannot change a user whose current role is Admin or Chief Editor
          if (!CHIEF_EDITOR_MANAGEABLE_ROLES.has(currentRole)) {
            return res.status(403).json({
              error: {
                type: 'AuthorizationError',
                code: 'INSUFFICIENT_PERMISSIONS',
                message: `Chief Editor cannot change the role of a user with role '${currentRole}'`,
              },
            });
          }
          // Cannot promote to Admin or Chief Editor
          if (!CHIEF_EDITOR_MANAGEABLE_ROLES.has(newRole)) {
            return res.status(403).json({
              error: {
                type: 'AuthorizationError',
                code: 'INSUFFICIENT_PERMISSIONS',
                message: `Chief Editor cannot assign the '${newRole}' role`,
              },
            });
          }
        }

        // If role is unchanged, just return success
        if (currentRole === newRole) {
          return res.json({
            id: targetUser.id,
            email: targetUser.email,
            role: currentRole,
            message: 'Role unchanged',
          });
        }

        // Update Cognito groups — remove from old group, add to new group
        const username = targetUser.email;
        try {
          // Get current Cognito groups for the user
          const listGroupsCmd = new AdminListGroupsForUserCommand({
            UserPoolId: userPoolId,
            Username: username,
          });
          const groupsResult = await cognito.send(listGroupsCmd);
          const currentGroups = (groupsResult.Groups || []).map((g) => g.GroupName);

          // Remove from all existing role groups
          for (const group of currentGroups) {
            const removeCmd = new AdminRemoveUserFromGroupCommand({
              UserPoolId: userPoolId,
              Username: username,
              GroupName: group,
            });
            await cognito.send(removeCmd);
          }

          // Add to new role group
          const newGroupName = ROLE_TO_GROUP[newRole];
          if (newGroupName) {
            const addCmd = new AdminAddUserToGroupCommand({
              UserPoolId: userPoolId,
              Username: username,
              GroupName: newGroupName,
            });
            await cognito.send(addCmd);
          }
        } catch (cognitoErr) {
          if (isCognitoServiceError(cognitoErr)) {
            console.error('Cognito service error during role change:', cognitoErr);
            return res.status(502).json({
              error: {
                type: 'ServerError',
                code: 'COGNITO_UNAVAILABLE',
                message: 'Identity provider is unavailable. Role change could not be completed.',
              },
            });
          }

          console.error('Cognito error during role change:', cognitoErr);
          return res.status(500).json({
            error: {
              type: 'ServerError',
              code: 'ROLE_CHANGE_FAILED',
              message: 'Failed to update role in identity provider. Local role not changed.',
            },
          });
        }

        // Cognito succeeded — update local DB
        const updateResult = await db.query(
          `UPDATE users SET role = $1, updated_at = now() WHERE id = $2 RETURNING *`,
          [newRole, userId]
        );

        const updatedUser = updateResult.rows[0];
        res.json({
          id: updatedUser.id,
          email: updatedUser.email,
          name: updatedUser.name,
          role: updatedUser.role,
          status: updatedUser.status,
          department: updatedUser.department,
          lastActive: updatedUser.last_active,
          createdAt: updatedUser.created_at,
          updatedAt: updatedUser.updated_at,
        });
      } catch (err) {
        console.error('PATCH /api/users/:userId/role error:', err);
        res.status(500).json({
          error: {
            type: 'ServerError',
            code: 'INTERNAL_ERROR',
            message: 'Failed to change user role',
          },
        });
      }
    }
  );

  // ─── PATCH /api/users/:userId/status ─────────────────────────────────────

  /**
   * Activate or deactivate a user.
   * Updates Cognito (enable/disable) first, then the local record.
   */
  router.patch(
    '/api/users/:userId/status',
    authMiddleware,
    requirePermission('manage_users'),
    express.json(),
    async (req, res) => {
      try {
        const { userId } = req.params;
        const { status } = req.body;

        // Validate status
        if (!status || !['Active', 'Inactive'].includes(status)) {
          return res.status(422).json({
            error: {
              type: 'ValidationError',
              code: 'VALIDATION_ERROR',
              message: `Invalid status: '${status}'. Allowed values: Active, Inactive`,
            },
          });
        }

        // Fetch target user
        const userResult = await db.query(
          'SELECT id, email, status FROM users WHERE id = $1',
          [userId]
        );
        if (userResult.rows.length === 0) {
          return res.status(404).json({
            error: {
              type: 'NotFoundError',
              code: 'USER_NOT_FOUND',
              message: `User with id '${userId}' not found`,
            },
          });
        }

        const targetUser = userResult.rows[0];
        const username = targetUser.email;

        // Update Cognito user status
        try {
          if (status === 'Active') {
            const enableCmd = new AdminEnableUserCommand({
              UserPoolId: userPoolId,
              Username: username,
            });
            await cognito.send(enableCmd);
          } else {
            const disableCmd = new AdminDisableUserCommand({
              UserPoolId: userPoolId,
              Username: username,
            });
            await cognito.send(disableCmd);
          }
        } catch (cognitoErr) {
          if (isCognitoServiceError(cognitoErr)) {
            console.error('Cognito service error during status change:', cognitoErr);
            return res.status(502).json({
              error: {
                type: 'ServerError',
                code: 'COGNITO_UNAVAILABLE',
                message: 'Identity provider is unavailable. Status change could not be completed.',
              },
            });
          }

          console.error('Cognito error during status change:', cognitoErr);
          return res.status(500).json({
            error: {
              type: 'ServerError',
              code: 'STATUS_CHANGE_FAILED',
              message: 'Failed to update user status in identity provider',
            },
          });
        }

        // Cognito succeeded — update local DB
        const updateResult = await db.query(
          `UPDATE users SET status = $1, updated_at = now() WHERE id = $2 RETURNING *`,
          [status, userId]
        );

        const updatedUser = updateResult.rows[0];
        res.json({
          id: updatedUser.id,
          email: updatedUser.email,
          name: updatedUser.name,
          role: updatedUser.role,
          status: updatedUser.status,
          department: updatedUser.department,
          lastActive: updatedUser.last_active,
          createdAt: updatedUser.created_at,
          updatedAt: updatedUser.updated_at,
        });
      } catch (err) {
        console.error('PATCH /api/users/:userId/status error:', err);
        res.status(500).json({
          error: {
            type: 'ServerError',
            code: 'INTERNAL_ERROR',
            message: 'Failed to update user status',
          },
        });
      }
    }
  );

  return router;
};

// Export constants for testing
module.exports.VALID_ROLES = VALID_ROLES;
module.exports.ROLE_TO_GROUP = ROLE_TO_GROUP;
module.exports.CHIEF_EDITOR_MANAGEABLE_ROLES = CHIEF_EDITOR_MANAGEABLE_ROLES;
