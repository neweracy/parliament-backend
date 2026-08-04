'use strict';

/**
 * Security Headers Middleware — Browser Security and Cache Controls
 *
 * Emits Content-Security-Policy, X-Content-Type-Options, Referrer-Policy,
 * X-Frame-Options, Strict-Transport-Security, and Cache-Control/Pragma
 * headers per Requirements 14.1–14.12.
 *
 * Usage:
 *   const createSecurityHeaders = require('./middleware/security-headers');
 *   app.use(createSecurityHeaders({ authMode, cognitoDomain, isProduction }));
 *
 * @module middleware/security-headers
 */

/**
 * Creates security headers middleware.
 *
 * @param {Object} [options={}]
 * @param {string} [options.authMode] - 'legacy' or 'cognito'
 * @param {string} [options.cognitoDomain] - Cognito hosted UI domain (without https://)
 * @param {boolean} [options.isProduction=false] - Whether this is a production deployment
 * @returns {Function} Express middleware
 */
function createSecurityHeaders(options = {}) {
  const { authMode, cognitoDomain, isProduction = false } = options;

  // Build CSP directives (Req 14.1, 14.2, 14.3, 14.4)
  const cspDirectives = [
    "default-src 'self'",
    "script-src 'self'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "style-src 'self' 'unsafe-inline'",
  ];

  // Req 14.3: In cognito mode, add Cognito origin to connect-src and form-action
  // Req 14.4: In local-dev mode, connect-src and form-action are just 'self'
  if (authMode === 'cognito' && cognitoDomain) {
    const cognitoOrigin = `https://${cognitoDomain}`;
    cspDirectives.push(`connect-src 'self' ${cognitoOrigin}`);
    cspDirectives.push(`form-action 'self' ${cognitoOrigin}`);
  } else {
    cspDirectives.push("connect-src 'self'");
    cspDirectives.push("form-action 'self'");
  }

  // Pre-compute the CSP value (Req 14.1: exactly one Content-Security-Policy header)
  const cspValue = cspDirectives.join('; ');

  return function securityHeadersMiddleware(req, res, next) {
    // Req 14.1, 14.2: Content-Security-Policy
    res.setHeader('Content-Security-Policy', cspValue);

    // Req 14.5: X-Content-Type-Options: nosniff
    res.setHeader('X-Content-Type-Options', 'nosniff');

    // Req 14.6: Referrer-Policy: no-referrer
    res.setHeader('Referrer-Policy', 'no-referrer');

    // Req 14.7: X-Frame-Options: DENY (defense in depth for older clients)
    res.setHeader('X-Frame-Options', 'DENY');

    // Req 14.8, 14.9: Strict-Transport-Security only on production HTTPS
    if (isProduction && req.secure) {
      res.setHeader('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
    }

    // Req 14.10: Cache-Control: no-store on all auth responses
    // Req 14.11: Pragma: no-cache on token responses (success responses from login)
    const isAuthPath = req.path === '/api/auth/login' || req.path === '/api/session';
    if (isAuthPath) {
      res.setHeader('Cache-Control', 'no-store');
      // Pragma: no-cache is set on all auth path responses here.
      // The login handler additionally ensures it on success responses,
      // but we set it universally on auth endpoints for defense in depth.
      res.setHeader('Pragma', 'no-cache');
    }

    next();
  };
}

module.exports = createSecurityHeaders;
