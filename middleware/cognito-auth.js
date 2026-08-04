'use strict';

const jwt = require('jsonwebtoken');
const jwksRsa = require('jwks-rsa');

/**
 * Group-to-role precedence map (highest first).
 * First match wins when a user belongs to multiple groups.
 */
const GROUP_ROLE_MAP = [
  { group: 'admin', role: 'Admin' },
  { group: 'chief-editor', role: 'Chief Editor' },
  { group: 'supervisor', role: 'Supervisor' },
  { group: 'editor', role: 'Editor' },
  { group: 'viewer', role: 'Viewer' },
];

/**
 * Default permissions per role. Used inline until lib/rbac-config.js is available.
 * Matches the design.md permission table exactly.
 */
const ROLE_PERMISSIONS = {
  'Admin': [
    'manage_users', 'system_config', 'create_sitting', 'assign_editor',
    'certify_record', 'manage_templates', 'export_hansard', 'view_audit_trail',
    'review_record', 'approve_certification', 'edit_record', 'upload_audio',
    'rename_speakers', 'submit_for_review', 'export_drafts', 'view_records',
    'search_hansard', 'export_published',
  ],
  'Chief Editor': [
    'manage_users', 'create_sitting', 'assign_editor', 'certify_record',
    'manage_templates', 'export_hansard', 'view_audit_trail', 'review_record',
    'edit_record', 'upload_audio', 'rename_speakers', 'submit_for_review',
    'export_drafts', 'view_records', 'search_hansard', 'export_published',
  ],
  'Supervisor': [
    'review_record', 'approve_certification', 'export_hansard',
    'view_audit_trail', 'export_drafts', 'view_records', 'search_hansard',
    'export_published',
  ],
  'Editor': [
    'edit_record', 'upload_audio', 'rename_speakers', 'submit_for_review',
    'export_drafts', 'view_records', 'search_hansard', 'export_published',
  ],
  'Viewer': [
    'view_records', 'search_hansard', 'export_published',
  ],
};

/**
 * Resolves the highest-precedence role from a list of Cognito groups.
 * Returns 'Viewer' if no groups match.
 *
 * @param {string[]} groups - Array of Cognito group names
 * @returns {string} - Resolved role name
 */
function resolveRole(groups) {
  if (!Array.isArray(groups) || groups.length === 0) {
    return 'Viewer';
  }

  for (const { group, role } of GROUP_ROLE_MAP) {
    if (groups.includes(group)) {
      return role;
    }
  }

  return 'Viewer';
}

/**
 * Returns the permissions array for a given role.
 *
 * @param {string} role - Role name
 * @returns {string[]} - Array of permission identifiers
 */
function getPermissionsForRole(role) {
  return ROLE_PERMISSIONS[role] || ROLE_PERMISSIONS['Viewer'];
}

/**
 * Creates Cognito JWT validation middleware.
 *
 * @param {Object} config
 * @param {string} config.userPoolId - AWS Cognito User Pool ID
 * @param {string} config.region - AWS region (e.g., 'eu-west-1')
 * @param {string} config.appClientId - Cognito App Client ID (audience)
 * @returns {Function} Express middleware (req, res, next)
 */
function createCognitoAuth({ userPoolId, region, appClientId }) {
  const issuer = `https://cognito-idp.${region}.amazonaws.com/${userPoolId}`;
  const jwksUri = `${issuer}/.well-known/jwks.json`;

  // Configure jwks-rsa client with 24h caching and 5-min rate limit
  const jwksClient = jwksRsa({
    jwksUri,
    cache: true,
    cacheMaxAge: 24 * 60 * 60 * 1000, // 24 hours
    rateLimit: true,
    jwksRequestsPerMinute: 12, // max 1 request per 5 seconds
    timeout: 5000, // 5s timeout for JWKS fetch
  });

  /**
   * Retrieves the signing key for a given key ID (kid).
   *
   * @param {string} kid - Key ID from JWT header
   * @returns {Promise<string>} - PEM-encoded public key
   */
  function getSigningKey(kid) {
    return new Promise((resolve, reject) => {
      jwksClient.getSigningKey(kid, (err, key) => {
        if (err) {
          reject(err);
          return;
        }
        const signingKey = key.getPublicKey();
        resolve(signingKey);
      });
    });
  }

  /**
   * Express middleware that validates Cognito JWT tokens.
   */
  return async function cognitoAuth(req, res, next) {
    // 1. Extract Bearer token from Authorization header
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    const token = authHeader.slice(7);
    if (!token) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'MISSING_TOKEN',
          message: 'Authorization header with Bearer token is required',
        },
      });
    }

    // 2. Decode header to get kid (without verifying yet)
    let decoded;
    try {
      decoded = jwt.decode(token, { complete: true });
    } catch (_err) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    if (!decoded || !decoded.header || !decoded.header.kid) {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    // Reject tokens not using RS256 before JWKS lookup (defense-in-depth).
    // This explicitly blocks HS256 local tokens from being accepted in Cognito mode.
    if (decoded.header.alg !== 'RS256') {
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    // 3. Fetch the signing key from JWKS
    let signingKey;
    try {
      signingKey = await getSigningKey(decoded.header.kid);
    } catch (err) {
      // JWKS unreachable + no cached keys
      if (err.name === 'SigningKeyNotFoundError') {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'INVALID_TOKEN',
            message: 'Invalid authentication token',
          },
        });
      }
      // Network/timeout error fetching JWKS
      return res.status(503).json({
        error: {
          type: 'AuthenticationError',
          code: 'AUTH_SERVICE_UNAVAILABLE',
          message: 'Authentication service is temporarily unavailable',
        },
      });
    }

    // 4. Verify token signature, issuer, audience, and expiry
    let payload;
    try {
      payload = jwt.verify(token, signingKey, {
        algorithms: ['RS256'],
        issuer: issuer,
        audience: appClientId,
      });
    } catch (err) {
      if (err.name === 'TokenExpiredError') {
        return res.status(401).json({
          error: {
            type: 'AuthenticationError',
            code: 'TOKEN_EXPIRED',
            message: 'Session expired, please sign in again',
          },
        });
      }
      // Invalid signature, issuer mismatch, audience mismatch, etc.
      return res.status(401).json({
        error: {
          type: 'AuthenticationError',
          code: 'INVALID_TOKEN',
          message: 'Invalid authentication token',
        },
      });
    }

    // 5. Extract claims and resolve role
    const userId = payload.sub;
    const email = payload.email || '';
    const name = payload.name || payload['cognito:username'] || '';
    const groups = payload['cognito:groups'] || [];

    const role = resolveRole(groups);
    const permissions = getPermissionsForRole(role);

    // 6. Attach user context to request
    req.user = {
      userId,
      email,
      name,
      role,
      permissions,
    };

    next();
  };
}

// Export factory and helpers for testing
module.exports = createCognitoAuth;
module.exports.createCognitoAuth = createCognitoAuth;
module.exports.resolveRole = resolveRole;
module.exports.getPermissionsForRole = getPermissionsForRole;
module.exports.GROUP_ROLE_MAP = GROUP_ROLE_MAP;
module.exports.ROLE_PERMISSIONS = ROLE_PERMISSIONS;
