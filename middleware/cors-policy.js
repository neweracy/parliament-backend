'use strict';

/**
 * Exact-origin CORS policy middleware.
 *
 * Replaces the wildcard `cors()` with a finite FRONTEND_ORIGINS allowlist.
 * - Exact byte-for-byte origin matching; omits Access-Control-Allow-Origin on mismatch.
 * - Preflight: 204 with permitted methods/headers; 403 on unknown method/header.
 * - Credentials support disabled; no wildcards on auth/protected APIs.
 *
 * Requirements: 13.9–13.16
 */

/**
 * Default allowed HTTP methods for CORS preflight.
 * Only methods the application actually uses are permitted.
 */
const DEFAULT_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'];

/**
 * Default allowed request headers for CORS preflight.
 * Only headers the application needs are permitted.
 */
const DEFAULT_HEADERS = ['Content-Type', 'Authorization', 'X-Request-ID'];

/**
 * Creates a strict CORS middleware with exact-origin matching.
 *
 * @param {Object} [options]
 * @param {string} [options.origins] - Comma-separated origin allowlist.
 *   Falls back to FRONTEND_ORIGINS env var, then to 'http://localhost:5173'.
 * @param {string[]} [options.methods] - Allowed HTTP methods.
 * @param {string[]} [options.headers] - Allowed request headers.
 * @returns {Function} Express middleware
 */
function createCorsPolicy(options = {}) {
  const originsEnv = options.origins || process.env.FRONTEND_ORIGINS || 'http://localhost:5173';
  const allowedOrigins = new Set(
    originsEnv.split(',').map(o => o.trim()).filter(Boolean)
  );

  const allowedMethods = options.methods || DEFAULT_METHODS;
  const allowedHeaders = options.headers || DEFAULT_HEADERS;

  // Pre-compute lowercase header set for comparison
  const allowedHeadersLower = new Set(allowedHeaders.map(h => h.toLowerCase()));

  // Pre-compute uppercase method set for comparison
  const allowedMethodsUpper = new Set(allowedMethods.map(m => m.toUpperCase()));

  // Serialized values for response headers (computed once)
  const methodsString = allowedMethods.join(', ');
  const headersString = allowedHeaders.join(', ');

  return function corsMiddleware(req, res, next) {
    const origin = req.headers.origin;

    // No origin header → not a cross-origin request; no CORS headers needed
    if (!origin) {
      // Still handle OPTIONS without origin as a non-CORS preflight
      if (req.method === 'OPTIONS') {
        return res.status(204).end();
      }
      return next();
    }

    // Exact byte-for-byte origin matching (Req 13.10, 13.11)
    if (!allowedOrigins.has(origin)) {
      // Origin mismatch: omit Access-Control-Allow-Origin entirely (Req 13.11, 13.14)
      if (req.method === 'OPTIONS') {
        // Preflight from unknown/mismatched origin → 403 (Req 13.13)
        return res.status(403).end();
      }
      // Non-preflight: proceed without CORS headers; browser will block response
      return next();
    }

    // Origin matches — set CORS response headers (Req 13.10)
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');

    // Credentials explicitly disabled (Req 13.15)
    // Do NOT set Access-Control-Allow-Credentials

    // Handle preflight (OPTIONS)
    if (req.method === 'OPTIONS') {
      const requestMethod = req.headers['access-control-request-method'];
      const requestHeaders = req.headers['access-control-request-headers'];

      // Validate requested method against allowlist (Req 13.12, 13.13)
      if (requestMethod) {
        if (!allowedMethodsUpper.has(requestMethod.toUpperCase())) {
          // Unknown method → 403, omit allow headers (Req 13.13)
          res.removeHeader('Access-Control-Allow-Origin');
          return res.status(403).end();
        }
      }

      // Validate requested headers against allowlist (Req 13.12, 13.13)
      if (requestHeaders) {
        const requested = requestHeaders.split(',').map(h => h.trim().toLowerCase());
        for (const h of requested) {
          if (h && !allowedHeadersLower.has(h)) {
            // Unknown header → 403, omit allow headers (Req 13.13)
            res.removeHeader('Access-Control-Allow-Origin');
            return res.status(403).end();
          }
        }
      }

      // Valid preflight → 204 with permitted methods/headers (Req 13.12)
      res.setHeader('Access-Control-Allow-Methods', methodsString);
      res.setHeader('Access-Control-Allow-Headers', headersString);
      res.setHeader('Access-Control-Max-Age', '86400');
      return res.status(204).end();
    }

    // Simple/actual request with matching origin — continue to route handler
    next();
  };
}

module.exports = createCorsPolicy;
