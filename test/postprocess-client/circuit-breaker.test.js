const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");

const {
  _isCircuitOpen,
  _recordResult,
  _resetCircuitBreaker,
  _breakerState,
  POSTPROCESS_BREAKER_THRESHOLD,
  POSTPROCESS_BREAKER_COOLDOWN_MS,
} = require("../../lib/postprocess-client");

describe("circuit breaker - state machine", () => {
  beforeEach(() => {
    _resetCircuitBreaker();
  });

  it("starts in closed state", () => {
    assert.equal(_breakerState.state, "closed");
    assert.equal(_breakerState.consecutiveFailures, 0);
    assert.equal(_breakerState.openedAt, null);
  });

  it("returns false (circuit closed) in initial state", () => {
    assert.equal(_isCircuitOpen(), false);
  });

  it("stays closed after fewer than threshold consecutive failures", () => {
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD - 1; i++) {
      _recordResult(false);
    }
    assert.equal(_breakerState.state, "closed");
    assert.equal(_isCircuitOpen(), false);
  });

  it("opens after exactly threshold consecutive failures", () => {
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    assert.equal(_breakerState.state, "open");
    assert.notEqual(_breakerState.openedAt, null);
  });

  it("returns true (circuit open) after tripping", () => {
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    assert.equal(_isCircuitOpen(), true);
  });

  it("resets consecutive failures on success", () => {
    _recordResult(false);
    _recordResult(false);
    _recordResult(true);
    assert.equal(_breakerState.consecutiveFailures, 0);
    assert.equal(_breakerState.state, "closed");
  });

  it("does not open if a success interrupts the failure streak", () => {
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD - 1; i++) {
      _recordResult(false);
    }
    _recordResult(true); // resets
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD - 1; i++) {
      _recordResult(false);
    }
    assert.equal(_breakerState.state, "closed");
  });
});

describe("circuit breaker - cooldown and half_open transitions", () => {
  beforeEach(() => {
    _resetCircuitBreaker();
  });

  it("transitions to half_open after cooldown elapses", () => {
    // Trip the breaker
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    assert.equal(_breakerState.state, "open");

    // Simulate cooldown elapsed by backdating openedAt
    _breakerState.openedAt = Date.now() - POSTPROCESS_BREAKER_COOLDOWN_MS - 1;

    // The next check should transition to half_open and admit the probe
    assert.equal(_isCircuitOpen(), false);
    assert.equal(_breakerState.state, "half_open");
  });

  it("blocks concurrent callers while in half_open", () => {
    // Trip the breaker and simulate cooldown
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    _breakerState.openedAt = Date.now() - POSTPROCESS_BREAKER_COOLDOWN_MS - 1;

    // First call transitions to half_open and is admitted
    assert.equal(_isCircuitOpen(), false);
    assert.equal(_breakerState.state, "half_open");

    // Subsequent calls while in half_open see it as open
    assert.equal(_isCircuitOpen(), true);
    assert.equal(_isCircuitOpen(), true);
  });

  it("closes the breaker when a probe succeeds", () => {
    // Trip and transition to half_open
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    _breakerState.openedAt = Date.now() - POSTPROCESS_BREAKER_COOLDOWN_MS - 1;
    _isCircuitOpen(); // transitions to half_open

    // Probe succeeds
    _recordResult(true);
    assert.equal(_breakerState.state, "closed");
    assert.equal(_breakerState.consecutiveFailures, 0);
    assert.equal(_breakerState.openedAt, null);
    assert.equal(_isCircuitOpen(), false);
  });

  it("reopens the breaker when a probe fails", () => {
    // Trip and transition to half_open
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    _breakerState.openedAt = Date.now() - POSTPROCESS_BREAKER_COOLDOWN_MS - 1;
    _isCircuitOpen(); // transitions to half_open

    // Probe fails — breaker reopens (threshold is already met since counter increments)
    _recordResult(false);
    assert.equal(_breakerState.state, "open");
    assert.notEqual(_breakerState.openedAt, null);
  });

  it("stays open before cooldown elapses", () => {
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    // openedAt is recent (just set by _recordResult), so cooldown has not elapsed
    assert.equal(_isCircuitOpen(), true);
    assert.equal(_breakerState.state, "open");
  });
});

describe("circuit breaker - _resetCircuitBreaker", () => {
  it("resets all state to initial values", () => {
    // Trip the breaker
    for (let i = 0; i < POSTPROCESS_BREAKER_THRESHOLD; i++) {
      _recordResult(false);
    }
    assert.equal(_breakerState.state, "open");

    _resetCircuitBreaker();

    assert.equal(_breakerState.state, "closed");
    assert.equal(_breakerState.consecutiveFailures, 0);
    assert.equal(_breakerState.openedAt, null);
    assert.equal(_isCircuitOpen(), false);
  });
});

describe("circuit breaker - configuration defaults", () => {
  it("threshold defaults to 5", () => {
    assert.equal(POSTPROCESS_BREAKER_THRESHOLD, 5);
  });

  it("cooldown defaults to 30000 ms", () => {
    assert.equal(POSTPROCESS_BREAKER_COOLDOWN_MS, 30000);
  });
});
