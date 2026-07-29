/**
 * HTTP client for the Postprocessing_Service.
 *
 * Sends Correction_Requests and returns typed success/failure results.
 * Includes timeout via AbortSignal, at-most-once retry on connection
 * errors or 502/503/504, and an in-process circuit breaker.
 *
 * @module lib/postprocess-client
 */

"use strict";

// ---------------------------------------------------------------------------
// Configuration (environment variables with documented defaults)
// ---------------------------------------------------------------------------

const POSTPROCESS_URL = process.env.POSTPROCESS_URL || "http://localhost:8082";
const POSTPROCESS_TIMEOUT_MS = Number(process.env.POSTPROCESS_TIMEOUT_MS) || 20000;
const POSTPROCESS_TOKEN = process.env.POSTPROCESS_TOKEN || "";
const POSTPROCESS_BREAKER_THRESHOLD = Number(process.env.POSTPROCESS_BREAKER_THRESHOLD) || 5;
const POSTPROCESS_BREAKER_COOLDOWN_MS = Number(process.env.POSTPROCESS_BREAKER_COOLDOWN_MS) || 30000;

// ---------------------------------------------------------------------------
// Circuit breaker — in-process state machine
// ---------------------------------------------------------------------------

/**
 * Module-level circuit breaker state.
 * States: 'closed' | 'open' | 'half_open'
 *
 * Closed → Open: consecutiveFailures >= POSTPROCESS_BREAKER_THRESHOLD
 * Open → HalfOpen: cool-down elapsed (POSTPROCESS_BREAKER_COOLDOWN_MS)
 * HalfOpen → Closed: probe request succeeds (resets counter)
 * HalfOpen → Open: probe request fails
 */
const _breakerState = {
  state: "closed",
  consecutiveFailures: 0,
  openedAt: null,
};

/**
 * Check whether the circuit breaker should block the call.
 *
 * - Closed: allow (return false)
 * - Open + cooldown not elapsed: block (return true)
 * - Open + cooldown elapsed: transition to half_open, admit one probe (return false)
 * - HalfOpen: block concurrent callers (return true) — only the probe is admitted
 *
 * @returns {boolean} true if the call should be skipped
 */
function _isCircuitOpen() {
  if (_breakerState.state === "closed") {
    return false;
  }

  if (_breakerState.state === "open") {
    const elapsed = Date.now() - _breakerState.openedAt;
    if (elapsed >= POSTPROCESS_BREAKER_COOLDOWN_MS) {
      // Cool-down elapsed — admit one probe
      _breakerState.state = "half_open";
      return false;
    }
    // Still cooling down — skip the call
    return true;
  }

  // state === 'half_open': only one probe admitted; concurrent callers see open
  return true;
}

/**
 * Record the result of a request and update breaker state.
 *
 * @param {boolean} success - Whether the request succeeded
 */
function _recordResult(success) {
  if (success) {
    _breakerState.consecutiveFailures = 0;
    _breakerState.state = "closed";
    _breakerState.openedAt = null;
  } else {
    _breakerState.consecutiveFailures += 1;
    if (_breakerState.consecutiveFailures >= POSTPROCESS_BREAKER_THRESHOLD) {
      _breakerState.state = "open";
      _breakerState.openedAt = Date.now();
    }
  }
}

/**
 * Reset the circuit breaker to its initial closed state.
 * Exported for testing only.
 */
function _resetCircuitBreaker() {
  _breakerState.state = "closed";
  _breakerState.consecutiveFailures = 0;
  _breakerState.openedAt = null;
}

// ---------------------------------------------------------------------------
// Retry-eligible status codes
// ---------------------------------------------------------------------------

const RETRYABLE_STATUS_CODES = new Set([502, 503, 504]);

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Classify an error or HTTP status into the closed reason set.
 * @param {Error|null} err
 * @param {Response|null} res
 * @returns {string}
 */
function _classifyFailure(err, res) {
  if (err) {
    if (err.name === "TimeoutError" || err.name === "AbortError") {
      return "timeout";
    }
    return "connection";
  }
  if (res) {
    if (res.status >= 500) return "http_5xx";
    if (res.status >= 400) return "http_4xx";
  }
  return "connection";
}

/**
 * Determine whether a failure is eligible for retry.
 * Only connection errors and HTTP 502/503/504 qualify.
 * @param {Error|null} err
 * @param {Response|null} res
 * @returns {boolean}
 */
function _isRetryable(err, res) {
  if (err) {
    // Connection errors are retryable; timeouts are not
    if (err.name === "TimeoutError" || err.name === "AbortError") return false;
    return true;
  }
  if (res && RETRYABLE_STATUS_CODES.has(res.status)) return true;
  return false;
}

/**
 * Execute a single fetch attempt.
 * @param {string} url
 * @param {object} body
 * @param {string} correlationId
 * @returns {Promise<{err: Error|null, res: Response|null}>}
 */
async function _attempt(url, body, correlationId) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${POSTPROCESS_TOKEN}`,
        "X-Correlation-Id": correlationId,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(POSTPROCESS_TIMEOUT_MS),
    });
    return { err: null, res };
  } catch (err) {
    return { err, res: null };
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Send a Correction_Request to the Postprocessing_Service.
 *
 * @param {string} transcript - Raw transcript text
 * @param {Array<object>} words - Ordered Word list from ASR
 * @param {object} options - Correction options (e.g. { llmRefine: true })
 * @param {string} correlationId - Correlation identifier for tracing
 * @returns {Promise<{ok: true, data: object} | {ok: false, reason: string, elapsedMs: number}>}
 */
async function postprocess(transcript, words, options, correlationId) {
  const start = Date.now();

  // 1. Circuit breaker check
  if (_isCircuitOpen()) {
    return { ok: false, reason: "circuit_open", elapsedMs: Date.now() - start };
  }

  const url = `${POSTPROCESS_URL}/v1/postprocess`;
  const body = { transcript, words, options, correlationId };

  // 2. First attempt
  let { err, res } = await _attempt(url, body, correlationId);

  // 3. Retry exactly once if retryable
  if (_isRetryable(err, res)) {
    ({ err, res } = await _attempt(url, body, correlationId));
  }

  // 4. Handle network/fetch errors
  if (err) {
    const reason = _classifyFailure(err, null);
    _recordResult(false);
    return { ok: false, reason, elapsedMs: Date.now() - start };
  }

  // 5. Handle non-2xx HTTP responses
  if (!res.ok) {
    const reason = _classifyFailure(null, res);
    _recordResult(false);
    return { ok: false, reason, elapsedMs: Date.now() - start };
  }

  // 6. Parse response body
  try {
    const data = await res.json();
    _recordResult(true);
    return { ok: true, data };
  } catch (_parseErr) {
    _recordResult(false);
    return { ok: false, reason: "bad_body", elapsedMs: Date.now() - start };
  }
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  postprocess,
  // Circuit breaker internals — exported for testing
  _isCircuitOpen,
  _recordResult,
  _resetCircuitBreaker,
  _breakerState,
  // Configuration exports for tests and downstream consumers
  POSTPROCESS_URL,
  POSTPROCESS_TIMEOUT_MS,
  POSTPROCESS_TOKEN,
  POSTPROCESS_BREAKER_THRESHOLD,
  POSTPROCESS_BREAKER_COOLDOWN_MS,
};
