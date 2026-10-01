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
import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from langchain_aws import BedrockEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.correction.evidence_builder import build_correction_evidence
from app.correction.evidence_writer import persist_correction_evidence
from app.correction.source_ids import assign_source_word_ids
from app.models.entities import CorrectionRecord, EntityKind, EntityType, MatchStrategy

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

# Strips punctuation for token comparison when aligning saved text to timings,
# so "Accra," in the text still matches the timed word "Accra".
_ALIGN_STRIP_RE = re.compile(r"[^\w']+")


def _align_key(token: str) -> str:
    """Normalize a token for alignment: case- and punctuation-insensitive."""
    return _ALIGN_STRIP_RE.sub("", token.lower())


def _correction_record_from_dict(raw: dict) -> CorrectionRecord | None:
    """Map a persisted correction dict to a :class:`CorrectionRecord` (task 4.1).

    Reuses the defensive enum-coercion the pipeline applies when building the
    corrections list (``pipeline._build_corrections``): an unrecognized
    ``strategy``/``entity_kind``/``entity_type`` falls back to a safe default
    rather than raising, so one malformed persisted correction never aborts the
    whole version's evidence persistence. Accepts both snake_case and camelCase
    keys so the record round-trips whether it was persisted from the Python
    response (snake) or the Gateway boundary (camel).

    Returns ``None`` when the dict lacks the ``original``/``corrected`` text a
    Correction_Entry requires — such a row carries no reviewable change.
    """
    original = raw.get("original")
    corrected = raw.get("corrected")
    if not isinstance(original, str) or not isinstance(corrected, str):
        return None

    strategy_raw = str(raw.get("strategy") or "")
    kind_raw = str(raw.get("entity_kind") or raw.get("entityKind") or "")
    type_raw = str(raw.get("entity_type") or raw.get("entityType") or "")
    confidence_raw = raw.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    return CorrectionRecord(
        original=original,
        corrected=corrected,
        strategy=(
            MatchStrategy(strategy_raw)
            if strategy_raw in MatchStrategy.__members__
            else MatchStrategy.exact
        ),
        confidence=confidence,
        entity_kind=(
            EntityKind(kind_raw)
            if kind_raw in EntityKind.__members__
            else EntityKind.location
        ),
        entity_type=(
            EntityType(type_raw)
            if type_raw in EntityType.__members__
            else EntityType.supplementary
        ),
    )


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
        # Transcript ids queued but not yet ingested. A save and a /rag/reindex
        # (or a repeated trigger) for the same id would otherwise ingest it
        # twice — double the embedding cost for an identical result.
        self._pending: set[int] = set()
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
        if transcript_id in self._pending:
            logger.debug("rag.ingestion.already_queued", transcript_id=transcript_id)
            return
        try:
            self._queue.put_nowait(transcript_id)
            self._pending.add(transcript_id)
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

        # The saved text is the source of truth for what gets indexed: the
        # postprocessed text on first transcription, and the editor's text on
        # every save (each save is a new transcript version).
        corrected_text = transcript_data["corrected_text"]
        word_timings = transcript_data["word_timings"]
        entities = transcript_data["entities"]

        # Only the record's latest version belongs in the index (retrieval reads
        # only the latest, and the delete below is record-wide). A late ingest of
        # an older version must not replace the newer one, so bail out before
        # paying for embeddings. Evidence for this version is still persisted.
        if not transcript_data["is_latest"]:
            logger.info(
                "rag.ingestion.skipped_stale_version",
                transcript_id=transcript_id,
                version=transcript_data["version"],
            )
            await self._persist_correction_evidence(transcript_id, transcript_data)
            return

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

        # Swap the record's indexed chunks for this version's, atomically and
        # only if this is still the latest version (re-checked under a lock).
        replaced = await self._replace_chunks(transcript_id, chunks, embeddings)
        if not replaced:
            await self._persist_correction_evidence(transcript_id, transcript_data)
            return

        # Persist Correction_Evidence keyed to (transcript_id, version)
        # (transcript-evidence-navigation task 4.1, Req 1.6, 8.5). Python is the
        # sole writer of the correction table; this runs on the ingestion flow
        # already triggered with the transcript_id (design Decision 2). Isolated
        # from chunk indexing: a failure here logs and returns without affecting
        # the chunks just stored or the transcript row.
        await self._persist_correction_evidence(transcript_id, transcript_data)

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

        # Chunk text always comes from `text_content` (the saved transcript),
        # never from the timed words. An editor save stores new text but carries
        # the previous version's word timings forward, so chunking the timed
        # words indexed the pre-edit text and editor changes never reached RAG.
        # Aligning the saved text onto the timings keeps speaker turns and
        # timestamps for citations while indexing exactly what was saved.
        words = self._align_text_to_words(text_content, words)

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

    def _align_text_to_words(self, text_content: str, words: list[dict]) -> list[dict]:
        """Re-time the saved text's tokens using the stored word timings.

        Returns one word dict per whitespace token of `text_content`, in text
        order, whose `word` is that saved token and whose start/end/speaker come
        from the timed word it lines up with. Alignment is a diff of the two
        token sequences (case- and punctuation-insensitive):

        - equal:   a token keeps its own word's timing and speaker;
        - replace: an edited span spreads across the words it replaced;
        - delete:  text an editor added, with no timed word, borrows the
                   neighbouring word's timing so it stays in the right turn;
        - insert:  timed words an editor removed are dropped — they are no
                   longer in the transcript, so they must not be indexed.

        When the text and timings agree (the usual postprocessed first version)
        this is a token-for-token copy, so speaker-turn chunking is unchanged.
        """
        tokens = text_content.split()
        if not tokens or not words:
            return []

        text_keys = [_align_key(token) for token in tokens]
        word_keys = [_align_key(str(word.get("word", ""))) for word in words]

        def timed(token: str, source: dict, *, at: str | None = None) -> dict:
            if at == "end":
                start = end = source.get("end", source.get("start"))
            elif at == "start":
                start = end = source.get("start", source.get("end"))
            else:
                start, end = source.get("start"), source.get("end")
            return {"word": token, "start": start, "end": end, "speaker": source.get("speaker")}

        aligned: list[dict] = []
        matcher = difflib.SequenceMatcher(None, text_keys, word_keys, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                aligned.extend(timed(tokens[i1 + k], words[j1 + k]) for k in range(i2 - i1))
            elif tag == "replace":
                span = words[j1:j2]
                count = i2 - i1
                aligned.extend(
                    timed(tokens[i1 + k], span[min(k * len(span) // count, len(span) - 1)])
                    for k in range(count)
                )
            elif tag == "delete":
                # Added text: anchor to the previous timed word (or the next one
                # at the very start), zero-length so it never reorders time.
                if j1 > 0:
                    anchor, at = words[j1 - 1], "end"
                else:
                    anchor, at = words[min(j1, len(words) - 1)], "start"
                aligned.extend(timed(tokens[k], anchor, at=at) for k in range(i1, i2))
            # "insert": words removed from the text — intentionally not indexed.

        return aligned

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
                        "SELECT t.corrected_text, t.word_timings, t.entities, "
                        "t.version, t.raw_text, t.metadata, "
                        "t.version = ("
                        "    SELECT MAX(version) FROM transcript WHERE record_id = t.record_id"
                        ") AS is_latest "
                        "FROM transcript t WHERE t.id = :id"
                    ),
                    {"id": transcript_id},
                )
                row = result.fetchone()
                if row is None:
                    return None

                corrected_text = row[0]
                word_timings = row[1] if row[1] else []
                entities = row[2] if row[2] else []
                version = row[3] if row[3] is not None else 1
                raw_text = row[4] if row[4] else ""
                metadata = row[5] if row[5] else {}
                # The locked re-check in _replace_chunks is the authoritative
                # guard; this early check only avoids embedding a stale version.
                is_latest = bool(row[6]) if row[6] is not None else True

                # word_timings, entities and metadata are JSONB columns; they may
                # already be parsed or may be strings depending on driver
                if isinstance(word_timings, str):
                    word_timings = json.loads(word_timings)
                if isinstance(entities, str):
                    entities = json.loads(entities)
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)

                return {
                    "corrected_text": corrected_text,
                    "word_timings": word_timings,
                    "entities": entities,
                    "version": version,
                    "raw_text": raw_text,
                    "metadata": metadata,
                    "is_latest": is_latest,
                }
        except Exception:
            logger.error(
                "rag.ingestion.load_transcript_failed",
                transcript_id=transcript_id,
                exc_info=True,
            )
            return None

    async def _replace_chunks(
        self,
        transcript_id: int,
        chunks: list[Chunk],
        embeddings: list[list[float] | None],
    ) -> bool:
        """Swap the record's indexed chunks for this version's, in one transaction.

        Returns False (writing nothing) when this version is no longer the
        record's latest — a newer save already owns the index.

        - Record-wide delete: an edit inserts a new `transcript` row rather than
          updating the old one, so deleting only this `transcript_id` left every
          earlier version's chunks in the index, competing with the text that
          replaced them. The index holds exactly one generation per record.
        - One transaction: delete and insert used to commit separately, so a
          failed insert left the record with no chunks at all — invisible to
          search until something re-ingested it.
        - Per-record advisory lock + latest-version re-check: ingests for two
          saves of the same record can run concurrently (one queue per worker
          process). Without serialising them, an older version finishing last
          deleted the newer version's chunks and wrote its own, which retrieval
          then ignored (it reads the latest version only) — the record vanished
          from search. The lock is namespaced and released at commit.

        Chunks with a None embedding are stored without the embedding vector and
        without an indexed_at timestamp (marked as unindexed for /rag/reindex).
        """
        try:
            async with self._session_factory() as session, session.begin():
                result = await session.execute(
                    text("SELECT record_id, version FROM transcript WHERE id = :transcript_id"),
                    {"transcript_id": transcript_id},
                )
                row = result.fetchone()
                if row is None:
                    return False
                record_id, version = row[0], row[1]

                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended('rag_ingest:' || CAST(:record_id AS text), 0))"
                    ),
                    {"record_id": record_id},
                )

                latest = await session.execute(
                    text("SELECT MAX(version) FROM transcript WHERE record_id = :record_id"),
                    {"record_id": record_id},
                )
                latest_version = latest.scalar()
                if latest_version is not None and version < latest_version:
                    logger.info(
                        "rag.ingestion.skipped_stale_version",
                        transcript_id=transcript_id,
                        version=version,
                        latest_version=latest_version,
                    )
                    return False

                await session.execute(
                    text(
                        "DELETE FROM transcript_chunk "
                        "WHERE transcript_id IN ("
                        "    SELECT id FROM transcript WHERE record_id = :record_id"
                        ")"
                    ),
                    {"record_id": record_id},
                )

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
            return True
        except Exception:
            # The transaction rolled back: the previous chunks are still in place,
            # so search keeps working on the last good version.
            logger.error(
                "rag.ingestion.store_chunks_failed",
                transcript_id=transcript_id,
                exc_info=True,
            )
            return False

    async def _persist_correction_evidence(
        self,
        transcript_id: int,
        transcript_data: dict,
    ) -> None:
        """Build and persist Correction_Evidence for this version (task 4.1).

        Runs on the Python ingestion flow triggered with *transcript_id*
        (design Decision 2). Python is the sole writer of ``correction_evidence``
        (Req 1.6, 8.5); the write is keyed to ``(transcript_id, version)``.

        Batch-grouping approach and its limitation
        -------------------------------------------
        The evidence builder (task 2.2) needs the raw ASR words, their stable
        Source_Word_Ids, and the pipeline's correction records grouped by origin
        (rule / year / llm / vetoed). At ingestion time the only durable inputs
        are the persisted ``transcript`` columns: ``raw_text`` (raw ASR text),
        ``word_timings`` (the corrected words), ``metadata``, and ``version``.

        The engine's per-change ``corrections`` array (each carrying
        ``original``/``corrected``/``stage``/``outcome``/``confidence``/entity
        classification) is only durable here when the pipeline/Gateway contract
        persisted it under ``metadata['corrections']``. When that payload is
        present, records are grouped by their ``stage``/``outcome`` fields into
        the builder's batches. When it is ABSENT — the corrections array was not
        carried onto the transcript row — this method persists NO rows rather
        than fabricating changes from the corrected-word flags (which do not
        preserve original text). A version with no persisted evidence is Baseline
        "no evidence" (Req 10.5), which is the correct, truthful state for the
        official record — never a guessed range or invented before/after text.

        The raw ASR word list needed for exact source addressing is likewise
        only durable when carried under ``metadata['raw_words']``; absent it, the
        builder aligns against an empty word list and every entry reports
        ``mapping_confidence = lost`` (Req 2.10) rather than an inferred range.
        """
        metadata = transcript_data.get("metadata") or {}
        if not isinstance(metadata, dict):
            return

        raw_corrections = metadata.get("corrections")
        if not raw_corrections or not isinstance(raw_corrections, list):
            # No durable corrections payload — Baseline "no evidence" (Req 10.5).
            # Do NOT fabricate changes from corrected-word flags.
            logger.debug(
                "rag.ingestion.no_correction_evidence",
                transcript_id=transcript_id,
            )
            return

        # The raw ASR words carry exact source addressing. They are durable only
        # when the contract persisted them; absent, the builder emits lost
        # mappings (Req 2.10) rather than guessing.
        raw_words = metadata.get("raw_words") or transcript_data.get("word_timings") or []
        if not isinstance(raw_words, list):
            raw_words = []
        source_word_ids = assign_source_word_ids(raw_words)

        applied, year, llm, vetoed = self._group_correction_records(raw_corrections)

        dataset_version = metadata.get("dataset_version") or metadata.get("datasetVersion")
        correlation_id = metadata.get("correlation_id") or metadata.get("correlationId")
        model_id = metadata.get("model_id") or metadata.get("modelId")

        evidence = build_correction_evidence(
            transcript_id,
            int(transcript_data.get("version") or 1),
            raw_words,
            source_word_ids,
            applied_records=applied,
            year_records=year,
            llm_records=llm,
            vetoed_records=vetoed,
            correlation_id=str(correlation_id) if correlation_id else None,
            dataset_version=str(dataset_version) if dataset_version else None,
            model_id=str(model_id) if model_id else None,
        )

        written = await persist_correction_evidence(self._session_factory, evidence)
        logger.debug(
            "rag.ingestion.correction_evidence_persisted",
            transcript_id=transcript_id,
            rows=written,
        )

    @staticmethod
    def _group_correction_records(
        raw_corrections: list,
    ) -> tuple[
        list[CorrectionRecord],
        list[CorrectionRecord],
        list[CorrectionRecord],
        list[CorrectionRecord],
    ]:
        """Group persisted correction dicts into (applied, year, llm, vetoed).

        Splits each correction by its ``stage``/``correctionStage`` and
        ``outcome``/``correctionOutcome`` fields into the four builder batches.
        A ``vetoed`` outcome goes to *vetoed* regardless of stage; otherwise the
        record is routed by stage — ``year`` to *year*, ``llm`` to *llm*, and
        everything else (including a missing/unknown stage) to *applied* as the
        best available grouping (documented limitation): the persisted contract
        may not cleanly split rule/year/llm origins, and inventing an origin is
        never acceptable for the official record.
        """
        applied: list[CorrectionRecord] = []
        year: list[CorrectionRecord] = []
        llm: list[CorrectionRecord] = []
        vetoed: list[CorrectionRecord] = []

        for raw in raw_corrections:
            if not isinstance(raw, dict):
                continue
            record = _correction_record_from_dict(raw)
            if record is None:
                continue
            stage = str(raw.get("stage") or raw.get("correctionStage") or "").lower()
            outcome = str(
                raw.get("outcome") or raw.get("correctionOutcome") or ""
            ).lower()
            if outcome == "vetoed":
                vetoed.append(record)
            elif stage == "year":
                year.append(record)
            elif stage == "llm":
                llm.append(record)
            else:
                applied.append(record)

        return applied, year, llm, vetoed

    async def _worker_loop(self) -> None:
        """Background loop consuming transcript IDs and processing them."""
        while True:
            try:
                transcript_id = await self._queue.get()
                self._pending.discard(transcript_id)
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
                self._pending.discard(transcript_id)
                await self.ingest(transcript_id)
            except asyncio.QueueEmpty:
                break
            except Exception:
                logger.error(
                    "rag.ingestion.drain_error",
                    exc_info=True,
                )
