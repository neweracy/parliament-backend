"""Unit tests for observability: logging redaction, metric dimensions, and history overflow.

Tests verify:
- Service_Token never appears in a log record (Requirement 13.10)
- Metric dimensions contain no transcript text (Requirement 13.7)
- History overflow drops instead of blocking (Requirement 13.9)

Requirements: 13.7, 13.9, 13.10, 15.1
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.history.writer import CorrectionHistoryWriter, HistoryRecord
from app.obs.logging import _make_redaction_processor
from app.obs import metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_TOKEN = "super-secret-token-abc123"


def _settings_with_token(token: str = _TEST_TOKEN) -> Settings:
    """Create a minimal Settings object with a known service_token."""
    return Settings(
        service_token=token,
        database_url="postgresql://test:test@localhost/test",
    )


def _make_history_record(suffix: str = "1") -> HistoryRecord:
    """Create a test HistoryRecord."""
    from datetime import datetime, timezone

    return HistoryRecord(
        correlation_id=f"corr-{suffix}",
        text_hash=f"hash-{suffix}",
        original=f"original-{suffix}",
        corrected=f"corrected-{suffix}",
        strategy="exact",
        confidence=0.95,
        entity_kind="person",
        entity_type="mp",
        model_version="rule-based",
        created_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc),
    )


def _mock_session_factory():
    """Create a mock async session factory."""
    from unittest.mock import AsyncMock

    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = factory_cm
    return factory, session


# ---------------------------------------------------------------------------
# 1. Logging Redaction Tests (Requirement 13.10)
# ---------------------------------------------------------------------------


class TestLoggingRedaction:
    """Service_Token never appears in a log record."""

    def test_token_in_message_is_redacted(self):
        """Token value appearing in a log event message field is replaced with ***."""
        settings = _settings_with_token()
        processor = _make_redaction_processor(settings)

        event_dict = {"event": f"Auth succeeded with token {_TEST_TOKEN}"}
        result = processor(None, "info", event_dict)

        assert _TEST_TOKEN not in result["event"]
        assert result["event"] == "***"

    def test_token_in_arbitrary_field_is_redacted(self):
        """Token value appearing in any field value is replaced with ***."""
        settings = _settings_with_token()
        processor = _make_redaction_processor(settings)

        event_dict = {
            "event": "request.completed",
            "token_value": _TEST_TOKEN,
            "message": f"Bearer {_TEST_TOKEN}",
        }
        result = processor(None, "info", event_dict)

        assert _TEST_TOKEN not in result["token_value"]
        assert result["token_value"] == "***"
        assert _TEST_TOKEN not in result["message"]
        assert result["message"] == "***"

    def test_authorization_header_is_redacted(self):
        """The 'authorization' key always has its value replaced with ***."""
        settings = _settings_with_token()
        processor = _make_redaction_processor(settings)

        event_dict = {
            "event": "request.incoming",
            "authorization": f"Bearer {_TEST_TOKEN}",
        }
        result = processor(None, "info", event_dict)

        assert result["authorization"] == "***"

    def test_authorization_case_insensitive(self):
        """Authorization key redaction is case-insensitive."""
        settings = _settings_with_token()
        processor = _make_redaction_processor(settings)

        event_dict = {
            "event": "request",
            "Authorization": "Bearer some-other-value",
        }
        result = processor(None, "info", event_dict)

        assert result["Authorization"] == "***"

    def test_non_sensitive_fields_pass_through(self):
        """Fields not containing the token or named 'authorization' are untouched."""
        settings = _settings_with_token()
        processor = _make_redaction_processor(settings)

        event_dict = {
            "event": "request.completed",
            "path": "/v1/postprocess",
            "status": 200,
            "latency_ms": 42.5,
        }
        result = processor(None, "info", event_dict)

        assert result["event"] == "request.completed"
        assert result["path"] == "/v1/postprocess"
        assert result["status"] == 200
        assert result["latency_ms"] == 42.5

    def test_token_substring_in_larger_string_is_redacted(self):
        """A field containing the token as a substring is still redacted."""
        settings = _settings_with_token()
        processor = _make_redaction_processor(settings)

        event_dict = {
            "event": "debug",
            "header": f"Bearer {_TEST_TOKEN} extra-stuff",
        }
        result = processor(None, "debug", event_dict)

        assert _TEST_TOKEN not in result["header"]
        assert result["header"] == "***"

    def test_none_token_does_not_redact_values(self):
        """When service_token is None, no value-based redaction occurs (but auth key still redacted)."""
        settings = Settings(
            service_token=None,
            database_url="postgresql://test:test@localhost/test",
        )
        processor = _make_redaction_processor(settings)

        event_dict = {
            "event": "some message",
            "data": "arbitrary-value",
            "authorization": "Bearer secret",
        }
        result = processor(None, "info", event_dict)

        # Non-auth fields pass through when no token is set
        assert result["data"] == "arbitrary-value"
        # Authorization key still gets redacted
        assert result["authorization"] == "***"


# ---------------------------------------------------------------------------
# 2. Metric Dimensions Tests (Requirement 13.7)
# ---------------------------------------------------------------------------

# The closed-set dimension keys allowed in metric dimensions
_ALLOWED_DIMENSION_KEYS = frozenset({"strategy", "error_code", "llm_status", "path", "reason", "service"})


class TestMetricDimensions:
    """Metric dimensions contain no transcript text — only closed-set keys."""

    def _capture_emit_args(self):
        """Patch the internal _emit to capture dimension dicts."""
        captured = []
        original_emit = metrics._emit

        def _capturing_emit(metric_name, value, unit, dimensions=None):
            captured.append({
                "metric_name": metric_name,
                "value": value,
                "unit": unit,
                "dimensions": dimensions or {},
            })

        return captured, _capturing_emit

    def test_corrections_applied_uses_closed_strategy_dimension(self):
        """emit_corrections_applied uses only the 'strategy' dimension key."""
        captured, fake_emit = self._capture_emit_args()

        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_corrections_applied("exact", 3)

        assert len(captured) == 1
        dims = captured[0]["dimensions"]
        assert set(dims.keys()) <= _ALLOWED_DIMENSION_KEYS
        assert dims["strategy"] == "exact"

    def test_emit_error_uses_closed_error_code_dimension(self):
        """emit_error uses only the 'error_code' dimension key."""
        captured, fake_emit = self._capture_emit_args()

        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_error("INVALID_REQUEST")

        assert len(captured) == 1
        dims = captured[0]["dimensions"]
        assert set(dims.keys()) <= _ALLOWED_DIMENSION_KEYS
        assert dims["error_code"] == "INVALID_REQUEST"

    def test_llm_latency_uses_closed_llm_status_dimension(self):
        """emit_llm_latency uses only the 'llm_status' dimension key."""
        captured, fake_emit = self._capture_emit_args()

        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_llm_latency(150.0, "ok")

        assert len(captured) == 1
        dims = captured[0]["dimensions"]
        assert set(dims.keys()) <= _ALLOWED_DIMENSION_KEYS
        assert dims["llm_status"] == "ok"

    def test_handler_latency_uses_closed_path_dimension(self):
        """emit_handler_latency uses only the 'path' dimension key."""
        captured, fake_emit = self._capture_emit_args()

        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_handler_latency(200.0, "/v1/postprocess")

        assert len(captured) == 1
        dims = captured[0]["dimensions"]
        assert set(dims.keys()) <= _ALLOWED_DIMENSION_KEYS
        assert dims["path"] == "/v1/postprocess"

    def test_llm_chunks_dropped_uses_closed_reason_dimension(self):
        """emit_llm_chunks_dropped uses only the 'reason' dimension key."""
        captured, fake_emit = self._capture_emit_args()

        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_llm_chunks_dropped("timeout", 2)

        assert len(captured) == 1
        dims = captured[0]["dimensions"]
        assert set(dims.keys()) <= _ALLOWED_DIMENSION_KEYS
        assert dims["reason"] == "timeout"

    def test_no_dimension_contains_transcript_text(self):
        """No emit_* function passes arbitrary text (transcript content) into dimensions."""
        captured, fake_emit = self._capture_emit_args()

        # Call every emit function with representative values
        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_corrections_applied("fuzzy", 1)
            metrics.emit_rule_latency(50.0)
            metrics.emit_llm_latency(100.0, "partial")
            metrics.emit_handler_latency(200.0, "/v1/postprocess")
            metrics.emit_dataset_cache_records(5000)
            metrics.emit_dataset_cache_age(120.5)
            metrics.emit_error("PIPELINE_FAILED")
            metrics.emit_llm_prompt_tokens(1500)
            metrics.emit_dataset_refresh_failure()
            metrics.emit_llm_chunks_dropped("error", 1)

        # Verify all dimension values are from the closed set
        for call in captured:
            dims = call["dimensions"]
            # All dimension keys must be from the closed set
            assert set(dims.keys()) <= _ALLOWED_DIMENSION_KEYS, (
                f"Metric {call['metric_name']} has unexpected dimension keys: "
                f"{set(dims.keys()) - _ALLOWED_DIMENSION_KEYS}"
            )
            # No dimension value should be longer than a reasonable bound
            # (transcript text would be much longer, entity names too)
            for key, value in dims.items():
                assert len(str(value)) <= 30, (
                    f"Metric {call['metric_name']} dimension {key}={value!r} "
                    f"looks like it might contain transcript text"
                )

    def test_dimensionless_metrics_emit_no_dimensions(self):
        """Metrics without dimensions (rule_latency, cache_records, etc.) emit empty dicts."""
        captured, fake_emit = self._capture_emit_args()

        with patch.object(metrics, "_emit", side_effect=fake_emit):
            metrics.emit_rule_latency(50.0)
            metrics.emit_dataset_cache_records(5000)
            metrics.emit_dataset_cache_age(120.5)
            metrics.emit_llm_prompt_tokens(1500)
            metrics.emit_dataset_refresh_failure()

        for call in captured:
            assert call["dimensions"] == {}, (
                f"Metric {call['metric_name']} should have no dimensions "
                f"but has: {call['dimensions']}"
            )


# ---------------------------------------------------------------------------
# 3. History Overflow Tests (Requirement 13.9)
# ---------------------------------------------------------------------------


class TestHistoryOverflow:
    """History overflow drops instead of blocking."""

    def test_enqueue_returns_in_bounded_time_when_full(self):
        """enqueue completes in bounded time even with a completely full queue."""
        factory, _ = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=5)

        # Fill the queue completely
        for i in range(5):
            writer.enqueue(_make_history_record(str(i)))

        # Now time how long additional enqueues take
        start = time.monotonic()
        for i in range(1000):
            writer.enqueue(_make_history_record(f"overflow-{i}"))
        elapsed = time.monotonic() - start

        # 1000 enqueue calls on a full queue must complete in well under 1 second
        # (they should be essentially instant — just incrementing a counter)
        assert elapsed < 0.5, (
            f"enqueue took {elapsed:.3f}s for 1000 calls on a full queue — "
            f"this suggests blocking behaviour"
        )

    def test_dropped_count_increments_on_overflow(self):
        """dropped_count tracks exactly how many records were dropped."""
        factory, _ = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=3)

        # Fill
        for i in range(3):
            writer.enqueue(_make_history_record(str(i)))
        assert writer.dropped_count == 0

        # Overflow by 5
        for i in range(5):
            writer.enqueue(_make_history_record(f"drop-{i}"))

        assert writer.dropped_count == 5

    @pytest.mark.asyncio
    async def test_enqueue_does_not_block_event_loop(self):
        """enqueue on a full queue does not block the asyncio event loop."""
        factory, _ = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=2)

        # Fill the queue
        writer.enqueue(_make_history_record("1"))
        writer.enqueue(_make_history_record("2"))

        # Run enqueue inside the event loop — should not block
        start = time.monotonic()
        for i in range(100):
            writer.enqueue(_make_history_record(f"async-{i}"))
        elapsed = time.monotonic() - start

        assert elapsed < 0.1
        assert writer.dropped_count == 100
