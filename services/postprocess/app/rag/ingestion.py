"""Transcript ingestion worker — chunking, embedding, and indexing.

Processes completed transcripts into speaker-turn-aware chunks, generates
embeddings via Amazon Titan Text Embeddings V2, and stores them in the
transcript_chunk table for hybrid retrieval.

Follows the same bounded-queue + background-task pattern as
CorrectionHistoryWriter: ingestion is fully async, non-blocking, and
overflow is logged rather than allowed to stall request handlers.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from langchain_aws import BedrockEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings

logger = structlog.get_logger("rag.ingestion")

# Chunking parameters
_MIN_CHUNK_WORDS = 100
_MAX_CHUNK_WORDS = 200
# Pause threshold (seconds) for splitting within a speaker turn
_PAUSE_THRESHOLD_S = 2.0
# Embedding model identifier stored per chunk for future model swaps
_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
# Titan Text Embeddings V2 produces 1024-dimensional vectors
_EMBEDDING_DIMENSION = 1024


@dataclass(frozen=True)
class Chunk:
    """A speaker-turn-aware transcript segment ready for embedding."""

    text: str
    start_s: float | None
    end_s: float | None
    speaker: str | None
    entity_names: list[str] = field(default_factory=list)
    ordinal: int = 0


class TranscriptIngestionWorker:
    """Async worker that chunks and indexes transcripts.

    Follows the same bounded-queue + background-task pattern as
    CorrectionHistoryWriter. Enqueue a transcript_id and the worker
    will chunk, embed, and store asynchronously without blocking callers.

    Usage::

        worker = TranscriptIngestionWorker(session_factory, settings)
        worker.start()
        # ... after transcript persistence ...
        worker.enqueue(transcript_id)
        # ... on shutdown ...
        await worker.stop()
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        max_queue_size: int = 100,
        embeddings: BedrockEmbeddings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=max_queue_size)
        self._dropped_count: int = 0
        self._task: asyncio.Task[None] | None = None
        self._embeddings = embeddings
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,  # ~200 words * 5 chars/word
            chunk_overlap=50,
            separators=["\n\n", "\n", ". ", " "],
        )

    @property
    def dropped_count(self) -> int:
        """Number of ingest requests dropped due to queue overflow."""
        return self._dropped_count

    def start(self) -> None:
        """Launch the background ingestion worker task."""
        self._task = asyncio.create_task(self._worker_loop(), name="rag-ingestion-worker")

    async def stop(self) -> None:
        """Cancel the background task gracefully, draining remaining items."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        # Drain any remaining transcript IDs
        await self._drain_remaining()

    def enqueue(self, transcript_id: int) -> None:
        """Queue a transcript for ingestion. Drops if full, never blocks.

        Overflow is logged at WARNING and counted. Ingestion is
        best-effort — dropping an item means the transcript stays
        unindexed but is still persisted and queryable via direct DB access.
        """
        try:
            self._queue.put_nowait(transcript_id)
        except asyncio.QueueFull:
            self._dropped_count += 1
            logger.warning(
                "rag.ingestion.queue_overflow",
                transcript_id=transcript_id,
                dropped_total=self._dropped_count,
                queue_maxsize=self._queue.maxsize,
            )

    async def ingest(self, transcript_id: int) -> None:
        """Chunk, embed, and index a single transcript.

        Loads transcript data from the DB, chunks it with speaker-turn
        awareness, generates embeddings via Bedrock Titan V2, and stores
        chunks in the transcript_chunk table.

        On embedding failure per chunk: logs the error, stores the chunk
        without an embedding (marked as unindexed), and continues with
        remaining chunks.
        """
        logger.info("rag.ingestion.start", transcript_id=transcript_id)

        # Load transcript data from DB
        transcript_data = await self._load_transcript(transcript_id)
        if transcript_data is None:
            logger.error(
                "rag.ingestion.transcript_not_found",
                transcript_id=transcript_id,
            )
            return

        corrected_text = transcript_data["corrected_text"]
        word_timings = transcript_data["word_timings"]
        entities = transcript_data["entities"]

        # Chunk the transcript
        chunks = self.chunk_transcript(corrected_text, word_timings, entities)
        if not chunks:
            logger.warning(
                "rag.ingestion.no_chunks",
                transcript_id=transcript_id,
            )
            return

        # Generate embeddings
        embeddings = await self.embed_chunks(chunks)

        # Delete any existing chunks for this transcript (re-ingestion on edit)
        await self._delete_existing_chunks(transcript_id)

        # Store chunks in DB
        await self._store_chunks(transcript_id, chunks, embeddings)

        logger.info(
            "rag.ingestion.complete",
            transcript_id=transcript_id,
            chunk_count=len(chunks),
        )

    def chunk_transcript(
        self,
        text_content: str,
        words: list[dict],
        entities: list[dict],
    ) -> list[Chunk]:
        """Split transcript into speaker-turn-aware chunks of 100-200 words.

        Chunking rules:
        1. Never cross speaker transitions — each chunk belongs to one speaker.
        2. Split at pauses > 2 seconds when possible within a turn.
        3. Target 100-200 words per chunk.
        4. The final chunk of a speaker turn may be < 100 words if the
           remaining text in that turn is fewer than 100 words.

        Args:
            text_content: The corrected transcript text.
            words: Word-level timing data from ASR. Each dict has at minimum:
                   {"word": str, "start": float, "end": float, "speaker": str|None}
            entities: Entity data from NER. Each dict has at minimum:
                      {"name": str, "start": float, "end": float}

        Returns:
            A list of Chunk objects ordered by their position in the transcript.
        """
        if not text_content or not text_content.strip():
            return []

        # If no word timings, fall back to simple text splitting
        if not words:
            return self._chunk_text_only(text_content, entities)

        # Group words by speaker turn
        turns = self._group_speaker_turns(words)

        # Build entity lookup by time range for efficient entity assignment
        entity_lookup = self._build_entity_lookup(entities)

        chunks: list[Chunk] = []
        ordinal = 0

        for turn in turns:
            turn_chunks = self._chunk_speaker_turn(turn, entity_lookup)
            for chunk in turn_chunks:
                chunks.append(
                    Chunk(
                        text=chunk["text"],
                        start_s=chunk["start_s"],
                        end_s=chunk["end_s"],
                        speaker=chunk["speaker"],
                        entity_names=chunk["entity_names"],
                        ordinal=ordinal,
                    )
                )
                ordinal += 1

        return chunks

    async def embed_chunks(self, chunks: list[Chunk]) -> list[list[float] | None]:
        """Generate embeddings for chunks via Bedrock Titan Text Embeddings V2.

        Uses batch embedding (aembed_documents) for throughput — processes
        chunks in batches of `rag_embedding_batch_size` (default 10). Each
        batch is retried up to `rag_embedding_max_retries` times with
        exponential backoff before falling back to per-chunk embedding.

        On final failure per chunk: logs the error, returns None for that
        chunk's embedding (marking it as unindexed), and continues.

        Args:
            chunks: List of Chunk objects to embed.

        Returns:
            A list of embedding vectors (or None on failure) parallel to the
            input chunks list.
        """
        if self._embeddings is None:
            logger.warning("rag.ingestion.embeddings_not_configured")
            return [None] * len(chunks)

        batch_size = (
            self._settings.rag_embedding_batch_size if self._settings else 10
        )
        max_retries = (
            self._settings.rag_embedding_max_retries if self._settings else 2
        )

        embeddings: list[list[float] | None] = []

        # Process in batches for throughput
        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start : batch_start + batch_size]
            batch_texts = [chunk.text for chunk in batch]

            batch_embeddings = await self._embed_batch_with_retry(
                batch_texts, max_retries
            )

            if batch_embeddings is not None:
                # Batch succeeded — all embeddings are valid
                embeddings.extend(batch_embeddings)
            else:
                # Batch failed after retries — fall back to per-chunk embedding
                logger.warning(
                    "rag.ingestion.batch_failed_fallback_to_individual",
                    batch_start=batch_start,
                    batch_size=len(batch),
                )
                for chunk in batch:
                    embedding = await self._embed_single_with_retry(
                        chunk.text, chunk.ordinal, max_retries
                    )
                    embeddings.append(embedding)

        return embeddings

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _embed_batch_with_retry(
        self,
        texts: list[str],
        max_retries: int,
    ) -> list[list[float]] | None:
        """Embed a batch of texts with exponential-backoff retry.

        Returns the list of embedding vectors on success, or None if all
        retries are exhausted.
        """
        for attempt in range(max_retries + 1):
            try:
                result = await self._embeddings.aembed_documents(texts)
                return result
            except Exception:
                if attempt < max_retries:
                    backoff = 2 ** attempt  # 1s, 2s, 4s...
                    logger.warning(
                        "rag.ingestion.batch_embedding_retry",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        backoff_s=backoff,
                        batch_size=len(texts),
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "rag.ingestion.batch_embedding_exhausted",
                        batch_size=len(texts),
                        exc_info=True,
                    )
        return None

    async def _embed_single_with_retry(
        self,
        text_content: str,
        ordinal: int,
        max_retries: int,
    ) -> list[float] | None:
        """Embed a single text with retry, returning None on final failure."""
        for attempt in range(max_retries + 1):
            try:
                result = await self._embeddings.aembed_documents([text_content])
                return result[0]
            except Exception:
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "rag.ingestion.single_embedding_retry",
                        attempt=attempt + 1,
                        ordinal=ordinal,
                        backoff_s=backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "rag.ingestion.embedding_failed",
                        chunk_ordinal=ordinal,
                        chunk_text_preview=text_content[:80],
                        exc_info=True,
                    )
        return None

    def _group_speaker_turns(self, words: list[dict]) -> list[list[dict]]:
        """Group consecutive words by speaker into turns.

        A new turn starts whenever the speaker label changes.
        """
        if not words:
            return []

        turns: list[list[dict]] = []
        current_turn: list[dict] = [words[0]]

        for word in words[1:]:
            current_speaker = current_turn[0].get("speaker")
            word_speaker = word.get("speaker")

            if word_speaker != current_speaker:
                turns.append(current_turn)
                current_turn = [word]
            else:
                current_turn.append(word)

        if current_turn:
            turns.append(current_turn)

        return turns

    def _chunk_speaker_turn(
        self,
        turn_words: list[dict],
        entity_lookup: list[dict],
    ) -> list[dict]:
        """Split a single speaker turn into chunks of 100-200 words.

        Prefers splitting at pauses > 2s. If no suitable pause is found,
        splits at _MAX_CHUNK_WORDS boundary. Final chunk of a turn may
        be < 100 words.
        """
        if not turn_words:
            return []

        speaker = turn_words[0].get("speaker")
        chunks: list[dict] = []
        remaining = turn_words

        while remaining:
            if len(remaining) <= _MAX_CHUNK_WORDS:
                # All remaining words fit in one chunk
                chunk = self._make_chunk_from_words(remaining, speaker, entity_lookup)
                chunks.append(chunk)
                break

            # Try to find a pause-based split point between _MIN_CHUNK_WORDS
            # and _MAX_CHUNK_WORDS
            split_idx = self._find_pause_split(remaining)

            if split_idx is not None:
                chunk_words = remaining[:split_idx]
                remaining = remaining[split_idx:]
            else:
                # No pause found; split at _MAX_CHUNK_WORDS
                chunk_words = remaining[:_MAX_CHUNK_WORDS]
                remaining = remaining[_MAX_CHUNK_WORDS:]

            chunk = self._make_chunk_from_words(chunk_words, speaker, entity_lookup)
            chunks.append(chunk)

        return chunks

    def _find_pause_split(self, words: list[dict]) -> int | None:
        """Find the best split point based on pauses > 2s.

        Searches between _MIN_CHUNK_WORDS and _MAX_CHUNK_WORDS for the
        longest pause. Returns the index after the pause (i.e., the start
        of the next chunk), or None if no suitable pause is found.
        """
        best_pause = 0.0
        best_idx: int | None = None

        for i in range(_MIN_CHUNK_WORDS, min(_MAX_CHUNK_WORDS, len(words))):
            prev_end = words[i - 1].get("end", 0.0)
            curr_start = words[i].get("start", 0.0)
            pause_duration = curr_start - prev_end

            if pause_duration >= _PAUSE_THRESHOLD_S and pause_duration > best_pause:
                best_pause = pause_duration
                best_idx = i

        return best_idx

    def _make_chunk_from_words(
        self,
        chunk_words: list[dict],
        speaker: str | None,
        entity_lookup: list[dict],
    ) -> dict:
        """Build a chunk dict from a list of word dicts."""
        text_content = " ".join(w.get("word", "") for w in chunk_words)
        start_s = chunk_words[0].get("start")
        end_s = chunk_words[-1].get("end")

        # Find entities that overlap with this chunk's time range
        entity_names = self._find_overlapping_entities(start_s, end_s, entity_lookup)

        return {
            "text": text_content,
            "start_s": start_s,
            "end_s": end_s,
            "speaker": speaker,
            "entity_names": entity_names,
        }

    def _build_entity_lookup(self, entities: list[dict]) -> list[dict]:
        """Build a sorted list of entities for efficient overlap lookup."""
        # Each entity should have at minimum: name, start, end
        lookup = []
        for entity in entities:
            if "name" in entity:
                lookup.append(
                    {
                        "name": entity["name"],
                        "start": entity.get("start", 0.0),
                        "end": entity.get("end", 0.0),
                    }
                )
        # Sort by start time for efficient range checks
        lookup.sort(key=lambda e: e["start"])
        return lookup

    def _find_overlapping_entities(
        self,
        start_s: float | None,
        end_s: float | None,
        entity_lookup: list[dict],
    ) -> list[str]:
        """Find entity names that overlap with the given time range."""
        if start_s is None or end_s is None:
            return []

        names: list[str] = []
        seen: set[str] = set()

        for entity in entity_lookup:
            e_start = entity["start"]
            e_end = entity["end"]

            # Check for overlap: entity overlaps chunk if
            # entity_start < chunk_end AND entity_end > chunk_start
            if e_start < end_s and e_end > start_s:
                name = entity["name"]
                if name not in seen:
                    names.append(name)
                    seen.add(name)

        return names

    def _chunk_text_only(self, text_content: str, entities: list[dict]) -> list[Chunk]:
        """Fallback chunking when no word timings are available.

        Uses RecursiveCharacterTextSplitter for intelligent splitting
        with overlap and sentence-aware boundaries.
        """
        # Collect all entity names (no time-based filtering possible)
        all_entity_names = list({e["name"] for e in entities if "name" in e})

        sub_texts = self._text_splitter.split_text(text_content)

        chunks: list[Chunk] = []
        for ordinal, text_segment in enumerate(sub_texts):
            chunks.append(
                Chunk(
                    text=text_segment,
                    start_s=None,
                    end_s=None,
                    speaker=None,
                    entity_names=all_entity_names,
                    ordinal=ordinal,
                )
            )
        return chunks

    async def _load_transcript(self, transcript_id: int) -> dict | None:
        """Load transcript data from the database."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    text(
                        "SELECT corrected_text, word_timings, entities "
                        "FROM transcript WHERE id = :id"
                    ),
                    {"id": transcript_id},
                )
                row = result.fetchone()
                if row is None:
                    return None

                corrected_text = row[0]
                word_timings = row[1] if row[1] else []
                entities = row[2] if row[2] else []

                # word_timings and entities are JSONB columns; they may
                # already be parsed or may be strings depending on driver
                if isinstance(word_timings, str):
                    word_timings = json.loads(word_timings)
                if isinstance(entities, str):
                    entities = json.loads(entities)

                return {
                    "corrected_text": corrected_text,
                    "word_timings": word_timings,
                    "entities": entities,
                }
        except Exception:
            logger.error(
                "rag.ingestion.load_transcript_failed",
                transcript_id=transcript_id,
                exc_info=True,
            )
            return None

    async def _delete_existing_chunks(self, transcript_id: int) -> None:
        """Clear indexed chunks for this transcript's record, not just this version.

        A transcript edit inserts a new `transcript` row rather than updating the
        existing one, so scoping the delete to `transcript_id` alone left every
        earlier version's chunks in the index. Retrieval has no notion of which
        version supersedes which, so those rows kept competing with the text that
        replaced them and kept accumulating on every save.

        Deleting by record means the index holds exactly one generation of chunks
        per record: the one being written now.
        """
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "DELETE FROM transcript_chunk "
                        "WHERE transcript_id IN ("
                        "    SELECT id FROM transcript"
                        "    WHERE record_id = ("
                        "        SELECT record_id FROM transcript WHERE id = :transcript_id"
                        "    )"
                        ")"
                    ),
                    {"transcript_id": transcript_id},
                )
        except Exception:
            logger.error(
                "rag.ingestion.delete_chunks_failed",
                transcript_id=transcript_id,
                exc_info=True,
            )

    async def _store_chunks(
        self,
        transcript_id: int,
        chunks: list[Chunk],
        embeddings: list[list[float] | None],
    ) -> None:
        """Persist chunks to the transcript_chunk table.

        Chunks with a None embedding are stored without the embedding
        vector and without an indexed_at timestamp (marked as unindexed).
        """
        try:
            async with self._session_factory() as session, session.begin():
                for chunk, embedding in zip(chunks, embeddings, strict=True):
                    indexed_at = datetime.now(UTC) if embedding else None
                    # Format embedding as pgvector literal or NULL
                    embedding_value = str(embedding) if embedding else None

                    await session.execute(
                        text(
                            "INSERT INTO transcript_chunk "
                            "(transcript_id, ordinal, text, start_s, end_s, "
                            "speaker, entity_names, embedding, model_id, indexed_at) "
                            "VALUES "
                            "(:transcript_id, :ordinal, :text, :start_s, :end_s, "
                            ":speaker, :entity_names, :embedding, :model_id, :indexed_at)"
                        ),
                        {
                            "transcript_id": transcript_id,
                            "ordinal": chunk.ordinal,
                            "text": chunk.text,
                            "start_s": chunk.start_s,
                            "end_s": chunk.end_s,
                            "speaker": chunk.speaker,
                            "entity_names": chunk.entity_names,
                            "embedding": embedding_value,
                            "model_id": _EMBEDDING_MODEL_ID,
                            "indexed_at": indexed_at,
                        },
                    )

            logger.debug(
                "rag.ingestion.chunks_stored",
                transcript_id=transcript_id,
                count=len(chunks),
                unindexed=sum(1 for e in embeddings if e is None),
            )
        except Exception:
            logger.error(
                "rag.ingestion.store_chunks_failed",
                transcript_id=transcript_id,
                exc_info=True,
            )

    async def _worker_loop(self) -> None:
        """Background loop consuming transcript IDs and processing them."""
        while True:
            try:
                transcript_id = await self._queue.get()
                await self.ingest(transcript_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("rag.ingestion.worker_error", exc_info=True)
                # Brief backoff on unexpected errors to avoid tight loop
                await asyncio.sleep(1.0)

    async def _drain_remaining(self) -> None:
        """Drain and process any transcript IDs left in the queue."""
        while not self._queue.empty():
            try:
                transcript_id = self._queue.get_nowait()
                await self.ingest(transcript_id)
            except asyncio.QueueEmpty:
                break
            except Exception:
                logger.error(
                    "rag.ingestion.drain_error",
                    exc_info=True,
                )
