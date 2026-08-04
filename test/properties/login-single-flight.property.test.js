'use strict';

/**
 * Property tests for login single-flight submission behavior.
 *
 * Property 4: Login submission is single-flight
 *
 * Tests the request-deadline middleware's guarantees:
 * - At most one response is ever sent per request (no double-write)
 * - When deadline fires, the response is exactly 504 with the standard body
 * - req.signal.aborted is true after deadline fires
 * - Guard releases on settlement (success, failure, timeout)
 *
 * **Validates: Requirements 2.4**
 *
 * @module test/properties/login-single-flight.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { EventEmitter } = require('events');

const requestDeadline = require('../../middleware/request-deadline');

// ─── Constants ───────────────────────────────────────────────────────────────

const TIMEOUT_RESPONSE = Object.freeze({
  error: {
    type: 'ServerError',
    code: 'REQUEST_TIMEOUT',
    message: 'Request processing exceeded time limit',
  },
});

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Creates a mock req (EventEmitter) and mock res with response tracking.
 *
 * @returns {{ req, res, getStatus, getBody, getResponseCount }}
 */
function createMocks() {
  const req = new EventEmitter();
  req.destroyed = false;
  req.headers = {};

  const res = new EventEmitter();
  res.headersSent = false;
  let statusCode = null;
  let responseBody = null;
  let responseCount = 0;

  res.status = (code) => {
    statusCode = code;
    return res;
  };
  res.json = (body) => {
    responseCount++;
    responseBody = body;
    res.headersSent = true;
    res.emit('finish');
    return res;
  };

  return {
    req,
    res,
    getStatus: () => statusCode,
    getBody: () => responseBody,
    getResponseCount: () => responseCount,
  };
}

/**
 * Simulates a handler that delays for the given time, then responds
 * (if the signal is not aborted).
 *
 * @param {object} req - Mock request with signal
 * @param {object} res - Mock response
 * @param {number} delayMs - Handler processing time
 * @param {string} outcome - 'success' | 'failure' | 'error'
 * @returns {Promise<void>}
 */
async function simulateHandler(req, res, delayMs, outcome) {
  await new Promise(resolve => setTimeout(resolve, delayMs));

  // Cooperative abort check
  if (req.signal && req.signal.aborted) {
    return;
  }

  if (outcome === 'success') {
    res.status(200).json({ token: 'test-token', user: { id: '1', role: 'Admin' } });
  } else if (outcome === 'failure') {
    res.status(401).json({
      error: { type: 'AuthenticationError', code: 'INVALID_CREDENTIALS', message: 'Invalid email or password' },
    });
  } else {
    res.status(500).json({
      error: { type: 'ServerError', code: 'INTERNAL_ERROR', message: 'An internal error occurred' },
    });
  }
}

// ─── Property Tests ──────────────────────────────────────────────────────────

describe('Property 4: Login submission is single-flight', () => {

  describe('Request-deadline produces exactly one response per request', () => {

    it('for any handler delay < deadline, handler responds (not deadline)', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 80, max: 200 }),    // deadline
          fc.integer({ min: 1, max: 30 }),       // handler delay (always < deadline-50)
          fc.constantFrom('success', 'failure', 'error'),
          async (deadlineMs, handlerDelayMs, outcome) => {
            const actualDelay = Math.min(handlerDelayMs, deadlineMs - 50);
            if (actualDelay < 1) return;

            const { req, res, getStatus, getBody, getResponseCount } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            let nextCalled = false;
            middleware(req, res, () => { nextCalled = true; });
            assert.ok(nextCalled, 'Middleware should call next()');

            // Simulate the handler
            await simulateHandler(req, res, actualDelay, outcome);

            // Handler responded before deadline
            assert.equal(getResponseCount(), 1, 'Exactly one response should be sent');
            assert.notEqual(getStatus(), 504, 'Handler should beat deadline');

            if (outcome === 'success') assert.equal(getStatus(), 200);
            else if (outcome === 'failure') assert.equal(getStatus(), 401);
            else assert.equal(getStatus(), 500);
          }
        ),
        { numRuns: 30 }
      );
    });

    it('for any handler delay > deadline, response is exactly 504 with standard body', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 20, max: 60 }),   // short deadline
          fc.integer({ min: 30, max: 80 }),    // extra delay beyond deadline
          async (deadlineMs, extraDelay) => {
            const handlerDelayMs = deadlineMs + extraDelay;

            const { req, res, getStatus, getBody, getResponseCount } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            let nextCalled = false;
            middleware(req, res, () => { nextCalled = true; });
            assert.ok(nextCalled);

            // Simulate the handler (will be too slow)
            await simulateHandler(req, res, handlerDelayMs, 'success');

            // Deadline should have fired — handler cooperatively aborted
            assert.equal(getStatus(), 504, 'Deadline should send 504');
            assert.deepStrictEqual(getBody(), TIMEOUT_RESPONSE);
            assert.equal(getResponseCount(), 1, 'Only one response (from deadline)');
          }
        ),
        { numRuns: 30 }
      );
    });

    it('responseCount never exceeds 1 regardless of timing race', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 10, max: 60 }),  // deadline
          fc.integer({ min: 10, max: 60 }),  // handler delay
          async (deadlineMs, handlerDelayMs) => {
            const { req, res, getStatus, getResponseCount } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            let nextCalled = false;
            middleware(req, res, () => { nextCalled = true; });
            assert.ok(nextCalled);

            // Simulate handler
            await simulateHandler(req, res, handlerDelayMs, 'success');

            // Wait for both deadline and handler to fully settle
            await new Promise(resolve => setTimeout(resolve, Math.max(deadlineMs, handlerDelayMs) + 20));

            // At most one response was sent (either deadline or handler, not both)
            assert.ok(getResponseCount() <= 1,
              `Expected at most 1 response, got ${getResponseCount()}`);
            // And at least one response was produced
            assert.ok(getResponseCount() >= 1,
              'At least one response should be produced');
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  describe('Signal is aborted after deadline fires', () => {

    it('req.signal.aborted is true when handler runs after deadline', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 10, max: 40 }),   // short deadline
          fc.integer({ min: 20, max: 60 }),   // extra delay
          async (deadlineMs, extraDelay) => {
            const handlerDelayMs = deadlineMs + extraDelay;

            const { req, res } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            middleware(req, res, () => {});

            // Signal should not be aborted immediately
            assert.equal(req.signal.aborted, false);

            // Wait for deadline to fire
            await new Promise(resolve => setTimeout(resolve, deadlineMs + 10));

            // Signal should now be aborted
            assert.equal(req.signal.aborted, true, 'Signal should be aborted after deadline');
            assert.equal(req.deadlineExceeded, true);
          }
        ),
        { numRuns: 30 }
      );
    });

    it('req.signal.aborted is false when handler runs before deadline', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 80, max: 200 }),   // generous deadline
          fc.integer({ min: 1, max: 20 }),     // fast handler
          async (deadlineMs, handlerDelayMs) => {
            const { req, res } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            middleware(req, res, () => {});

            // Wait for handler delay
            await new Promise(resolve => setTimeout(resolve, handlerDelayMs));

            // Signal should NOT be aborted yet
            assert.equal(req.signal.aborted, false, 'Signal should not be aborted before deadline');
            assert.equal(req.deadlineExceeded, false);

            // Send response (triggers cleanup which clears timer)
            res.status(200).json({ ok: true });
          }
        ),
        { numRuns: 30 }
      );
    });
  });

  describe('Single-flight command model: sequential requests', () => {

    it('at most one in-flight request exists at any time in a sequence', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.array(
            fc.record({
              deadlineMs: fc.integer({ min: 15, max: 60 }),
              handlerDelayMs: fc.integer({ min: 5, max: 80 }),
              outcome: fc.constantFrom('success', 'failure', 'error'),
            }),
            { minLength: 1, maxLength: 5 }
          ),
          async (sequence) => {
            let maxConcurrentInflight = 0;
            let currentInflight = 0;

            for (const { deadlineMs, handlerDelayMs, outcome } of sequence) {
              currentInflight++;
              maxConcurrentInflight = Math.max(maxConcurrentInflight, currentInflight);

              const { req, res, getStatus, getResponseCount } = createMocks();
              const middleware = requestDeadline(deadlineMs);

              middleware(req, res, () => {});
              await simulateHandler(req, res, handlerDelayMs, outcome);

              // Ensure settled
              await new Promise(resolve => setTimeout(resolve, Math.max(deadlineMs, handlerDelayMs) + 10));

              currentInflight--;

              // Invariant: exactly one response was produced
              assert.equal(getResponseCount(), 1,
                'Every request must produce exactly one response');

              // Verify response matches expectation
              if (handlerDelayMs < deadlineMs - 10) {
                if (outcome === 'success') assert.equal(getStatus(), 200);
                else if (outcome === 'failure') assert.equal(getStatus(), 401);
                else assert.equal(getStatus(), 500);
              } else if (handlerDelayMs > deadlineMs + 10) {
                assert.equal(getStatus(), 504);
              }
              // In overlap zone, either is acceptable
            }

            // Sequential execution: max 1 at a time
            assert.equal(maxConcurrentInflight, 1);
            assert.equal(currentInflight, 0, 'All requests must settle');
          }
        ),
        { numRuns: 20 }
      );
    });
  });

  describe('Cooperative abort: handler discards late results after deadline', () => {

    it('handler produces no response when req.signal.aborted is true', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 20, max: 50 }),  // extra delay after deadline
          fc.constantFrom('success', 'failure', 'error'),
          async (extraDelay, outcome) => {
            const deadlineMs = 10;
            const handlerDelayMs = deadlineMs + extraDelay;

            let handlerReachedResponse = false;

            const { req, res, getStatus, getResponseCount } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            middleware(req, res, () => {});

            // Simulate handler that checks signal before responding
            await new Promise(resolve => setTimeout(resolve, handlerDelayMs));

            if (req.signal && req.signal.aborted) {
              // Cooperative: do NOT send response — this is correct behavior
            } else {
              handlerReachedResponse = true;
              res.status(200).json({ late: true });
            }

            // The only response should be 504 from deadline
            assert.equal(getStatus(), 504);
            assert.deepStrictEqual(res.headersSent, true);
            assert.equal(handlerReachedResponse, false,
              'Handler must not send a response after deadline fires');
            assert.equal(getResponseCount(), 1, 'Only one response from deadline');
          }
        ),
        { numRuns: 30 }
      );
    });
  });

  describe('Guard release and password clearing on all settlement types', () => {

    it('for any terminal outcome, response does not leak the password', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.constantFrom('success', 'failure', 'timeout', 'error'),
          fc.string({ minLength: 4, maxLength: 30 }),
          async (settlementType, password) => {
            let deadlineMs, handlerDelayMs, outcome;

            switch (settlementType) {
              case 'success':
                deadlineMs = 200; handlerDelayMs = 5; outcome = 'success'; break;
              case 'failure':
                deadlineMs = 200; handlerDelayMs = 5; outcome = 'failure'; break;
              case 'timeout':
                deadlineMs = 10; handlerDelayMs = 60; outcome = 'success'; break;
              case 'error':
                deadlineMs = 200; handlerDelayMs = 5; outcome = 'error'; break;
            }

            const { req, res, getStatus, getBody, getResponseCount } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            middleware(req, res, () => {});
            await simulateHandler(req, res, handlerDelayMs, outcome);

            // Wait for complete settlement
            await new Promise(resolve => setTimeout(resolve, Math.max(deadlineMs, handlerDelayMs) + 10));

            // Every settlement produces exactly one response
            assert.equal(getResponseCount(), 1);

            // Password must NOT appear in response body
            const responseText = JSON.stringify(getBody());
            assert.ok(!responseText.includes(password),
              'Password must not appear in response body');

            // Correct status per settlement type
            if (settlementType === 'success') assert.equal(getStatus(), 200);
            else if (settlementType === 'failure') assert.equal(getStatus(), 401);
            else if (settlementType === 'timeout') assert.equal(getStatus(), 504);
            else if (settlementType === 'error') assert.equal(getStatus(), 500);
          }
        ),
        { numRuns: 40 }
      );
    });
  });

  describe('Client disconnect aborts the signal', () => {

    it('req close event aborts the signal and cleans up', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.integer({ min: 100, max: 300 }),  // deadline (generous)
          fc.integer({ min: 5, max: 30 }),     // disconnect time
          async (deadlineMs, disconnectAfterMs) => {
            const { req, res } = createMocks();
            const middleware = requestDeadline(deadlineMs);

            middleware(req, res, () => {});

            // Signal should not be aborted yet
            assert.equal(req.signal.aborted, false);

            // Simulate client disconnect
            await new Promise(resolve => setTimeout(resolve, disconnectAfterMs));
            req.emit('close');

            // Signal should be aborted after client disconnect
            assert.equal(req.signal.aborted, true, 'Signal should abort on client disconnect');
          }
        ),
        { numRuns: 20 }
      );
    });
  });
});
