"""Transient-error handling for RAG ingestion: outcomes, retries, and the sweep.

A save's ingest must not be lost to a transient failure. The worker retries a
failed ingest with backoff, and a periodic reconciliation sweep re-queues any
saved transcript still missing from the index (the database is the source of
truth). The DB session and Bedrock are mocked; nothing touches the network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import verify_service_token
from app.rag import router as rag_router_module
from app.rag.ingestion import (
    IngestOutcome,
    IngestTransientError,
    TranscriptIngestionWorker,
    find_index_gaps,
)


def _settings(base, **overrides):
    """Real Settings with retry/sweep tunables overridden for fast tests."""
    values = {"rag_ingest_retry_base_s": 0, "rag_reconcile_interval_s": 0, **overrides}
    return base.model_copy(update=values)


def _worker(settings, embeddings, session_factory=None) -> TranscriptIngestionWorker:
    return TranscriptIngestionWorker(
        session_factory or MagicMock(), settings, embeddings=embeddings
    )


def _transcript_data() -> dict:
    words = [
        {"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "speaker": "A"}
        for i, w in enumerate(["the", "house", "adjourned"])
    ]
    return {
        "corrected_text": "The House adjourned",
        "word_timings": words,
        "entities": [],
        "version": 2,
        "raw_text": "the house adjourned",
        "metadata": {},
        "is_latest": True,
    }


def _session_factory(results: list):
    """Factory whose session.execute() returns *results* in order."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=results)
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session_cm)
    factory.session = session
    return factory


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# ingest() outcomes: transient failures are distinguishable from "nothing to do"
# ---------------------------------------------------------------------------


class TestIngestOutcome:
    async def test_database_unavailable_on_load_is_failed_not_skipped(
        self, mock_settings, mock_embeddings
    ):
        """Regression: a DB error on load used to look like "not found" and the
        save was silently dropped from the index."""
        factory = _session_factory([ConnectionError("database unavailable")])
        worker = _worker(mock_settings, mock_embeddings, factory)

        assert await worker.ingest(42) is IngestOutcome.failed

    async def test_missing_transcript_is_skipped(self, mock_settings, mock_embeddings):
        factory = _session_factory([MagicMock(fetchone=lambda: None)])
        worker = _worker(mock_settings, mock_embeddings, factory)

        assert await worker.ingest(42) is IngestOutcome.skipped

    async def test_rolled_back_chunk_write_is_failed(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        worker = _worker(mock_settings, mock_embeddings)
        monkeypatch.setattr(worker, "_load_transcript", AsyncMock(return_value=_transcript_data()))
        monkeypatch.setattr(worker, "embed_chunks", AsyncMock(return_value=[[0.1] * 1024]))
        monkeypatch.setattr(
            worker, "_replace_chunks", AsyncMock(side_effect=IngestTransientError("rolled back"))
        )
        evidence = AsyncMock()
        monkeypatch.setattr(worker, "_persist_correction_evidence", evidence)

        assert await worker.ingest(42) is IngestOutcome.failed
        # Left to the retry, which writes it at most once.
        evidence.assert_not_awaited()

    async def test_superseded_version_is_skipped(self, mock_settings, mock_embeddings, monkeypatch):
        worker = _worker(mock_settings, mock_embeddings)
        monkeypatch.setattr(worker, "_load_transcript", AsyncMock(return_value=_transcript_data()))
        monkeypatch.setattr(worker, "embed_chunks", AsyncMock(return_value=[[0.1] * 1024]))
        monkeypatch.setattr(worker, "_replace_chunks", AsyncMock(return_value=False))
        monkeypatch.setattr(worker, "_persist_correction_evidence", AsyncMock())

        assert await worker.ingest(42) is IngestOutcome.skipped

    async def test_all_vectors_present_is_indexed(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        worker = _worker(mock_settings, mock_embeddings)
        monkeypatch.setattr(worker, "_load_transcript", AsyncMock(return_value=_transcript_data()))
        monkeypatch.setattr(worker, "embed_chunks", AsyncMock(return_value=[[0.1] * 1024]))
        monkeypatch.setattr(worker, "_replace_chunks", AsyncMock(return_value=True))
        monkeypatch.setattr(worker, "_persist_correction_evidence", AsyncMock())

        assert await worker.ingest(42) is IngestOutcome.indexed

    async def test_bedrock_failure_stores_text_then_reports_failed(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        """Chunks are stored (keyword-searchable now); retry later for vectors."""
        worker = _worker(mock_settings, mock_embeddings)
        monkeypatch.setattr(worker, "_load_transcript", AsyncMock(return_value=_transcript_data()))
        monkeypatch.setattr(worker, "embed_chunks", AsyncMock(return_value=[None]))
        replace = AsyncMock(return_value=True)
        monkeypatch.setattr(worker, "_replace_chunks", replace)
        monkeypatch.setattr(worker, "_persist_correction_evidence", AsyncMock())

        assert await worker.ingest(42) is IngestOutcome.failed
        replace.assert_awaited_once()

    async def test_no_embedding_client_is_not_retried(self, mock_settings, monkeypatch):
        """Without an embedding client a retry cannot produce vectors."""
        worker = _worker(mock_settings, embeddings=None)
        monkeypatch.setattr(worker, "_load_transcript", AsyncMock(return_value=_transcript_data()))
        monkeypatch.setattr(worker, "_replace_chunks", AsyncMock(return_value=True))
        monkeypatch.setattr(worker, "_persist_correction_evidence", AsyncMock())

        assert await worker.ingest(42) is IngestOutcome.indexed


# ---------------------------------------------------------------------------
# Bounded retries with backoff
# ---------------------------------------------------------------------------


class TestWorkerRetries:
    async def test_failed_ingest_is_requeued_after_backoff(self, mock_settings, mock_embeddings):
        worker = _worker(_settings(mock_settings), mock_embeddings)

        worker._record_outcome(7, IngestOutcome.failed)

        # Waiting out its delay off-queue; a sweep or reindex can't double it.
        assert 7 in worker._retrying
        assert worker.enqueue(7) is False
        await asyncio.gather(*worker._retry_tasks)
        assert 7 not in worker._retrying
        assert worker._queue.qsize() == 1
        assert worker._attempts[7] == 1

    async def test_backoff_doubles_per_attempt(self, mock_settings, mock_embeddings, monkeypatch):
        worker = _worker(
            _settings(mock_settings, rag_ingest_retry_base_s=5, rag_ingest_max_attempts=4),
            mock_embeddings,
        )
        requeue = AsyncMock()
        monkeypatch.setattr(worker, "_requeue_after", requeue)

        for _ in range(3):
            worker._record_outcome(7, IngestOutcome.failed)
            worker._retrying.discard(7)
        await asyncio.gather(*worker._retry_tasks)

        assert [call.args for call in requeue.await_args_list] == [(7, 5), (7, 10), (7, 20)]

    async def test_retries_stop_after_max_attempts(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        worker = _worker(_settings(mock_settings, rag_ingest_max_attempts=3), mock_embeddings)
        requeue = AsyncMock()
        monkeypatch.setattr(worker, "_requeue_after", requeue)

        for _ in range(3):
            worker._record_outcome(7, IngestOutcome.failed)
            worker._retrying.discard(7)
        await asyncio.gather(*worker._retry_tasks)

        # Attempts 1 and 2 were retried; attempt 3 is left to the sweep.
        assert requeue.await_count == 2
        assert 7 not in worker._attempts
        assert 7 not in worker._retrying

    async def test_max_attempts_one_disables_retries(self, mock_settings, mock_embeddings):
        worker = _worker(_settings(mock_settings, rag_ingest_max_attempts=1), mock_embeddings)

        worker._record_outcome(7, IngestOutcome.failed)

        assert not worker._retry_tasks
        assert 7 not in worker._retrying

    async def test_success_resets_the_attempt_count(self, mock_settings, mock_embeddings):
        worker = _worker(_settings(mock_settings), mock_embeddings)
        worker._attempts[7] = 2

        worker._record_outcome(7, IngestOutcome.indexed)

        assert 7 not in worker._attempts

    async def test_worker_loop_retries_until_the_ingest_succeeds(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        """End to end through the running worker: fail, fail, then index."""
        worker = _worker(_settings(mock_settings, rag_ingest_max_attempts=3), mock_embeddings)
        ingest = AsyncMock(
            side_effect=[IngestOutcome.failed, IngestOutcome.failed, IngestOutcome.indexed]
        )
        monkeypatch.setattr(worker, "ingest", ingest)

        worker.start()
        try:
            worker.enqueue(5)
            await _wait_for(lambda: ingest.await_count == 3)
            await _wait_for(lambda: 5 not in worker._attempts)
        finally:
            await worker.stop()

        assert [call.args for call in ingest.await_args_list] == [(5,), (5,), (5,)]

    async def test_unexpected_exception_is_retried_not_lost(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        worker = _worker(_settings(mock_settings), mock_embeddings)
        ingest = AsyncMock(side_effect=[RuntimeError("boom"), IngestOutcome.indexed])
        monkeypatch.setattr(worker, "ingest", ingest)

        worker.start()
        try:
            worker.enqueue(9)
            await _wait_for(lambda: ingest.await_count == 2)
        finally:
            await worker.stop()

    async def test_stop_cancels_pending_retries(self, mock_settings, mock_embeddings):
        worker = _worker(_settings(mock_settings, rag_ingest_retry_base_s=3600), mock_embeddings)
        worker.start()
        worker._record_outcome(7, IngestOutcome.failed)
        (retry,) = worker._retry_tasks

        await worker.stop()

        assert retry.cancelled()
        assert worker._queue.empty()
        assert 7 not in worker._retrying


# ---------------------------------------------------------------------------
# Reconciliation sweep
# ---------------------------------------------------------------------------


class TestReconcile:
    async def test_requeues_every_gap_found(self, mock_settings, mock_embeddings):
        factory = _session_factory(
            [
                MagicMock(scalar=lambda: True),  # pg_try_advisory_xact_lock
                MagicMock(fetchall=lambda: [(11, 0), (12, 3)]),
            ]
        )
        worker = _worker(_settings(mock_settings), mock_embeddings, factory)

        assert await worker.reconcile() == 2

        assert sorted(worker._pending) == [11, 12]
        lock_sql, gaps_sql = (str(c.args[0]) for c in factory.session.execute.call_args_list)
        assert "pg_try_advisory_xact_lock" in lock_sql
        assert "embedding IS NULL" in gaps_sql
        assert "NOT EXISTS" in gaps_sql
        assert factory.session.execute.call_args_list[1].args[1] == {"limit": 50}

    async def test_skips_the_round_when_another_process_is_sweeping(
        self, mock_settings, mock_embeddings
    ):
        factory = _session_factory([MagicMock(scalar=lambda: False)])
        worker = _worker(_settings(mock_settings), mock_embeddings, factory)

        assert await worker.reconcile() == 0
        assert factory.session.execute.await_count == 1
        assert worker._queue.empty()

    async def test_without_embeddings_only_never_indexed_versions_qualify(self, mock_settings):
        """Re-ingesting an unembedded chunk would store the same NULL vector."""
        factory = _session_factory(
            [MagicMock(scalar=lambda: True), MagicMock(fetchall=lambda: [(11, 0)])]
        )
        worker = _worker(_settings(mock_settings), None, factory)

        await worker.reconcile()

        gaps_sql = str(factory.session.execute.call_args_list[1].args[0])
        assert "embedding IS NULL" not in gaps_sql
        assert "NOT EXISTS" in gaps_sql

    async def test_does_not_double_queue_pending_or_retrying(self, mock_settings, mock_embeddings):
        factory = _session_factory(
            [MagicMock(scalar=lambda: True), MagicMock(fetchall=lambda: [(11, 0), (12, 0)])]
        )
        worker = _worker(_settings(mock_settings), mock_embeddings, factory)
        worker.enqueue(11)
        worker._retrying.add(12)

        assert await worker.reconcile() == 0
        assert worker._queue.qsize() == 1

    async def test_query_failure_is_logged_and_the_next_round_tries_again(
        self, mock_settings, mock_embeddings
    ):
        factory = _session_factory([ConnectionError("database unavailable")])
        worker = _worker(_settings(mock_settings), mock_embeddings, factory)

        assert await worker.reconcile() == 0

    async def test_start_sweeps_immediately_then_on_the_interval(
        self, mock_settings, mock_embeddings, monkeypatch
    ):
        worker = _worker(_settings(mock_settings, rag_reconcile_interval_s=3600), mock_embeddings)
        reconcile = AsyncMock(return_value=0)
        monkeypatch.setattr(worker, "reconcile", reconcile)

        worker.start()
        try:
            await _wait_for(lambda: reconcile.await_count == 1)
        finally:
            await worker.stop()
        assert worker._reconcile_task.cancelled()

    async def test_interval_zero_disables_the_sweep(self, mock_settings, mock_embeddings):
        worker = _worker(_settings(mock_settings, rag_reconcile_interval_s=0), mock_embeddings)

        worker.start()
        try:
            assert worker._reconcile_task is None
        finally:
            await worker.stop()

    async def test_stub_settings_fall_back_to_defaults(self, mock_embeddings):
        """Routers/tests that pass a bare namespace still get a working worker."""
        worker = _worker(SimpleNamespace(), mock_embeddings)

        assert worker._setting("rag_ingest_max_attempts", 3) == 3
        assert worker._setting("rag_reconcile_interval_s", 300) == 300


class TestFindIndexGaps:
    async def test_only_latest_versions_are_considered(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: [(4, 2)]))

        assert await find_index_gaps(session, 10) == [(4, 2)]

        sql = str(session.execute.await_args.args[0])
        # Both arms (never indexed, unembedded) are restricted to latest versions.
        assert sql.count("SELECT MAX(t2.version)") == 2
        assert ":limit" in sql
        assert session.execute.await_args.args[1] == {"limit": 10}


# ---------------------------------------------------------------------------
# POST /rag/reindex reuses the same gap query
# ---------------------------------------------------------------------------


def _reindex_client(*, embeddings, worker) -> TestClient:
    app = FastAPI()
    app.include_router(rag_router_module.router)
    app.dependency_overrides[verify_service_token] = lambda: None
    app.state.session_factory = _session_factory([])
    app.state.settings = SimpleNamespace()
    app.state.embeddings = embeddings
    app.state.ingestion_worker = worker
    return TestClient(app)


class TestReindexEndpoint:
    def test_queues_the_gaps_found(self, monkeypatch):
        monkeypatch.setattr(
            rag_router_module, "find_index_gaps", AsyncMock(return_value=[(11, 0), (12, 3)])
        )
        worker = MagicMock()
        client = _reindex_client(embeddings=object(), worker=worker)

        response = client.post("/rag/reindex", json={"limit": 25})

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["transcript_ids"] == [11, 12]
        assert body["total_unindexed_chunks"] == 3
        assert [c.args for c in worker.enqueue.call_args_list] == [(11,), (12,)]
        assert rag_router_module.find_index_gaps.await_args.args[1] == 25

    def test_reports_an_error_when_the_query_fails(self, monkeypatch):
        monkeypatch.setattr(
            rag_router_module,
            "find_index_gaps",
            AsyncMock(side_effect=ConnectionError("database unavailable")),
        )
        worker = MagicMock()
        client = _reindex_client(embeddings=object(), worker=worker)

        response = client.post("/rag/reindex", json={})

        assert response.status_code == 202
        assert response.json()["status"] == "error"
        worker.enqueue.assert_not_called()
