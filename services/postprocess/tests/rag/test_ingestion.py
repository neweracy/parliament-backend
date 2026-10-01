"""Tests for app/rag/ingestion.py — transcript ingestion worker."""

from __future__ import annotations

import pytest

from app.rag.ingestion import TranscriptIngestionWorker


@pytest.fixture
def worker(mock_settings, mock_embeddings, mock_session_factory):
    """Create an ingestion worker with mocked dependencies."""
    return TranscriptIngestionWorker(
        session_factory=mock_session_factory,
        settings=mock_settings,
        embeddings=mock_embeddings,
    )


class TestGroupSpeakerTurns:
    def test_single_speaker(self, worker):
        words = [
            {"word": "hello", "start": 0.0, "end": 0.5, "speaker": "A"},
            {"word": "world", "start": 0.6, "end": 1.0, "speaker": "A"},
        ]
        turns = worker._group_speaker_turns(words)
        assert len(turns) == 1
        assert len(turns[0]) == 2

    def test_speaker_change(self, worker):
        words = [
            {"word": "hello", "start": 0.0, "end": 0.5, "speaker": "A"},
            {"word": "hi", "start": 1.0, "end": 1.5, "speaker": "B"},
            {"word": "there", "start": 1.6, "end": 2.0, "speaker": "B"},
        ]
        turns = worker._group_speaker_turns(words)
        assert len(turns) == 2
        assert len(turns[0]) == 1  # speaker A
        assert len(turns[1]) == 2  # speaker B

    def test_empty_words(self, worker):
        assert worker._group_speaker_turns([]) == []

    def test_multiple_transitions(self, worker):
        words = [
            {"word": "a", "start": 0.0, "end": 0.5, "speaker": "A"},
            {"word": "b", "start": 1.0, "end": 1.5, "speaker": "B"},
            {"word": "c", "start": 2.0, "end": 2.5, "speaker": "A"},
        ]
        turns = worker._group_speaker_turns(words)
        assert len(turns) == 3
        assert turns[0][0]["speaker"] == "A"
        assert turns[1][0]["speaker"] == "B"
        assert turns[2][0]["speaker"] == "A"


class TestChunkSpeakerTurn:
    def test_short_turn_single_chunk(self, worker):
        """A turn under _MAX_CHUNK_WORDS becomes one chunk."""
        words = [
            {"word": f"word{i}", "start": float(i), "end": float(i) + 0.5, "speaker": "A"}
            for i in range(50)
        ]
        chunks = worker._chunk_speaker_turn(words, [])
        assert len(chunks) == 1
        assert chunks[0]["speaker"] == "A"

    def test_long_turn_splits(self, worker):
        """A turn over _MAX_CHUNK_WORDS is split."""
        words = [
            {"word": f"word{i}", "start": float(i), "end": float(i) + 0.5, "speaker": "A"}
            for i in range(300)
        ]
        chunks = worker._chunk_speaker_turn(words, [])
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk["speaker"] == "A"

    def test_pause_based_split(self, worker):
        """Splits prefer pause boundaries over hard word-count boundaries."""
        # First 120 words with a 3-second pause at index 110
        words = []
        for i in range(250):
            start = float(i)
            end = start + 0.5
            # Insert a 3-second pause at index 110 (between word 109 and 110)
            if i == 110:
                start = words[-1]["end"] + 3.0
                end = start + 0.5
            words.append({"word": f"word{i}", "start": start, "end": end, "speaker": "A"})
        chunks = worker._chunk_speaker_turn(words, [])
        assert len(chunks) >= 2
        # The first chunk should end around the pause point (110 words)
        first_chunk_word_count = len(chunks[0]["text"].split())
        assert first_chunk_word_count == 110


class TestChunkTextOnly:
    def test_uses_text_splitter(self, worker):
        """_chunk_text_only uses RecursiveCharacterTextSplitter."""
        text = " ".join(["word"] * 500)  # ~2500 chars
        entities = [{"name": "TestEntity"}]
        chunks = worker._chunk_text_only(text, entities)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.speaker is None
            assert chunk.start_s is None
            assert "TestEntity" in chunk.entity_names

    def test_empty_text(self, worker):
        """_chunk_text_only with empty text returns empty list."""
        # chunk_transcript guards empty text, but _chunk_text_only should handle it
        chunks = worker._chunk_text_only("", [])
        assert chunks == []

    def test_short_text_single_chunk(self, worker):
        """Short text produces a single chunk."""
        text = "The honourable member addressed the house on budget matters."
        entities = [{"name": "budget"}]
        chunks = worker._chunk_text_only(text, entities)
        assert len(chunks) == 1
        assert chunks[0].text == text
        assert chunks[0].ordinal == 0


class TestEmbedChunks:
    @pytest.mark.asyncio
    async def test_batch_embedding(self, worker, mock_embeddings):
        """embed_chunks calls aembed_documents in batches."""
        from app.rag.ingestion import Chunk

        chunks = [
            Chunk(text="test text 1", start_s=0.0, end_s=1.0, speaker=None, ordinal=0),
            Chunk(text="test text 2", start_s=1.0, end_s=2.0, speaker=None, ordinal=1),
        ]
        mock_embeddings.aembed_documents.return_value = [[0.1] * 1024, [0.2] * 1024]

        result = await worker.embed_chunks(chunks)

        assert len(result) == 2
        assert result[0] == [0.1] * 1024
        assert result[1] == [0.2] * 1024
        mock_embeddings.aembed_documents.assert_called()

    @pytest.mark.asyncio
    async def test_no_embeddings_returns_all_none(self, mock_settings, mock_session_factory):
        """embed_chunks returns all None when embeddings client is not configured."""
        from app.rag.ingestion import Chunk

        worker = TranscriptIngestionWorker(mock_session_factory, mock_settings, embeddings=None)
        chunks = [
            Chunk(text="test", start_s=0.0, end_s=1.0, speaker=None, ordinal=0),
        ]
        result = await worker.embed_chunks(chunks)
        assert result == [None]


class TestChunkTranscript:
    def test_empty_text_returns_empty(self, worker):
        """chunk_transcript returns empty list for empty text."""
        assert worker.chunk_transcript("", [], []) == []
        assert worker.chunk_transcript("   ", [], []) == []

    def test_no_words_uses_text_fallback(self, worker):
        """chunk_transcript without word timings uses text-only fallback."""
        text = "The honourable member spoke about education."
        chunks = worker.chunk_transcript(text, [], [{"name": "education"}])
        assert len(chunks) >= 1
        assert all(c.speaker is None for c in chunks)
        assert all(c.start_s is None for c in chunks)

    def test_end_to_end_with_words(self, worker):
        """chunk_transcript with word timings produces speaker-attributed chunks."""
        words = [
            {"word": f"word{i}", "start": float(i), "end": float(i) + 0.5, "speaker": "A"}
            for i in range(50)
        ]
        text = " ".join(w["word"] for w in words)
        entities = [{"name": "entity1", "start": 0.0, "end": 25.0}]
        chunks = worker.chunk_transcript(text, words, entities)
        assert len(chunks) >= 1
        assert chunks[0].speaker == "A"
        assert chunks[0].start_s == 0.0
        assert chunks[0].ordinal == 0
        assert "entity1" in chunks[0].entity_names

    def test_multi_speaker_chunking(self, worker):
        """chunk_transcript respects speaker boundaries."""
        words_a = [
            {"word": f"a{i}", "start": float(i), "end": float(i) + 0.5, "speaker": "A"}
            for i in range(30)
        ]
        words_b = [
            {"word": f"b{i}", "start": float(30 + i), "end": float(30 + i) + 0.5, "speaker": "B"}
            for i in range(30)
        ]
        all_words = words_a + words_b
        text = " ".join(w["word"] for w in all_words)
        chunks = worker.chunk_transcript(text, all_words, [])
        # Should have at least 2 chunks (one per speaker)
        assert len(chunks) >= 2
        speakers = {c.speaker for c in chunks}
        assert "A" in speakers
        assert "B" in speakers


class TestQueueOverflow:
    def test_overflow_drops_and_counts(self, mock_settings, mock_embeddings, mock_session_factory):
        """enqueue drops items when queue is full."""
        worker = TranscriptIngestionWorker(
            session_factory=mock_session_factory,
            settings=mock_settings,
            embeddings=mock_embeddings,
            max_queue_size=5,
        )
        # Fill the queue
        for i in range(5):
            worker.enqueue(i)
        # These should be dropped
        worker.enqueue(100)
        worker.enqueue(101)
        assert worker.dropped_count == 2

    def test_no_overflow_when_within_capacity(self, worker):
        """enqueue does not drop items when within capacity."""
        worker.enqueue(1)
        worker.enqueue(2)
        assert worker.dropped_count == 0

    def test_same_transcript_is_queued_once(self, worker):
        """A save trigger and a reindex for the same id must not ingest it twice."""
        worker.enqueue(7)
        worker.enqueue(7)
        worker.enqueue(8)

        assert worker._queue.qsize() == 2
        assert worker.dropped_count == 0

    @pytest.mark.asyncio
    async def test_transcript_can_be_requeued_once_picked_up(self, worker, monkeypatch):
        from unittest.mock import AsyncMock

        ingest = AsyncMock()
        monkeypatch.setattr(worker, "ingest", ingest)
        worker.enqueue(7)
        await worker._drain_remaining()

        worker.enqueue(7)

        ingest.assert_awaited_once_with(7)
        assert worker._queue.qsize() == 1


class TestPersistCorrectionEvidence:
    """Task 4.1 wiring: build + persist Correction_Evidence at ingestion time.

    The worker persists evidence only from a durable ``metadata['corrections']``
    payload keyed to (transcript_id, version); when that payload is absent it
    writes nothing (Baseline no-evidence, Req 10.5) rather than fabricating
    changes from corrected-word flags. Bedrock/AWS mocked; no network call.
    """

    @pytest.mark.asyncio
    async def test_persists_evidence_from_metadata_corrections(self, worker, monkeypatch):
        """A durable corrections payload is grouped by origin and persisted."""
        import app.rag.ingestion as ingestion_mod

        captured = {}

        async def _fake_persist(session_factory, evidence):
            captured["evidence"] = evidence
            return len(evidence.entries)

        monkeypatch.setattr(ingestion_mod, "persist_correction_evidence", _fake_persist)

        raw_words = [
            {"word": "Akra", "start": 0.0, "end": 0.5},
            {"word": "in", "start": 0.6, "end": 0.7},
            {"word": "twenty", "start": 0.8, "end": 1.0},
        ]
        transcript_data = {
            "corrected_text": "Accra in 2020",
            "word_timings": raw_words,
            "entities": [],
            "version": 4,
            "raw_text": "Akra in twenty",
            "metadata": {
                "raw_words": raw_words,
                "corrections": [
                    {
                        "original": "Akra",
                        "corrected": "Accra",
                        "strategy": "fuzzy",
                        "confidence": 0.92,
                        "entity_kind": "location",
                        "entity_type": "city",
                        "stage": "rule",
                        "outcome": "applied",
                    },
                    {
                        "original": "twenty",
                        "corrected": "2020",
                        "confidence": 1.0,
                        "stage": "year",
                        "outcome": "applied",
                    },
                ],
                "dataset_version": "ds-1",
                "correlation_id": "corr-9",
            },
        }

        await worker._persist_correction_evidence(101, transcript_data)

        evidence = captured["evidence"]
        assert evidence.transcript_id == 101
        assert evidence.version == 4
        assert len(evidence.entries) == 2
        stages = {e.correction_stage.value for e in evidence.entries}
        assert stages == {"rule", "year"}

    @pytest.mark.asyncio
    async def test_no_corrections_payload_persists_nothing(self, worker, monkeypatch):
        """Absent a corrections payload, no evidence is fabricated or written."""
        import app.rag.ingestion as ingestion_mod

        called = {"count": 0}

        async def _fake_persist(session_factory, evidence):
            called["count"] += 1
            return 0

        monkeypatch.setattr(ingestion_mod, "persist_correction_evidence", _fake_persist)

        transcript_data = {
            "corrected_text": "Accra",
            "word_timings": [{"word": "Accra", "start": 0.0, "end": 0.5}],
            "entities": [],
            "version": 1,
            "raw_text": "Akra",
            "metadata": {},  # no corrections payload
        }

        await worker._persist_correction_evidence(202, transcript_data)

        assert called["count"] == 0

    def test_group_correction_records_routes_by_stage_and_outcome(self, worker):
        """vetoed -> vetoed; year/llm by stage; unknown/missing -> applied."""
        raw = [
            {"original": "a", "corrected": "A", "stage": "rule", "outcome": "applied"},
            {"original": "b", "corrected": "B", "stage": "year", "outcome": "applied"},
            {"original": "c", "corrected": "C", "stage": "llm", "outcome": "applied"},
            {"original": "d", "corrected": "D", "stage": "llm", "outcome": "vetoed"},
            {"original": "e", "corrected": "E"},  # missing stage -> applied
        ]
        applied, year, llm, vetoed = worker._group_correction_records(raw)
        assert [r.original for r in applied] == ["a", "e"]
        assert [r.original for r in year] == ["b"]
        assert [r.original for r in llm] == ["c"]
        assert [r.original for r in vetoed] == ["d"]

    def test_group_correction_records_skips_malformed(self, worker):
        """A dict lacking original/corrected text is skipped, not persisted."""
        raw = [
            {"original": "ok", "corrected": "OK"},
            {"corrected": "no original"},
            "not a dict",
            {"original": 123, "corrected": "bad type"},
        ]
        applied, year, llm, vetoed = worker._group_correction_records(raw)
        assert [r.original for r in applied] == ["ok"]
        assert year == [] and llm == [] and vetoed == []


# ---------------------------------------------------------------------------
# Indexed text comes from the saved transcript, including editor saves
# ---------------------------------------------------------------------------


def _timed(words: list[str], speaker: str = "A", start: float = 0.0) -> list[dict]:
    """Word timings 0.5s apart, all one speaker."""
    return [
        {"word": w, "start": start + i * 0.5, "end": start + i * 0.5 + 0.4, "speaker": speaker}
        for i, w in enumerate(words)
    ]


class TestAlignTextToWords:
    """The saved text is what gets indexed; timings only supply speaker/time."""

    def test_matching_text_keeps_each_words_timing(self, worker):
        words = _timed(["the", "house", "adjourned"])
        aligned = worker._align_text_to_words("The house adjourned.", words)

        assert [w["word"] for w in aligned] == ["The", "house", "adjourned."]
        assert [w["start"] for w in aligned] == [0.0, 0.5, 1.0]
        assert all(w["speaker"] == "A" for w in aligned)

    def test_edited_word_is_indexed_with_the_replaced_words_timing(self, worker):
        words = _timed(["the", "minister", "of", "finanse", "spoke"])
        aligned = worker._align_text_to_words("the minister of finance spoke", words)

        assert [w["word"] for w in aligned] == ["the", "minister", "of", "finance", "spoke"]
        assert aligned[3]["start"] == words[3]["start"]

    def test_text_added_in_the_editor_is_indexed(self, worker):
        words = _timed(["motion", "carried"])
        aligned = worker._align_text_to_words("motion carried unanimously", words)

        assert [w["word"] for w in aligned] == ["motion", "carried", "unanimously"]
        # Anchored to the previous word's end: in the same turn, never back in time.
        assert aligned[2]["start"] == aligned[2]["end"] == words[1]["end"]
        assert aligned[2]["speaker"] == "A"

    def test_text_added_before_the_first_word_anchors_to_its_start(self, worker):
        words = _timed(["order", "order"], start=3.0)
        aligned = worker._align_text_to_words("Speaker: order order", words)

        assert aligned[0]["word"] == "Speaker:"
        assert aligned[0]["start"] == aligned[0]["end"] == 3.0

    def test_text_removed_in_the_editor_is_not_indexed(self, worker):
        words = _timed(["um", "the", "bill", "uh", "passed"])
        aligned = worker._align_text_to_words("the bill passed", words)

        assert [w["word"] for w in aligned] == ["the", "bill", "passed"]

    def test_speaker_turns_survive_an_edit(self, worker):
        words = _timed(["question", "time"], speaker="SPEAKER") + _timed(
            ["thank", "you"], speaker="MP", start=5.0
        )
        aligned = worker._align_text_to_words("Question time begins. Thank you", words)

        assert [w["speaker"] for w in aligned] == ["SPEAKER", "SPEAKER", "SPEAKER", "MP", "MP"]


class TestChunkTranscriptUsesSavedText:
    def test_editor_save_with_stale_timings_indexes_the_edited_text(self, worker):
        """Regression: an editor save keeps the previous version's word timings.

        Chunks used to be built from those timed words, so the index kept the
        pre-edit text and the editor's correction never reached search.
        """
        stale_words = _timed(["the", "honourable", "member", "for", "akra", "central"])
        edited_text = "The Honourable Member for Accra Central"

        chunks = worker.chunk_transcript(edited_text, stale_words, [])

        indexed = " ".join(chunk.text for chunk in chunks)
        assert "Accra" in indexed
        assert "akra" not in indexed
        assert chunks[0].start_s == 0.0
        assert chunks[0].speaker == "A"

    def test_postprocessed_text_punctuation_is_indexed(self, worker):
        words = _timed(["mr", "speaker", "i", "rise"])
        chunks = worker.chunk_transcript("Mr. Speaker, I rise.", words, [])

        assert chunks[0].text == "Mr. Speaker, I rise."


def _transcript_data(*, is_latest: bool, version: int = 2) -> dict:
    return {
        "corrected_text": "The House adjourned",
        "word_timings": _timed(["the", "house", "adjourned"]),
        "entities": [],
        "version": version,
        "raw_text": "the house adjourned",
        "metadata": {},
        "is_latest": is_latest,
    }


class TestIngestOnlyIndexesTheLatestVersion:
    @pytest.mark.asyncio
    async def test_stale_version_is_not_embedded_or_indexed(self, worker, monkeypatch):
        """A late ingest of an older save must not replace the newer version's chunks."""
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            worker, "_load_transcript", AsyncMock(return_value=_transcript_data(is_latest=False))
        )
        embed = AsyncMock()
        replace = AsyncMock()
        evidence = AsyncMock()
        monkeypatch.setattr(worker, "embed_chunks", embed)
        monkeypatch.setattr(worker, "_replace_chunks", replace)
        monkeypatch.setattr(worker, "_persist_correction_evidence", evidence)

        await worker.ingest(41)

        embed.assert_not_awaited()
        replace.assert_not_awaited()
        # Evidence for that version is still recorded.
        evidence.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_latest_version_replaces_the_records_chunks(self, worker, monkeypatch):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            worker, "_load_transcript", AsyncMock(return_value=_transcript_data(is_latest=True))
        )
        monkeypatch.setattr(worker, "embed_chunks", AsyncMock(return_value=[[0.1] * 1024]))
        replace = AsyncMock(return_value=True)
        monkeypatch.setattr(worker, "_replace_chunks", replace)
        monkeypatch.setattr(worker, "_persist_correction_evidence", AsyncMock())

        await worker.ingest(42)

        replace.assert_awaited_once()
        transcript_id, chunks, _embeddings = replace.await_args.args
        assert transcript_id == 42
        assert chunks[0].text == "The House adjourned"


def _scripted_session_factory(results: list):
    """Session factory whose execute() returns `results` in order, recording SQL."""
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=results)
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context_manager)
    factory.session = session
    return factory


def _executed_sql(factory) -> list[str]:
    return [str(call.args[0]) for call in factory.session.execute.call_args_list]


class TestReplaceChunks:
    def _chunks(self):
        from app.rag.ingestion import Chunk

        return [Chunk(text="The House adjourned", start_s=0.0, end_s=1.4, speaker="A")]

    @pytest.mark.asyncio
    async def test_newer_version_wins_under_the_record_lock(self, mock_settings, mock_embeddings):
        """Re-checked inside the locked transaction: nothing is deleted or written."""
        from unittest.mock import MagicMock

        factory = _scripted_session_factory(
            [
                MagicMock(fetchone=lambda: (7, 2)),  # this transcript: record 7, v2
                MagicMock(),  # pg_advisory_xact_lock
                MagicMock(scalar=lambda: 3),  # latest version is v3
            ]
        )
        worker = TranscriptIngestionWorker(factory, mock_settings, embeddings=mock_embeddings)

        replaced = await worker._replace_chunks(41, self._chunks(), [[0.1] * 1024])

        assert replaced is False
        sql = _executed_sql(factory)
        assert "pg_advisory_xact_lock" in sql[1]
        assert not any("DELETE FROM transcript_chunk" in s for s in sql)
        assert not any("INSERT INTO transcript_chunk" in s for s in sql)

    @pytest.mark.asyncio
    async def test_latest_version_deletes_then_inserts_in_one_transaction(
        self, mock_settings, mock_embeddings
    ):
        from unittest.mock import MagicMock

        factory = _scripted_session_factory(
            [
                MagicMock(fetchone=lambda: (7, 3)),
                MagicMock(),
                MagicMock(scalar=lambda: 3),
                MagicMock(),  # DELETE
                MagicMock(),  # INSERT
            ]
        )
        worker = TranscriptIngestionWorker(factory, mock_settings, embeddings=mock_embeddings)

        replaced = await worker._replace_chunks(42, self._chunks(), [[0.1] * 1024])

        assert replaced is True
        sql = _executed_sql(factory)
        assert "DELETE FROM transcript_chunk" in sql[3]
        assert "INSERT INTO transcript_chunk" in sql[4]
        # Lock, delete, and insert share the single session.begin() transaction.
        factory.session.begin.assert_called_once()
        delete_params = factory.session.execute.call_args_list[3].args[1]
        assert delete_params == {"record_id": 7}

    @pytest.mark.asyncio
    async def test_failure_raises_transient_so_the_write_is_retried(
        self, mock_settings, mock_embeddings
    ):
        """A rolled-back write keeps the previous chunks, and must not be confused
        with "superseded" (False), which is never retried."""
        from unittest.mock import MagicMock

        from app.rag.ingestion import IngestTransientError

        factory = _scripted_session_factory(
            [
                MagicMock(fetchone=lambda: (7, 3)),
                MagicMock(),
                MagicMock(scalar=lambda: 3),
                MagicMock(),
                RuntimeError("insert failed"),
            ]
        )
        worker = TranscriptIngestionWorker(factory, mock_settings, embeddings=mock_embeddings)

        with pytest.raises(IngestTransientError):
            await worker._replace_chunks(42, self._chunks(), [[0.1] * 1024])
