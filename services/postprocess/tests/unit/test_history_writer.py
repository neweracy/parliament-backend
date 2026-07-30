"""Unit tests for app/history/writer.py — CorrectionHistoryWriter.

Tests verify:
- enqueue drops records without blocking when the queue is full (Requirement 13.9)
- dropped_count increments on overflow
- The background writer persists batched records via SQLAlchemy
- A failing database write logs an error but does not crash the writer
- Graceful shutdown drains remaining items

Requirements: 13.9, 15.1
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.history.writer import CorrectionHistoryWriter, HistoryRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(suffix: str = "1") -> HistoryRecord:
    """Create a test HistoryRecord."""
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
    """Create a mock async session factory that tracks execute calls."""
    session = AsyncMock()

    # session.begin() must return an async context manager synchronously
    # (not a coroutine). Using MagicMock for begin so calling it returns
    # the context manager directly.
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    # factory() is an async context manager yielding the session
    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = factory_cm

    return factory, session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHistoryRecord:
    """HistoryRecord dataclass tests."""

    def test_record_is_frozen(self):
        record = _make_record()
        with pytest.raises(AttributeError):
            record.original = "changed"  # type: ignore[misc]

    def test_record_fields(self):
        record = _make_record("x")
        assert record.correlation_id == "corr-x"
        assert record.text_hash == "hash-x"
        assert record.original == "original-x"
        assert record.corrected == "corrected-x"
        assert record.strategy == "exact"
        assert record.confidence == 0.95
        assert record.entity_kind == "person"
        assert record.entity_type == "mp"
        assert record.model_version == "rule-based"
        assert record.created_at == datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)


class TestEnqueueOverflow:
    """enqueue drops on overflow without blocking (Requirement 13.9)."""

    def test_enqueue_drops_when_full(self):
        factory, _ = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=2)

        # Fill the queue
        writer.enqueue(_make_record("1"))
        writer.enqueue(_make_record("2"))
        assert writer.dropped_count == 0

        # Overflow — should drop without blocking
        writer.enqueue(_make_record("3"))
        assert writer.dropped_count == 1

        writer.enqueue(_make_record("4"))
        assert writer.dropped_count == 2

    def test_enqueue_never_blocks(self):
        """enqueue returns immediately even when full — no latency added."""
        factory, _ = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=1)

        writer.enqueue(_make_record("1"))

        # This should return instantly, not block
        import time

        start = time.monotonic()
        for i in range(100):
            writer.enqueue(_make_record(str(i)))
        elapsed = time.monotonic() - start

        # Should be well under 100ms for 100 calls
        assert elapsed < 0.1
        assert writer.dropped_count == 100


class TestBackgroundWriter:
    """Background writer persists records via SQLAlchemy."""

    @pytest.mark.asyncio
    async def test_writer_persists_records(self):
        factory, session = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=100)
        writer.start()

        # Enqueue a few records
        writer.enqueue(_make_record("a"))
        writer.enqueue(_make_record("b"))

        # Give the writer time to process
        await asyncio.sleep(0.1)
        await writer.stop()

        # The session should have had execute called with INSERT
        assert session.execute.called
        call_args = session.execute.call_args
        # First arg is the text SQL statement
        sql = str(call_args[0][0])
        assert "INSERT INTO correction_history" in sql

    @pytest.mark.asyncio
    async def test_writer_handles_db_error_gracefully(self):
        """A failing database write logs an error but does not crash the writer."""
        factory, session = _mock_session_factory()
        session.execute.side_effect = RuntimeError("DB connection lost")

        writer = CorrectionHistoryWriter(factory, max_queue_size=100)
        writer.start()

        writer.enqueue(_make_record("fail"))

        # Give the writer time to attempt the write and recover
        await asyncio.sleep(0.2)

        # Writer should still be running (not crashed)
        assert not writer._task.done()

        # Enqueue another record — writer is still alive
        writer.enqueue(_make_record("after-fail"))

        await writer.stop()

    @pytest.mark.asyncio
    async def test_stop_drains_remaining(self):
        """Graceful shutdown drains remaining items in the queue."""
        factory, session = _mock_session_factory()

        # Use a writer that we won't start — we'll manually verify drain
        writer = CorrectionHistoryWriter(factory, max_queue_size=100)

        # Don't start the writer — records stay in queue
        writer.enqueue(_make_record("drain-1"))
        writer.enqueue(_make_record("drain-2"))

        # Start and immediately stop — should drain
        writer.start()
        await asyncio.sleep(0.05)
        await writer.stop()

        # Records should have been persisted during drain
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_dropped_count_property(self):
        factory, _ = _mock_session_factory()
        writer = CorrectionHistoryWriter(factory, max_queue_size=1)

        writer.enqueue(_make_record("1"))
        writer.enqueue(_make_record("2"))  # dropped
        writer.enqueue(_make_record("3"))  # dropped

        assert writer.dropped_count == 2
