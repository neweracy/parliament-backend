"""Lightweight circuit breaker for external service calls (Bedrock, embeddings).

Tracks consecutive failures and short-circuits to a fallback when the failure
count exceeds a threshold. Resets after a configurable recovery window.

This prevents cascading latency when Bedrock is down: instead of waiting for
the timeout on every request, the breaker opens after a few failures and
subsequent calls fail immediately with a structured response until the recovery
window elapses.

Requirements: Production resilience for RAG answering and ingestion.
"""

from __future__ import annotations

import time
from enum import Enum

import structlog

logger = structlog.get_logger("rag.circuit_breaker")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation — calls pass through
    OPEN = "open"  # Failing — calls are rejected immediately
    HALF_OPEN = "half_open"  # Recovery probe — one call allowed


class CircuitBreaker:
    """Simple circuit breaker with configurable failure threshold and recovery.

    Usage::

        breaker = CircuitBreaker(name="bedrock", failure_threshold=5, recovery_timeout_s=60)

        if not breaker.allow_request():
            return fallback_response()

        try:
            result = await external_call()
            breaker.record_success()
            return result
        except Exception:
            breaker.record_failure()
            return fallback_response()

    Thread-safety: This implementation uses simple counters without locks.
    In an async single-threaded event loop (uvicorn), this is safe. For
    multi-worker deployments, each worker has its own breaker instance.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 60.0,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        """Current circuit state, accounting for recovery timeout."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout_s:
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def is_open(self) -> bool:
        """True when the circuit is open (rejecting calls)."""
        return self.state == CircuitState.OPEN

    def allow_request(self) -> bool:
        """Check whether a request should be allowed through.

        Returns True for CLOSED and HALF_OPEN states, False for OPEN.
        """
        current_state = self.state

        if current_state == CircuitState.CLOSED:
            return True

        if current_state == CircuitState.HALF_OPEN:
            # Allow one probe request
            logger.info(
                "circuit_breaker.half_open_probe",
                name=self._name,
            )
            return True

        # OPEN — reject
        logger.debug(
            "circuit_breaker.rejected",
            name=self._name,
            failures=self._failure_count,
            recovery_remaining_s=round(
                self._recovery_timeout_s - (time.monotonic() - self._opened_at), 1
            ),
        )
        return False

    def record_success(self) -> None:
        """Record a successful call — resets the breaker to CLOSED."""
        if self._state != CircuitState.CLOSED:
            logger.info(
                "circuit_breaker.closed",
                name=self._name,
                previous_failures=self._failure_count,
            )
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call — may trip the breaker to OPEN."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._failure_count >= self._failure_threshold:
            if self._state != CircuitState.OPEN:
                logger.warning(
                    "circuit_breaker.opened",
                    name=self._name,
                    failures=self._failure_count,
                    recovery_timeout_s=self._recovery_timeout_s,
                )
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
