/**
 * Login Rate Limiter — Dual-Dimension Budgets
 *
 * Enforces source-address (IP) and Normalized_Email attempt budgets with
 * rolling-window semantics, progressive delay, and temporary lockout.
 *
 * - IP budget: 20 attempts per 15-minute rolling window
 * - Email budget: 10 attempts per 15-minute rolling window
 * - Account lockout: 10 failures → 600-second block; auto-expires
 * - Progressive delay: 3 consecutive failures → 250ms doubling to 4000ms
 * - Success clears consecutive-failure count and active lock
 *
 * Process-memory storage (Map with TTL cleanup) for single-instance dev.
 * Atomic budget updates via synchronous decision before async work.
 *
 * @module middleware/rate-limiter
 */

'use strict';

// ─── Configuration Constants ─────────────────────────────────────────────────

const WINDOW_SECONDS = 900; // 15-minute rolling window
const IP_BUDGET = 20; // Max attempts per IP in window
const EMAIL_BUDGET = 10; // Max attempts per email in window
const LOCKOUT_THRESHOLD = 10; // Failures before lockout
const LOCKOUT_DURATION_SECONDS = 600; // 10-minute lockout
const PROGRESSIVE_DELAY_START = 3; // Failures before delay begins
const PROGRESSIVE_DELAY_BASE_MS = 250; // Initial delay
const PROGRESSIVE_DELAY_MAX_MS = 4000; // Cap

// ─── In-Memory Storage ───────────────────────────────────────────────────────

/**
 * IP bucket structure:
 * { attempts: [timestamps], expiresAt: number }
 *
 * Email bucket structure:
 * { attempts: [timestamps], consecutiveFailures: number, blockedUntil: number|null, expiresAt: number }
 */

/** @type {Map<string, {attempts: number[], expiresAt: number}>} */
const ipStore = new Map();

/** @type {Map<string, {attempts: number[], consecutiveFailures: number, blockedUntil: number|null, expiresAt: number}>} */
const emailStore = new Map();

// ─── TTL Cleanup ─────────────────────────────────────────────────────────────

/**
 * Prune expired entries from both stores.
 * An entry is expired when:
 * - No active lock and its newest attempt is older than WINDOW_SECONDS
 * - Has an active lock but both blockedUntil and window have passed
 */
function pruneExpiredEntries() {
  const now = Date.now();

  for (const [key, bucket] of ipStore) {
    if (bucket.expiresAt < now) {
      ipStore.delete(key);
    }
  }

  for (const [key, bucket] of emailStore) {
    if (bucket.blockedUntil && bucket.blockedUntil > now) {
      // Active lock — keep until lock expires
      continue;
    }
    if (bucket.expiresAt < now) {
      emailStore.delete(key);
    }
  }
}

// Periodic cleanup every 60 seconds
const cleanupInterval = setInterval(pruneExpiredEntries, 60_000);
// Allow the process to exit without waiting for the interval
if (cleanupInterval.unref) {
  cleanupInterval.unref();
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Get timestamps within the rolling window.
 * @param {number[]} attempts - Array of timestamps (ms)
 * @param {number} now - Current time (ms)
 * @returns {number[]} - Timestamps within window
 */
function getWindowAttempts(attempts, now) {
  const windowStart = now - (WINDOW_SECONDS * 1000);
  return attempts.filter(t => t > windowStart);
}

/**
 * Compute the progressive delay in ms for the given consecutive failure count.
 * @param {number} consecutiveFailures
 * @returns {number} Delay in milliseconds (0 if under threshold)
 */
function computeProgressiveDelay(consecutiveFailures) {
  if (consecutiveFailures < PROGRESSIVE_DELAY_START) {
    return 0;
  }
  // failures=3 → 250ms, 4 → 500ms, 5 → 1000ms, 6 → 2000ms, 7+ → 4000ms
  const doublings = consecutiveFailures - PROGRESSIVE_DELAY_START;
  const delay = PROGRESSIVE_DELAY_BASE_MS * Math.pow(2, doublings);
  return Math.min(delay, PROGRESSIVE_DELAY_MAX_MS);
}

/**
 * Get the client IP respecting Express trust proxy settings.
 * When Express trusts a proxy, req.ip returns the rightmost forwarded client
 * address only when the immediate socket peer is that trusted proxy.
 * When Express does not trust the peer, req.ip returns the socket address
 * and ignores X-Forwarded-For.
 *
 * @param {import('express').Request} req
 * @returns {string} Client IP address
 */
function getClientIp(req) {
  // Express req.ip already respects the `trust proxy` setting:
  // - If trust proxy is configured and peer matches, returns rightmost forwarded address
  // - If trust proxy is not configured, returns socket address (ignores X-Forwarded-For)
  return req.ip || req.socket.remoteAddress || '0.0.0.0';
}

/**
 * Compute Retry-After value (ceiling seconds until both budgets allow retry).
 * @param {number} now - Current time in ms
 * @param {object} ipBucket - IP bucket or null
 * @param {object} emailBucket - Email bucket or null
 * @returns {number} Retry-After in seconds (minimum 1)
 */
function computeRetryAfter(now, ipBucket, emailBucket) {
  let maxWaitMs = 0;

  // IP budget: find when the oldest in-window attempt expires
  if (ipBucket) {
    const windowAttempts = getWindowAttempts(ipBucket.attempts, now);
    if (windowAttempts.length >= IP_BUDGET) {
      // Oldest in-window attempt; when it exits the window, budget reopens
      const oldestInWindow = windowAttempts[0];
      const waitMs = (oldestInWindow + WINDOW_SECONDS * 1000) - now;
      maxWaitMs = Math.max(maxWaitMs, waitMs);
    }
  }

  // Email budget: check blockedUntil or window expiry
  if (emailBucket) {
    if (emailBucket.blockedUntil && emailBucket.blockedUntil > now) {
      const waitMs = emailBucket.blockedUntil - now;
      maxWaitMs = Math.max(maxWaitMs, waitMs);
    } else {
      const windowAttempts = getWindowAttempts(emailBucket.attempts, now);
      if (windowAttempts.length >= EMAIL_BUDGET) {
        const oldestInWindow = windowAttempts[0];
        const waitMs = (oldestInWindow + WINDOW_SECONDS * 1000) - now;
        maxWaitMs = Math.max(maxWaitMs, waitMs);
      }
    }
  }

  // Ceiling to seconds, minimum 1
  return Math.max(1, Math.ceil(maxWaitMs / 1000));
}

// ─── Rate Limiter Middleware Factory ─────────────────────────────────────────

/**
 * Creates the rate limiter middleware.
 *
 * The middleware expects `req.normalizedEmail` to be set by the login-validator
 * middleware upstream.
 *
 * @param {Object} [options] - Configuration overrides (for testing)
 * @param {number} [options.ipBudget] - Max IP attempts per window
 * @param {number} [options.emailBudget] - Max email attempts per window
 * @param {number} [options.windowSeconds] - Rolling window duration
 * @param {number} [options.lockoutThreshold] - Failures before lockout
 * @param {number} [options.lockoutDurationSeconds] - Lockout duration
 * @returns {import('express').RequestHandler}
 */
function createRateLimiter(options = {}) {
  const ipBudget = options.ipBudget || IP_BUDGET;
  const emailBudget = options.emailBudget || EMAIL_BUDGET;
  const windowSeconds = options.windowSeconds || WINDOW_SECONDS;
  const lockoutThreshold = options.lockoutThreshold || LOCKOUT_THRESHOLD;
  const lockoutDurationSeconds = options.lockoutDurationSeconds || LOCKOUT_DURATION_SECONDS;

  return function rateLimiterMiddleware(req, res, next) {
    const now = Date.now();
    const clientIp = getClientIp(req);
    const normalizedEmail = req.normalizedEmail;

    if (!normalizedEmail) {
      // If normalizedEmail is not set, the login-validator hasn't run.
      // Fail closed — reject the request.
      return res.status(500).json({
        error: {
          type: 'ServerError',
          code: 'INTERNAL_ERROR',
          message: 'An internal error occurred',
        },
      });
    }

    // ── IP Budget Check (atomic synchronous decision) ──

    let ipBucket = ipStore.get(clientIp);
    if (!ipBucket) {
      ipBucket = { attempts: [], expiresAt: now + (windowSeconds * 1000) };
      ipStore.set(clientIp, ipBucket);
    }

    // Prune out-of-window attempts for this bucket
    ipBucket.attempts = getWindowAttempts(ipBucket.attempts, now);

    if (ipBucket.attempts.length >= ipBudget) {
      // IP budget exceeded
      const retryAfter = computeRetryAfter(now, ipBucket, null);
      res.set('Retry-After', String(retryAfter));
      res.set('Cache-Control', 'no-store');
      return res.status(429).json({
        error: {
          type: 'RateLimitError',
          code: 'TOO_MANY_ATTEMPTS',
          message: 'Too many login attempts. Please try again later.',
        },
      });
    }

    // ── Email Budget Check (atomic synchronous decision) ──

    let emailBucket = emailStore.get(normalizedEmail);
    if (!emailBucket) {
      emailBucket = {
        attempts: [],
        consecutiveFailures: 0,
        blockedUntil: null,
        expiresAt: now + (windowSeconds * 1000),
      };
      emailStore.set(normalizedEmail, emailBucket);
    }

    // Check active lockout
    if (emailBucket.blockedUntil && emailBucket.blockedUntil > now) {
      // Account is locked — reject without DB lookup or bcrypt
      const retryAfter = computeRetryAfter(now, null, emailBucket);
      res.set('Retry-After', String(retryAfter));
      res.set('Cache-Control', 'no-store');
      return res.status(429).json({
        error: {
          type: 'RateLimitError',
          code: 'TOO_MANY_ATTEMPTS',
          message: 'Too many login attempts. Please try again later.',
        },
      });
    }

    // If lock has expired, clear it
    if (emailBucket.blockedUntil && emailBucket.blockedUntil <= now) {
      emailBucket.blockedUntil = null;
    }

    // Prune out-of-window attempts for email bucket
    emailBucket.attempts = getWindowAttempts(emailBucket.attempts, now);

    if (emailBucket.attempts.length >= emailBudget) {
      // Email budget exceeded
      const retryAfter = computeRetryAfter(now, ipBucket, emailBucket);
      res.set('Retry-After', String(retryAfter));
      res.set('Cache-Control', 'no-store');
      return res.status(429).json({
        error: {
          type: 'RateLimitError',
          code: 'TOO_MANY_ATTEMPTS',
          message: 'Too many login attempts. Please try again later.',
        },
      });
    }

    // ── Record attempt in both buckets (atomic before next()) ──

    ipBucket.attempts.push(now);
    ipBucket.expiresAt = now + (windowSeconds * 1000);

    emailBucket.attempts.push(now);
    emailBucket.expiresAt = now + (windowSeconds * 1000);

    // Proceed to credential verification
    next();
  };
}

// ─── Post-Auth Functions ─────────────────────────────────────────────────────

/**
 * Record a credential failure for the given normalized email.
 * Increments consecutive-failure count and applies lockout if threshold reached.
 *
 * @param {string} normalizedEmail
 */
function recordFailure(normalizedEmail) {
  const now = Date.now();
  let emailBucket = emailStore.get(normalizedEmail);

  if (!emailBucket) {
    emailBucket = {
      attempts: [],
      consecutiveFailures: 0,
      blockedUntil: null,
      expiresAt: now + (WINDOW_SECONDS * 1000),
    };
    emailStore.set(normalizedEmail, emailBucket);
  }

  emailBucket.consecutiveFailures += 1;

  // Apply lockout at threshold
  if (emailBucket.consecutiveFailures >= LOCKOUT_THRESHOLD) {
    emailBucket.blockedUntil = now + (LOCKOUT_DURATION_SECONDS * 1000);
    // Update expiresAt to include lockout duration
    emailBucket.expiresAt = Math.max(
      emailBucket.expiresAt,
      emailBucket.blockedUntil
    );
  }
}

/**
 * Clear consecutive-failure count, progressive-delay state, and active lock
 * for the authenticated Normalized_Email on successful login.
 *
 * @param {string} normalizedEmail
 */
function clearOnSuccess(normalizedEmail) {
  const emailBucket = emailStore.get(normalizedEmail);
  if (emailBucket) {
    emailBucket.consecutiveFailures = 0;
    emailBucket.blockedUntil = null;
  }
}

/**
 * Get the progressive delay in milliseconds for a given normalized email.
 * Called after credential verification to delay the response on consecutive failures.
 *
 * Progressive delay schedule:
 * - 3 consecutive failures → 250ms
 * - 4 → 500ms
 * - 5 → 1000ms
 * - 6 → 2000ms
 * - 7+ → 4000ms (cap)
 *
 * @param {string} normalizedEmail
 * @returns {number} Delay in milliseconds (0 if not applicable)
 */
function getProgressiveDelay(normalizedEmail) {
  const emailBucket = emailStore.get(normalizedEmail);
  if (!emailBucket) {
    return 0;
  }
  return computeProgressiveDelay(emailBucket.consecutiveFailures);
}

// ─── Testing Utilities ───────────────────────────────────────────────────────

/**
 * Reset all rate limiter state. For testing only.
 */
function _resetForTesting() {
  ipStore.clear();
  emailStore.clear();
}

/**
 * Get the current state of the IP store. For testing only.
 * @returns {Map}
 */
function _getIpStore() {
  return ipStore;
}

/**
 * Get the current state of the email store. For testing only.
 * @returns {Map}
 */
function _getEmailStore() {
  return emailStore;
}

// ─── Exports ─────────────────────────────────────────────────────────────────

module.exports = {
  createRateLimiter,
  recordFailure,
  clearOnSuccess,
  getProgressiveDelay,
  getClientIp,

  // Testing utilities
  _resetForTesting,
  _getIpStore,
  _getEmailStore,

  // Constants (for testing reference)
  WINDOW_SECONDS,
  IP_BUDGET,
  EMAIL_BUDGET,
  LOCKOUT_THRESHOLD,
  LOCKOUT_DURATION_SECONDS,
  PROGRESSIVE_DELAY_START,
  PROGRESSIVE_DELAY_BASE_MS,
  PROGRESSIVE_DELAY_MAX_MS,
};
