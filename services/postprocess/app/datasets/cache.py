"""Dataset_Cache — immutable snapshot, periodic refresh, and on-demand reload.

Holds an immutable DatasetSnapshot built from the Dataset_Store. The snapshot
contains all active Entity_Records, the derived Match_Index, and the block/stop
lists. Refresh builds a *new* snapshot in a worker thread and swaps the single
reference atomically, so in-flight requests keep a consistent view and no lock
is needed on the read path.

Behaviour:
- Startup: attempt load. On failure, leave snapshot as None (health → 503)
  and retry every DATASET_LOAD_RETRY_SECONDS.
- After successful load: periodic refresh every DATASET_REFRESH_SECONDS.
- Refresh failure: keep current snapshot, log one error, emit metric.
- reload(): on-demand refresh triggered by POST /v1/datasets/reload.
- get_snapshot(): returns the current snapshot (may be None before first load).

Requirements: 9.5, 9.6, 9.7, 9.9, 9.10, 10.5, 10.6
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.datasets.index import MatchIndex, build_index
from app.datasets.store import (
    load_active_records,
    load_all_block_lists,
    make_engine,
    make_session_factory,
)
from app.models.entities import EntityRecord

logger = structlog.get_logger("dataset_cache")


@dataclass(frozen=True)
class DatasetSnapshot:
    """Immutable point-in-time view of the Dataset_Store.

    Swap the reference, never mutate. All fields are deeply immutable
    (tuples, frozensets, and the MatchIndex which is built once and
    never written to after construction).
    """

    version: str
    records: tuple[EntityRecord, ...]
    record_count: int
    loaded_at: datetime
    index: MatchIndex
    block_list: frozenset[str]
    stopwords: frozenset[str]
    word_stopwords: frozenset[str]
    title_prefixes: frozenset[str]


class DatasetCache:
    """Manages the lifecycle of the DatasetSnapshot.

    Provides startup loading with retry, periodic background refresh,
    and on-demand reload. The snapshot reference is swapped atomically
    so readers never see a partial state.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        refresh_seconds: int = 300,
        load_retry_seconds: int = 30,
        *,
        database_url: str | None = None,
    ) -> None:
        """Build a cache over a shared session factory.

        Pass ``session_factory`` so the cache shares the process-wide pool.
        ``database_url`` is a fallback that creates a private engine, kept for
        standalone use (scripts, tests); when used, this cache owns that engine
        and disposes it in ``stop()``.
        """
        if session_factory is None and database_url is None:
            raise ValueError("provide either session_factory or database_url")

        self._refresh_seconds = refresh_seconds
        self._load_retry_seconds = load_retry_seconds

        self._snapshot: DatasetSnapshot | None = None

        if session_factory is not None:
            # Shared pool — this cache must not dispose an engine it doesn't own.
            self._session_factory = session_factory
            self._engine = None
        else:
            self._engine = make_engine(database_url)  # type: ignore[arg-type]
            self._session_factory = make_session_factory(self._engine)

        self._refresh_task: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = None
        self._stopping = False

    def get_snapshot(self) -> DatasetSnapshot | None:
        """Return the current snapshot, or None if never loaded."""
        return self._snapshot

    async def start(self) -> None:
        """Start the cache: attempt initial load, then schedule refresh.

        If the initial load fails, a background retry loop runs on
        DATASET_LOAD_RETRY_SECONDS until the first successful load,
        after which the periodic refresh takes over.
        """
        self._stopping = False
        loaded = await self._try_load()
        if loaded:
            self._start_refresh_loop()
        else:
            # Startup failure — retry on the retry interval
            self._startup_task = asyncio.create_task(
                self._startup_retry_loop(),
                name="dataset_cache_startup_retry",
            )

    async def stop(self) -> None:
        """Cancel background tasks, and dispose the engine only if we own it."""
        self._stopping = True

        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

        if self._startup_task is not None:
            self._startup_task.cancel()
            try:
                await self._startup_task
            except asyncio.CancelledError:
                pass
            self._startup_task = None

        # Only dispose an engine this cache created. When running on the shared
        # pool, the application lifespan owns disposal.
        if self._engine is not None:
            await self._engine.dispose()

    async def reload(self) -> DatasetSnapshot | None:
        """On-demand reload triggered by the /v1/datasets/reload endpoint.

        Returns the new snapshot on success, or None if the load failed
        (in which case the previous snapshot is retained).
        """
        snapshot = await self._build_snapshot()
        if snapshot is not None:
            self._snapshot = snapshot
            logger.info(
                "dataset_cache.reloaded",
                record_count=snapshot.record_count,
                version=snapshot.version,
            )
        return snapshot

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _try_load(self) -> bool:
        """Attempt to build and swap a snapshot. Returns True on success."""
        try:
            snapshot = await self._build_snapshot()
            if snapshot is not None:
                self._snapshot = snapshot
                logger.info(
                    "dataset_cache.loaded",
                    record_count=snapshot.record_count,
                    version=snapshot.version,
                )
                return True
            return False
        except Exception:
            logger.error("dataset_cache.load_failed", exc_info=True)
            return False

    async def _build_snapshot(self) -> DatasetSnapshot | None:
        """Load records and block lists, build the index in a thread.

        Returns a new DatasetSnapshot or None on failure.
        """
        try:
            async with self._session_factory() as session:
                records = await load_active_records(session)
                block_lists = await load_all_block_lists(session)

            # Extract block list sets by kind
            block_tokens = frozenset(
                entry.token for entry in block_lists.get("block", [])
            )
            stopword_tokens = frozenset(
                entry.token for entry in block_lists.get("stopword", [])
            )
            word_stopword_tokens = frozenset(
                entry.token for entry in block_lists.get("word_stopword", [])
            )
            title_prefix_tokens = frozenset(
                entry.token for entry in block_lists.get("title", [])
            )

            # Build index in a worker thread so the event loop stays free
            index = await asyncio.to_thread(build_index, records)

            now = datetime.now(timezone.utc)
            version = now.isoformat()

            snapshot = DatasetSnapshot(
                version=version,
                records=tuple(records),
                record_count=len(records),
                loaded_at=now,
                index=index,
                block_list=block_tokens,
                stopwords=stopword_tokens,
                word_stopwords=word_stopword_tokens,
                title_prefixes=title_prefix_tokens,
            )
            return snapshot

        except Exception:
            logger.error("dataset_cache.build_failed", exc_info=True)
            return None

    async def _startup_retry_loop(self) -> None:
        """Retry loading until success, then start the refresh loop."""
        while not self._stopping:
            await asyncio.sleep(self._load_retry_seconds)
            if self._stopping:
                break
            loaded = await self._try_load()
            if loaded:
                self._start_refresh_loop()
                return

    def _start_refresh_loop(self) -> None:
        """Launch the periodic refresh background task."""
        if self._refresh_task is not None:
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(),
            name="dataset_cache_refresh",
        )

    async def _refresh_loop(self) -> None:
        """Periodically refresh the snapshot.

        On failure: keep the current snapshot, log one error, emit the
        refresh-failure metric.
        """
        while not self._stopping:
            await asyncio.sleep(self._refresh_seconds)
            if self._stopping:
                break
            try:
                snapshot = await self._build_snapshot()
                if snapshot is not None:
                    self._snapshot = snapshot
                    logger.info(
                        "dataset_cache.refreshed",
                        record_count=snapshot.record_count,
                        version=snapshot.version,
                    )
                else:
                    # build_snapshot returned None — already logged inside
                    self._emit_refresh_failure_metric()
            except Exception:
                logger.error("dataset_cache.refresh_failed", exc_info=True)
                self._emit_refresh_failure_metric()

    def _emit_refresh_failure_metric(self) -> None:
        """Emit the dataset refresh failure metric via app.obs.metrics."""
        from app.obs.metrics import emit_dataset_refresh_failure  # noqa: WPS433

        emit_dataset_refresh_failure()
