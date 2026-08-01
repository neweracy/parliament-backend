"""Tests for app/rag/ingestion.py — transcript ingestion worker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.ingestion import Chunk, TranscriptIngestionWorker


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
            words.append(
                {"word": f"word{i}", "start": start, "end": end, "speaker": "A"}
            )
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


class TestGenerateEmbedding:
    @pytest.mark.asyncio
    async def test_calls_embeddings(self, worker, mock_embeddings):
        """_generate_embedding calls aembed_documents."""
        result = await worker._generate_embedding("test text")
        mock_embeddings.aembed_documents.assert_called_once_with(["test text"])
        assert result == [0.1] * 1024

    @pytest.mark.asyncio
    async def test_no_embeddings_raises(self, mock_settings, mock_session_factory):
        """_generate_embedding raises when embeddings not configured."""
        worker = TranscriptIngestionWorker(
            mock_session_factory, mock_settings, embeddings=None
        )
        with pytest.raises(RuntimeError, match="not configured"):
            await worker._generate_embedding("test")


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
