'use strict';

/**
 * Request deadline middleware for the login endpoint.
 *
 * Starts a configurable deadline timer when the middleware runs. If the timer
 * fires before `res.headersSent`, responds with HTTP 504. Also detects already-
 * aborted client connections and propagates an AbortSignal for downstream
 * cancellation.
 *
 * Usage:
 *   const requestDeadline = require('../middleware/request-deadline');
 *   router.post('/api/auth/login', requestDeadline(5000), ...handlers);
 *
 * @module middleware/request-deadline
 */

/**
 * 504 response body matching the design spec.
 */
const TIMEOUT_RESPONSE = Object.freeze({
  error: {
    type: 'ServerError',
    code: 'REQUEST_TIMEOUT',
    message: 'Request processing exceeded time limit',
  },
});

/**
 * Creates request deadline middleware with the specified timeout.
 *
 * @param {number} [timeoutMs=5000] - Deadline in milliseconds (default 5000).
 * @returns {Function} Express middleware (req, res, next).
 */
function requestDeadline(timeoutMs = 5000) {
  return function requestDeadlineMiddleware(req, res, next) {
    // --- 1. Reject already-aborted client connections ---
    if (req.destroyed) {
      return; // Client is gone; do not send a response or proceed
    }

    // --- 2. Create AbortController for downstream cancellation ---
    const controller = new AbortController();
    req.signal = controller.signal;
    req.deadlineExceeded = false;

    // --- 3. Start the deadline timer ---
    const timer = setTimeout(() => {
      req.deadlineExceeded = true;
      controller.abort();

      // Only send 504 if we haven't already started a response
      if (!res.headersSent) {
        res.status(504).json(TIMEOUT_RESPONSE);
      }
    }, timeoutMs);

    // Ensure the timer does not prevent Node from exiting
    if (timer.unref) {
      timer.unref();
    }

    // --- 4. Client disconnect handling ---
    function onClientClose() {
      clearTimeout(timer);
      controller.abort();
      cleanup();
    }

    // --- 5. Response finished (success path) — clear the timer ---
    function onResponseFinish() {
      clearTimeout(timer);
      cleanup();
    }

    // --- 6. Cleanup listeners to avoid leaks ---
    function cleanup() {
      req.removeListener('close', onClientClose);
      res.removeListener('finish', onResponseFinish);
    }

    req.on('close', onClientClose);
    res.on('finish', onResponseFinish);

    next();
  };
}

module.exports = requestDeadline;
