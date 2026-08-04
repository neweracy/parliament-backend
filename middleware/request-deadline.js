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
    //
    // Guard on the response, not the request. Node auto-destroys a readable
    // stream once it has been fully consumed, so after a body parser such as
    // express.json() runs, `req.destroyed` is always true even though the
    // client is still connected and waiting. `res.destroyed` only becomes true
    // when the connection itself is gone.
    if (res.destroyed || res.writableEnded) {
      return; // Client is gone; do not send a response or proceed
    }

    // --- 2. Create AbortController for downstream cancellation ---
    const controller = new AbortController();
    req.deadlineSignal = controller.signal;
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
    //
    // Listen on the response, not the request. A request stream emits 'close'
    // as soon as it has been fully consumed — which a body parser does before
    // this middleware runs — so `req.on('close')` would abort the controller
    // immediately and make every downstream abort check short-circuit, leaving
    // the request hanging with no response. The response only emits 'close'
    // when the connection itself goes away.
    function onConnectionClose() {
      clearTimeout(timer);
      // A normal finish also emits 'close'; only abort if we never replied.
      if (!res.writableFinished) {
        controller.abort();
      }
      cleanup();
    }

    // --- 5. Response finished (success path) — clear the timer ---
    function onResponseFinish() {
      clearTimeout(timer);
      cleanup();
    }

    // --- 6. Cleanup listeners to avoid leaks ---
    function cleanup() {
      res.removeListener('close', onConnectionClose);
      res.removeListener('finish', onResponseFinish);
    }

    res.on('close', onConnectionClose);
    res.on('finish', onResponseFinish);

    next();
  };
}

module.exports = requestDeadline;
