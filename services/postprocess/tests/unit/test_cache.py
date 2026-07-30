"""Unit tests for app/datasets/cache.py — DatasetCache refresh behaviour.

Tests verify:
- Refresh failure retains the previous snapshot (Requirement 9.6)
- A new record appears after one refresh without a restart (Requirement 9.7)
- Index build stays under the load budget for a 5000-record fixture (Requirement 10.6)
- Startup failure leaves snapshot as None and retries (Requirement 9.9, 9.10)
- reload() method updates the snapshot on demand
- get_snapshot() returns None before first load, then the snapshot after

Requirements: 9.6, 9.7, 9.9, 10.6, 15.1
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    canonical: str,
    entity_kind: EntityKind = EntityKind.location,
    entity_type: EntityType = EntityType.city,
    aliases: list[str] | None = None,
    source: str = "canonical",
    source_rank: int = 1,
) -> EntityRecord:
    """Create an EntityRecord for testing."""
    return EntityRecord(
        canonical=canonical,
        entity_kind=entity_kind,
        entity_type=entity_type,
        aliases=aliases or [],
        source=source,
        source_rank=source_rank,
    )


def _make_records_batch(count: int, alias_count: int = 3) -> list[EntityRecord]:
    """Generate a batch of EntityRecords for performance testing."""
    records = []
    for i in range(count):
        aliases = [f"alias_{i}_{j}" for j in range(alias_count)]
        kind = [EntityKind.location, EntityKind.person, EntityKind.party][i % 3]
        etype = [EntityType.city, EntityType.minister, EntityType.party][i % 3]
        records.append(
            EntityRecord(
                canonical=f"Entity {i} Canonical Name",
                entity_kind=kind,
                entity_type=etype,
                aliases=aliases,
                source="test_source",
                source_rank=i % 5,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Test: Refresh failure retains the previous snapshot (Requirement 9.6)
# ---------------------------------------------------------------------------


class TestRefreshFailureRetainsPreviousSnapshot:
    """When _build_snapshot raises or returns None during a refresh,
    the _snapshot reference remains unchanged."""

    async def test_refresh_failure_keeps_existing_snapshot(self):
        """After a successful load, a failing refresh does not overwrite the snapshot."""
        initial_records = [_make_record("Accra"), _make_record("Kumasi")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        # First build succeeds
        first_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(initial_records),
            record_count=2,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(initial_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )
        cache._snapshot = first_snapshot

        # Patch _build_snapshot to raise an exception (simulating DB failure)
        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = Exception("Database connection refused")

            # Manually invoke _try_load (which the refresh loop calls)
            result = await cache._try_load()

            assert result is False
            # Snapshot remains unchanged
            assert cache._snapshot is first_snapshot
            assert cache._snapshot.record_count == 2

    async def test_refresh_returning_none_keeps_existing_snapshot(self):
        """When _build_snapshot returns None, the snapshot stays the same."""
        initial_records = [_make_record("Tamale")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        first_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(initial_records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(initial_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )
        cache._snapshot = first_snapshot

        # Patch _build_snapshot to return None
        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = None

            result = await cache._try_load()

            assert result is False
            assert cache._snapshot is first_snapshot
            assert cache._snapshot.records[0].canonical == "Tamale"

    async def test_reload_failure_returns_none_and_keeps_snapshot(self):
        """reload() returns None on failure but keeps the existing snapshot."""
        initial_records = [_make_record("Cape Coast")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        first_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(initial_records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(initial_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )
        cache._snapshot = first_snapshot

        # Patch _build_snapshot to return None (simulating failure)
        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = None

            result = await cache.reload()

            assert result is None
            assert cache._snapshot is first_snapshot


# ---------------------------------------------------------------------------
# Test: New record appears after one refresh (Requirement 9.7)
# ---------------------------------------------------------------------------


class TestNewRecordAppearsAfterRefresh:
    """After a successful refresh with updated data, get_snapshot()
    reflects the new records."""

    async def test_new_record_visible_after_successful_refresh(self):
        """Adding a record to the store and refreshing makes it visible."""
        initial_records = [_make_record("Accra"), _make_record("Kumasi")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        # Initial snapshot with 2 records
        first_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(initial_records),
            record_count=2,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(initial_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )
        cache._snapshot = first_snapshot

        assert cache.get_snapshot().record_count == 2

        # Build a new snapshot that includes the new record
        updated_records = initial_records + [_make_record("Tamale")]
        new_snapshot = DatasetSnapshot(
            version="2026-01-01T00:05:00Z",
            records=tuple(updated_records),
            record_count=3,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(updated_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = new_snapshot

            result = await cache._try_load()

            assert result is True
            assert cache.get_snapshot().record_count == 3
            canonicals = [r.canonical for r in cache.get_snapshot().records]
            assert "Tamale" in canonicals

    async def test_new_record_indexed_after_refresh(self):
        """A newly added record is indexed and queryable after refresh."""
        initial_records = [_make_record("Accra")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        first_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(initial_records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(initial_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )
        cache._snapshot = first_snapshot

        # Verify new record not in initial index
        assert "ningo-prampram" not in cache.get_snapshot().index.canonical_map

        # After refresh with new record
        updated_records = initial_records + [
            _make_record("Ningo-Prampram", aliases=["ningoprampram"])
        ]
        new_snapshot = DatasetSnapshot(
            version="2026-01-01T00:05:00Z",
            records=tuple(updated_records),
            record_count=2,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(updated_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = new_snapshot

            result = await cache._try_load()

            assert result is True
            # New record is in the index
            assert "ningo-prampram" in cache.get_snapshot().index.canonical_map
            assert cache.get_snapshot().index.canonical_map["ningo-prampram"] == "Ningo-Prampram"

    async def test_reload_updates_snapshot_on_demand(self):
        """reload() swaps the snapshot immediately without waiting for interval."""
        initial_records = [_make_record("Wa")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        first_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(initial_records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(initial_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )
        cache._snapshot = first_snapshot

        updated_records = initial_records + [_make_record("Bolgatanga")]
        new_snapshot = DatasetSnapshot(
            version="2026-01-01T00:05:00Z",
            records=tuple(updated_records),
            record_count=2,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(updated_records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = new_snapshot

            result = await cache.reload()

            assert result is not None
            assert result.record_count == 2
            assert cache.get_snapshot().record_count == 2


# ---------------------------------------------------------------------------
# Test: Index build stays under the load budget (Requirement 10.6)
# ---------------------------------------------------------------------------


class TestIndexBuildPerformance:
    """build_index completes within 10 seconds for 5000 EntityRecords."""

    def test_build_index_5000_records_under_10_seconds(self):
        """Building a MatchIndex from 5000 records with realistic alias
        counts completes within 10 seconds."""
        # Generate 5000 records with 4 aliases each (= 20000 aliases total)
        records = _make_records_batch(5000, alias_count=4)

        start = time.perf_counter()
        index = build_index(records)
        elapsed = time.perf_counter() - start

        assert elapsed < 10.0, (
            f"build_index took {elapsed:.2f}s for 5000 records, "
            f"exceeds the 10-second budget (Requirement 10.6)"
        )

        # Sanity: verify the index was actually built
        assert len(index.canonical_map) > 0
        assert index.bk_tree is not None

    def test_build_index_produces_correct_structure_at_scale(self):
        """At 5000 records, all index structures are populated."""
        records = _make_records_batch(5000, alias_count=4)

        index = build_index(records)

        # canonical_map should have entries for all canonicals + aliases
        assert len(index.canonical_map) >= 5000
        # fused_map should be populated
        assert len(index.fused_map) >= 5000
        # length_buckets should be populated
        assert len(index.length_buckets) > 0
        # BK-tree should be built
        assert index.bk_tree is not None
        # entity_kind_map should have an entry per canonical
        assert len(index.entity_kind_map) == 5000


# ---------------------------------------------------------------------------
# Test: Startup failure leaves snapshot as None and retries (Req 9.9, 9.10)
# ---------------------------------------------------------------------------


class TestStartupFailureRetries:
    """When the initial load fails, the snapshot stays None and
    the cache retries on the retry interval."""

    async def test_snapshot_is_none_before_first_load(self):
        """get_snapshot() returns None before any load attempt."""
        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        assert cache.get_snapshot() is None

    async def test_startup_failure_leaves_snapshot_none(self):
        """When _build_snapshot fails during start(), snapshot remains None."""
        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 1  # Short for testing
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = None

            loaded = await cache._try_load()

            assert loaded is False
            assert cache.get_snapshot() is None

    async def test_startup_retry_loop_exits_on_success(self):
        """The startup retry loop stops retrying once a load succeeds."""
        records = [_make_record("Accra")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 0.05  # Very short for testing
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        success_snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )

        call_count = 0

        async def _build_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return None  # Fail first 2 attempts
            return success_snapshot

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.side_effect = _build_side_effect
            # Also patch _start_refresh_loop since it's called after success
            with patch.object(cache, "_start_refresh_loop") as mock_refresh:
                # Run the retry loop in a task with a timeout
                task = asyncio.create_task(cache._startup_retry_loop())
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except asyncio.TimeoutError:
                    pytest.fail("Startup retry loop did not exit in time")

                assert cache.get_snapshot() is success_snapshot
                assert call_count == 3
                mock_refresh.assert_called_once()

    async def test_startup_retry_loop_stops_when_stopping(self):
        """Setting _stopping=True causes the retry loop to exit."""
        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 0.05
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = None

            # Start the retry loop
            task = asyncio.create_task(cache._startup_retry_loop())

            # Let it run one iteration
            await asyncio.sleep(0.1)

            # Signal stop
            cache._stopping = True

            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                pytest.fail("Retry loop did not stop after _stopping was set")

            assert cache.get_snapshot() is None


# ---------------------------------------------------------------------------
# Test: get_snapshot() returns None before first load, snapshot after
# ---------------------------------------------------------------------------


class TestGetSnapshot:
    """get_snapshot() returns None before first load, then the snapshot after."""

    async def test_returns_none_initially(self):
        """Before any load, get_snapshot() is None."""
        cache = DatasetCache.__new__(DatasetCache)
        cache._snapshot = None

        assert cache.get_snapshot() is None

    async def test_returns_snapshot_after_successful_load(self):
        """After a successful load, get_snapshot() returns the snapshot."""
        records = [_make_record("Sunyani")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        snapshot = DatasetSnapshot(
            version="2026-01-01T00:00:00Z",
            records=tuple(records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(records),
            block_list=frozenset(["general", "page"]),
            stopwords=frozenset(["the", "a"]),
            word_stopwords=frozenset(["of"]),
            title_prefixes=frozenset(["Mr", "Mrs"]),
        )

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = snapshot

            loaded = await cache._try_load()

            assert loaded is True
            result = cache.get_snapshot()
            assert result is not None
            assert result.record_count == 1
            assert result.records[0].canonical == "Sunyani"
            assert "general" in result.block_list
            assert "the" in result.stopwords

    async def test_snapshot_version_is_set(self):
        """The snapshot has a version string set on load."""
        records = [_make_record("Ho")]

        cache = DatasetCache.__new__(DatasetCache)
        cache._database_url = "postgresql://test/db"
        cache._refresh_seconds = 300
        cache._load_retry_seconds = 30
        cache._snapshot = None
        cache._engine = MagicMock()
        cache._session_factory = MagicMock()
        cache._refresh_task = None
        cache._startup_task = None
        cache._stopping = False

        snapshot = DatasetSnapshot(
            version="2026-07-09T10:00:00Z",
            records=tuple(records),
            record_count=1,
            loaded_at=datetime.now(timezone.utc),
            index=build_index(records),
            block_list=frozenset(),
            stopwords=frozenset(),
            word_stopwords=frozenset(),
            title_prefixes=frozenset(),
        )

        with patch.object(cache, "_build_snapshot", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = snapshot

            await cache._try_load()

            assert cache.get_snapshot().version == "2026-07-09T10:00:00Z"
