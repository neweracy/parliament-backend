'use strict';

/**
 * Security Middleware — HTTPS Enforcement
 *
 * Enforces HTTPS for all non-loopback requests. Permits HTTP only when BOTH
 * the socket peer address AND the request-target hostname are loopback.
 *
 * Trusted proxy support: trusts X-Forwarded-Proto only from explicitly
 * configured trusted proxy addresses. Rejects insecure non-loopback requests
 * without reflecting any query data in the response.
 *
 * Requirements: 13.1–13.8
 *
 * @module middleware/security
 */

/**
 * Set of IPv4 and IPv6 loopback addresses.
 * Includes the IPv4-mapped IPv6 form (::ffff:127.0.0.1) that Node.js
 * reports for IPv4 connections on dual-stack sockets.
 */
const LOOPBACK_ADDRESSES = new Set([
  '127.0.0.1',
  '::1',
  '::ffff:127.0.0.1',
]);

/**
 * Determines whether an address string is a loopback address.
 *
 * @param {string} address - IP address or hostname
 * @returns {boolean} true if the address is loopback
 */
function isLoopback(address) {
  if (!address || typeof address !== 'string') return false;
  return LOOPBACK_ADDRESSES.has(address) || address === 'localhost';
}

/**
 * Creates the HTTPS enforcement middleware.
 *
 * Options:
 * - trustProxy: when truthy, X-Forwarded-Proto from the immediate socket peer
 *   is evaluated. Can be a boolean (true = trust any direct peer) or a Set/Array
 *   of trusted proxy IP addresses.
 *
 * Behavior:
 * - Loopback requests (both socket peer AND hostname): HTTP permitted.
 * - Trusted proxy requests: transport security derived from rightmost
 *   X-Forwarded-Proto if exactly 'https'; otherwise treated as insecure.
 * - All other non-loopback requests: HTTPS required.
 * - Insecure non-loopback requests: rejected with 403 and a generic message.
 *   No query data, path, or user input is reflected in the response.
 *
 * @param {Object} [options={}] - Configuration options
 * @param {boolean|Set<string>|string[]} [options.trustProxy=false] - Trusted proxy config
 * @returns {Function} Express middleware
 */
function createHttpsEnforcement(options = {}) {
  const { trustProxy = false } = options;

  // Normalize trusted proxy addresses into a Set for O(1) lookup
  let trustedProxySet = null;
  if (trustProxy === true) {
    // Trust any direct peer (single-hop proxy assumed)
    trustedProxySet = true;
  } else if (trustProxy instanceof Set) {
    trustedProxySet = trustProxy;
  } else if (Array.isArray(trustProxy)) {
    trustedProxySet = new Set(trustProxy);
  }
  // false or falsy → trustedProxySet remains null (no proxy trust)

  return function httpsEnforcement(req, res, next) {
    // Step 1: Determine direct transport security
    let isSecure = req.secure; // true if TLS-terminated at this server

    // Step 2: Trust X-Forwarded-Proto ONLY from configured trusted proxy
    // Requirement 13.4: use rightmost value only when immediate socket peer matches
    // Requirement 13.7: ignore forwarded-protocol when peer is not trusted
    if (!isSecure && trustedProxySet !== null) {
      const socketAddress = req.socket.remoteAddress || '';
      const peerIsTrusted = trustedProxySet === true || trustedProxySet.has(socketAddress);

      if (peerIsTrusted) {
        const forwardedProto = req.headers['x-forwarded-proto'];

        // Requirement 13.5: treat as HTTPS only when value is exactly 'https'
        // Requirement 13.6: absent, multiple comma-separated values, or non-'https' → insecure
        if (typeof forwardedProto === 'string' && forwardedProto === 'https') {
          isSecure = true;
        }
        // Any other value (including 'https, http' or 'HTTP' etc.) remains insecure
      }
      // Non-trusted peer: ignore all forwarded headers (Requirement 13.7)
    }

    // If the connection is secure, proceed
    if (isSecure) {
      return next();
    }

    // Step 3: Check loopback exception
    // Requirement 13.2: permit HTTP when BOTH socket peer AND hostname are loopback
    // Requirement 13.3: if only ONE is loopback, treat as non-loopback
    const socketAddress = req.socket.remoteAddress || '';
    const hostname = req.hostname || (req.headers.host ? req.headers.host.split(':')[0] : '');

    const socketIsLoopback = isLoopback(socketAddress);
    const hostIsLoopback = isLoopback(hostname);

    if (socketIsLoopback && hostIsLoopback) {
      // Both peer and hostname are loopback — allow HTTP
      return next();
    }

    // Step 4: Non-loopback, insecure request — reject
    // Requirement 13.8: reject without reflecting path or query data
    return res.status(403).json({
      error: {
        type: 'SecurityError',
        code: 'HTTPS_REQUIRED',
        message: 'HTTPS is required for non-local requests',
      },
    });
  };
}

module.exports = createHttpsEnforcement;
module.exports.isLoopback = isLoopback;
module.exports.LOOPBACK_ADDRESSES = LOOPBACK_ADDRESSES;
