"""Integration tests for Dataset_Cache refresh behaviour.

Verifies that correction requests are served from the previously loaded
snapshot during a refresh cycle, ensuring no interruption to service.

Requirements: 8.3, 8.4
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex


def _make_fake_snapshot(version: str = "v1") -> DatasetSnapshot:
    """Build a minimal DatasetSnapshot for testing."""
    return DatasetSnapshot(
        version=version,
        records=(),
        record_count=0,
        loaded_at=datetime.now(timezone.utc),
        index=MatchIndex(
            canonical_map={},
            fused_map={},
            phonetic_map={},
            bk_tree=None,
        ),
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )


@pytest.mark.asyncio
async def test_snapshot_available_during_refresh() -> None:
    """Correction requests are served from the previously loaded snapshot
    while a refresh is in progress (the old snapshot stays readable)."""

    old_snapshot = _make_fake_snapshot("v-old")
    new_snapshot = _make_fake_snapshot("v-new")

    # Event used to simulate a slow refresh build
    refresh_started = asyncio.Event()
    proceed_refresh = asyncio.Event()

    async def slow_build_snapshot(self: DatasetCache) -> DatasetSnapshot | None:
        refresh_started.set()
        await proceed_refresh.wait()
        return new_snapshot

    cache = DatasetCache.__new__(DatasetCache)
    cache._snapshot = old_snapshot
    cache._stopping = False
    cache._refresh_task = None
    cache._startup_task = None

    # Patch the internal build method to simulate a slow refresh
    with patch.object(
        DatasetCache, "_build_snapshot", slow_build_snapshot
    ):
        # Trigger a refresh (simulates the periodic loop calling _build_snapshot)
        refresh_task = asyncio.create_task(cache.reload())

        # Wait until the refresh has started
        await asyncio.wait_for(refresh_started.wait(), timeout=2.0)

        # While refresh is in progress, the old snapshot must still be available
        current = cache.get_snapshot()
        assert current is not None, "Snapshot should not be None during refresh"
        assert current.version == "v-old", (
            "Requests must be served from the previous snapshot during refresh"
        )

        # Let the refresh complete
        proceed_refresh.set()
        result = await asyncio.wait_for(refresh_task, timeout=2.0)

    # After refresh completes, the new snapshot is available
    assert result is not None
    assert cache.get_snapshot().version == "v-new"


@pytest.mark.asyncio
async def test_snapshot_retained_on_refresh_failure() -> None:
    """If refresh fails, the previously loaded snapshot is retained
    and continues to serve correction requests."""

    old_snapshot = _make_fake_snapshot("v-retained")

    cache = DatasetCache.__new__(DatasetCache)
    cache._snapshot = old_snapshot
    cache._stopping = False
    cache._refresh_task = None
    cache._startup_task = None

    async def failing_build(self: DatasetCache) -> DatasetSnapshot | None:
        return None  # Simulates a failed refresh

    with patch.object(DatasetCache, "_build_snapshot", failing_build):
        result = await cache.reload()

    # reload returns None on failure
    assert result is None

    # The old snapshot must still be available
    current = cache.get_snapshot()
    assert current is not None
    assert current.version == "v-retained", (
        "Previous snapshot must be retained when refresh fails"
    )
