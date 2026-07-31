"""Asynchronous correction history persistence.

Provides a bounded asyncio.Queue-backed writer that persists applied
Correction_Records to the ``correction_history`` table without ever
adding latency to or failing a correction request.

The queue is bounded — overflow drops records and increments a counter.
History is analytics; it must never block the request path.

Requirements: 13.9
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = structlog.get_logger("history")

# Maximum records drained per write cycle
_BATCH_SIZE = 50


def _emit_history_dropped() -> None:
    """Emit the dropped-history metric, tolerating an unavailable metrics stack."""
    try:
        from app.obs.metrics import emit_history_dropped  # noqa: WPS433

        emit_history_dropped()
    except Exception:  # pragma: no cover - metrics must never break the request path
        pass


@dataclass(frozen=True)
class HistoryRecord:
    """One applied correction destined for the correction_history table."""

    correlation_id: str
    text_hash: str
    original: str
    corrected: str
    strategy: str
    confidence: float
    entity_kind: str
    entity_type: str
    model_version: str
    created_at: datetime


class CorrectionHistoryWriter:
    """Bounded async queue + background writer for correction history.

    History persistence is fully async and non-blocking to request handlers.
    If the queue is full, records are silently dropped and counted — the
    correction request is never affected.

    Usage::

        writer = CorrectionHistoryWriter(session_factory)
        writer.start()
        # ... in request handlers ...
        writer.enqueue(record)
        # ... on shutdown ...
        await writer.stop()
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        max_queue_size: int = 1000,
        *,
        retention_days: int = 90,
        retention_interval_seconds: int = 86400,
    ) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue[HistoryRecord] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._dropped_count: int = 0
        self._task: asyncio.Task[None] | None = None
        self._retention_days = retention_days
        self._retention_interval_seconds = retention_interval_seconds
        self._retention_task: asyncio.Task[None] | None = None

    @property
    def dropped_count(self) -> int:
        """Number of records dropped due to queue overflow."""
        return self._dropped_count

    def start(self) -> None:
        """Launch the background writer and the retention sweeper."""
        self._task = asyncio.create_task(self._writer_loop(), name="history-writer")
        if self._retention_days > 0:
            self._retention_task = asyncio.create_task(
                self._retention_loop(), name="history-retention"
            )

    async def stop(self) -> None:
        """Cancel background tasks gracefully, draining remaining items."""
        if self._retention_task is not None:
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass
            self._retention_task = None

        if self._task is None:
            return

        # Signal the loop to stop by cancelling, then drain what remains
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

        # Drain any remaining records in the queue
        await self._drain_remaining()

    def enqueue(self, record: HistoryRecord) -> None:
        """Put a record on the queue. Drops if full, but never blocks.

        Overflow is logged at WARNING and counted as a metric. Dropping audit
        data is a real loss, so it must be visible to monitoring rather than
        buried at debug level where nobody would see it.
        """
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped_count += 1
            logger.warning(
                "history.queue_overflow",
                dropped_total=self._dropped_count,
                queue_maxsize=self._queue.maxsize,
            )
            _emit_history_dropped()

    async def _writer_loop(self) -> None:
        """Background loop consuming records and persisting them in batches."""
        while True:
            try:
                # Wait for at least one record
                first = await self._queue.get()
                batch = [first]

                # Drain up to _BATCH_SIZE - 1 more without blocking
                while len(batch) < _BATCH_SIZE:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                await self._persist_batch(batch)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("history.writer_error", exc_info=True)
                # Brief backoff on unexpected errors to avoid tight loop
                await asyncio.sleep(1.0)

    async def _retention_loop(self) -> None:
        """Periodically delete history rows older than the retention window.

        ``correction_history`` gains a row per applied correction, so without a
        sweeper it grows without bound and eventually degrades both queries and
        backups. Deletes are batched and capped per pass to avoid holding a long
        transaction or a large lock on a busy table.
        """
        while True:
            try:
                await asyncio.sleep(self._retention_interval_seconds)
                await self._prune_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("history.retention_error", exc_info=True)

    async def _prune_once(self, *, batch_limit: int = 10000) -> int:
        """Delete one batch of expired rows. Returns the number removed."""
        if self._retention_days <= 0:
            return 0

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    result = await session.execute(
                        text(
                            "DELETE FROM correction_history "
                            "WHERE id IN ("
                            "  SELECT id FROM correction_history "
                            "  WHERE created_at < now() - make_interval(days => :days) "
                            "  ORDER BY id "
                            "  LIMIT :limit"
                            ")"
                        ),
                        {"days": self._retention_days, "limit": batch_limit},
                    )
            deleted = result.rowcount or 0
            if deleted:
                logger.info(
                    "history.retention_pruned",
                    deleted=deleted,
                    retention_days=self._retention_days,
                )
            return deleted
        except Exception:
            logger.error("history.retention_prune_failed", exc_info=True)
            return 0

    async def _drain_remaining(self) -> None:
        """Drain and persist any records left in the queue after cancellation."""
        batch: list[HistoryRecord] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if batch:
            await self._persist_batch(batch)

    async def _persist_batch(self, batch: list[HistoryRecord]) -> None:
        """Write a batch of records to the correction_history table.

        A failing database write logs an error but does NOT crash the writer
        or affect requests.
        """
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO correction_history "
                            "(correlation_id, text_hash, original, corrected, "
                            "strategy, confidence, entity_kind, entity_type, "
                            "model_version, created_at) "
                            "VALUES "
                            "(:correlation_id, :text_hash, :original, :corrected, "
                            ":strategy, :confidence, :entity_kind, :entity_type, "
                            ":model_version, :created_at)"
                        ),
                        [
                            {
                                "correlation_id": r.correlation_id,
                                "text_hash": r.text_hash,
                                "original": r.original,
                                "corrected": r.corrected,
                                "strategy": r.strategy,
                                "confidence": r.confidence,
                                "entity_kind": r.entity_kind,
                                "entity_type": r.entity_type,
                                "model_version": r.model_version,
                                "created_at": r.created_at,
                            }
                            for r in batch
                        ],
                    )
            logger.debug("history.batch_written", count=len(batch))
        except Exception:
            logger.error(
                "history.persist_error",
                batch_size=len(batch),
                exc_info=True,
            )
