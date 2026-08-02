"""Deterministic recommendation derivation — no language model involved.

Derives Suggested_Question and Related_Record items from data the retriever
already returned, so a recommendation set costs nothing extra and stays
grounded in the corpus.

`RecommendationBuilder` is pure: no I/O, no model call, deterministic for a
given input. The one piece of data it needs that is not already in hand —
corpus-wide entity and speaker names for the no-chunk case — is supplied by the
caller as a `CorpusHints` value.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4
"""

from __future__ import annotations

import itertools
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import structlog
from langchain_core.documents import Document
from sqlalchemy import text

from app.rag.retriever import RetrievedChunk

logger = structlog.get_logger("rag.recommendations")

TARGET_SUGGESTION_COUNT = 3
MAX_RELATED_RECORDS = 3

# Hint lookup bounds. Only a handful of candidates are ever consumed — the
# builder stops as soon as it has `count` distinct suggestions — so these are
# kept far below the typeahead limits the suggestions endpoint uses.
_HINT_ENTITY_LIMIT = 25
_HINT_SPEAKER_LIMIT = 25


@dataclass(frozen=True)
class CorpusHints:
    """Entity and speaker names available from the search suggestions source.

    Populated by RAG_Router from the same `transcript_chunk` columns that back
    GET /api/search/suggestions. Empty when the corpus is empty or the lookup
    fails — the builder then falls back to its static set.
    """

    entities: list[str] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared dataclasses — used by both the answerer and the builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """A reference linking an answer statement to a source chunk.

    Attributes:
        transcript_id: The transcript containing the cited chunk.
        chunk_id: The unique identifier of the cited chunk.
        speaker: The speaker attributed to the cited chunk (if available).
        start_s: Start timestamp of the cited chunk in seconds.
        end_s: End timestamp of the cited chunk in seconds.
        excerpt: A short excerpt from the cited chunk text.
        sitting_id: The sitting the cited chunk belongs to — the navigation
            target, distinct from `transcript_id`.
        record_id: The hansard record the cited chunk belongs to — the
            navigation target, distinct from `transcript_id`.
    """

    transcript_id: int
    chunk_id: int
    speaker: str | None
    start_s: float | None
    end_s: float | None
    excerpt: str
    sitting_id: int | None = None
    record_id: int | None = None


@dataclass
class RecommendationItem:
    """A suggested follow-up question derived from the transcript context."""

    text: str
    reason: str


@dataclass
class RelatedRecordItem:
    """A sitting, record, or speaker behind the answer, linked to its transcript.

    Derived deterministically from the retrieved chunks by the
    RecommendationBuilder — no language model involved.

    Attributes:
        transcript_id: The transcript the record belongs to.
        label: Display label drawn from the source chunk metadata.
        chunk_id: The chunk the record was derived from.
        speaker: Speaker attributed to the source chunk (if available).
        sitting_title: Sitting title of the source chunk (if available).
        record_title: Record title of the source chunk (if available).
        date: Date of the source chunk (if available).
        start_s: Start timestamp of the source chunk in seconds (if available).
        sitting_id: The sitting the record belongs to — the navigation target.
        record_id: The hansard record itself — the navigation target, and the
            dedup key used by `RecommendationBuilder.related_records`.
    """

    transcript_id: int
    label: str
    chunk_id: int
    speaker: str | None = None
    sitting_title: str | None = None
    record_title: str | None = None
    date: str | None = None
    start_s: float | None = None
    sitting_id: int | None = None
    record_id: int | None = None


@dataclass
class AnswerResponse:
    """Structured response from the grounded answering engine.

    Attributes:
        answer: The generated answer text with inline citation markers.
        citations: List of validated Citation objects referenced in the answer.
        source_chunks: The retrieved chunks used as context for generation.
        recommendations: Suggested follow-up questions based on the context.
        related_records: Sittings, records, or speakers behind the answer.
        latency_ms: Total time from request to response in milliseconds.
    """

    answer: str
    citations: list[Citation] = field(default_factory=list)
    source_chunks: list[RetrievedChunk] = field(default_factory=list)
    recommendations: list[RecommendationItem] = field(default_factory=list)
    related_records: list[RelatedRecordItem] = field(default_factory=list)
    latency_ms: float = 0.0


_STATIC_SUGGESTIONS: tuple[tuple[str, str], ...] = (
    (
        "Which sittings are available in the record?",
        "Start from the sittings that have been transcribed",
    ),
    (
        "Which speakers appear most often in the record?",
        "Speaker coverage is a good entry point into the corpus",
    ),
    (
        "What topics were debated most recently?",
        "Recent proceedings are the most likely to be relevant",
    ),
)


def normalise_question(text: str) -> str:
    """Casefold, collapse whitespace, strip trailing punctuation.

    Used as the dedup key for suggestion distinctness and for excluding
    questions already present in the conversation history.
    """
    return re.sub(r"\s+", " ", text).strip().rstrip("?.!").strip().casefold()


def _clean(value: str | None) -> str | None:
    """Collapse whitespace and return None for absent or blank values."""
    if value is None:
        return None
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed or None


def chunks_to_documents(chunks: Iterable[RetrievedChunk]) -> list[Document]:
    """Convert RetrievedChunk objects into the Document form the builder expects.

    The builder reads its ranking inputs from `Document.metadata`, so a chunk
    coming back from the retriever facade has to be projected before it can be
    mined for suggestions or related records. Metadata keys match the ones the
    retriever arms emit, so a Document built here is interchangeable with one
    the retriever produced directly.
    """
    documents: list[Document] = []
    for chunk in chunks or []:
        documents.append(
            Document(
                page_content=chunk.text,
                metadata={
                    "chunk_id": chunk.chunk_id,
                    "transcript_id": chunk.transcript_id,
                    "speaker": chunk.speaker,
                    "start_s": chunk.start_s,
                    "end_s": chunk.end_s,
                    "entity_names": chunk.matched_entities or [],
                    "score": chunk.relevance_score,
                    "record_title": chunk.record_title,
                    "sitting_title": chunk.sitting_title,
                    "date": chunk.date,
                    "sitting_id": chunk.sitting_id,
                    "record_id": chunk.record_id,
                },
            )
        )
    return documents


def _make_item(text: str, reason: str) -> RecommendationItem:
    """Build a `RecommendationItem` — now locally defined, no deferred import needed."""
    return RecommendationItem(text=text, reason=reason)


def _make_related_record(chunk: Document, label: str) -> RelatedRecordItem:
    """Build a `RelatedRecordItem` from a chunk and its resolved label."""
    meta = chunk.metadata
    return RelatedRecordItem(
        transcript_id=meta["transcript_id"],
        label=label,
        chunk_id=meta["chunk_id"],
        speaker=_clean(meta.get("speaker")),
        sitting_title=_clean(meta.get("sitting_title")),
        record_title=_clean(meta.get("record_title")),
        date=_clean(meta.get("date")),
        start_s=meta.get("start_s"),
        sitting_id=meta.get("sitting_id"),
        record_id=meta.get("record_id"),
    )


def _record_label(chunk: Document) -> str:
    """Resolve the display label for a chunk, first non-empty value winning.

    Precedence: `sitting_title` → `record_title` → `speaker` → `date`. The
    `Transcript {transcript_id}` fallback guarantees a non-empty label even for
    a chunk carrying no metadata at all.
    """
    meta = chunk.metadata
    for value in (
        meta.get("sitting_title"),
        meta.get("record_title"),
        meta.get("speaker"),
        meta.get("date"),
    ):
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return f"Transcript {meta['transcript_id']}"


def _chunk_candidates(chunks: Iterable[Document]) -> Iterator[tuple[str, str]]:
    """Yield (text, reason) pairs naming real values from the given chunks.

    Chunks are consumed in the order given — relevance order from the
    retriever — so suggestions derived from the strongest match come first.
    Templates are keyed by the metadata actually present on the chunk, so the
    emitted text always mentions a value drawn from the record.
    """
    for chunk in chunks:
        meta = chunk.metadata
        speaker = _clean(meta.get("speaker"))
        entities = [e for e in (_clean(v) for v in (meta.get("entity_names") or [])) if e]
        date = _clean(meta.get("date"))
        sitting_title = _clean(meta.get("sitting_title"))

        for entity in entities:
            if speaker:
                yield (
                    f"What did {speaker} say about {entity}?",
                    f"{speaker} is on the record discussing {entity}",
                )
            yield (
                f"What else was said about {entity}?",
                f"{entity} appears in the retrieved proceedings",
            )
            yield (
                f"Which speakers discussed {entity}?",
                f"More than one member may have addressed {entity}",
            )

        if speaker:
            yield (
                f"What other contributions did {speaker} make?",
                f"{speaker} contributed to the retrieved proceedings",
            )

        if date:
            yield (
                f"What else was discussed on {date}?",
                f"Other business was transacted on {date}",
            )

        if sitting_title:
            yield (
                f"What other topics came up in {sitting_title}?",
                f"{sitting_title} covers more ground than this question",
            )


def _hint_candidates(corpus_hints: CorpusHints | None) -> Iterator[tuple[str, str]]:
    """Yield (text, reason) pairs naming real values from the corpus hints."""
    if corpus_hints is None:
        return

    for entity in (_clean(v) for v in corpus_hints.entities):
        if entity:
            yield (
                f"What was said about {entity} in the record?",
                f"{entity} appears in the parliamentary record",
            )

    for speaker in (_clean(v) for v in corpus_hints.speakers):
        if speaker:
            yield (
                f"What contributions did {speaker} make?",
                f"{speaker} appears in the parliamentary record",
            )


def _ordinal(value: int) -> str:
    """Return the English ordinal form of a positive integer."""
    if value % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _filler_candidates() -> Iterator[tuple[str, str]]:
    """Yield an unbounded stream of generic, distinct research prompts.

    The static set holds only three items, so an `exclude` list that already
    covers them would otherwise leave the builder short. This stream is
    infinite and every entry is distinct, which is what makes the "exactly
    `count` items" guarantee total.
    """
    for index in itertools.count(1):
        yield (
            f"Summarise the {_ordinal(index)} most recent sitting in the record",
            "Recent sittings are a reliable entry point into the parliamentary record",
        )


class RecommendationBuilder:
    """Derives Suggested_Question and Related_Record items from retrieval data.

    Pure: no I/O, no model call, deterministic for a given input. All ranking
    inputs (entities, speakers, dates, titles) are already present on the
    Document metadata dicts the retriever returned.
    """

    def suggestions(
        self,
        *,
        chunks: list[Document],
        corpus_hints: CorpusHints | None = None,
        exclude: list[str] | None = None,
        count: int = TARGET_SUGGESTION_COUNT,
    ) -> list[RecommendationItem]:
        """Return `count` distinct suggestions, excluding normalised `exclude` text.

        Source precedence: chunk metadata → corpus hints → static set → generic
        filler. The filler stream is unbounded, so the returned list always has
        exactly `count` items, each with non-empty `text` and `reason`, each
        distinct from the others and from every `exclude` entry under
        `normalise_question`.

        Args:
            chunks: Retrieved chunks as LangChain Document objects to mine for
                entity, speaker, date, and sitting values. May be empty.
            corpus_hints: Corpus-wide entity and speaker names, used when the
                chunks yield too few candidates.
            exclude: Question text that must not be returned — parsed model
                suggestions and prior user questions.
            count: Exact number of items to return. Zero or negative yields an
                empty list.

        Returns:
            Exactly `count` RecommendationItem objects, or fewer only when
            `count` is not positive.
        """
        if count <= 0:
            return []

        seen: set[str] = {normalise_question(value) for value in (exclude or [])}

        candidates = itertools.chain(
            _chunk_candidates(chunks or []),
            _hint_candidates(corpus_hints),
            iter(_STATIC_SUGGESTIONS),
            _filler_candidates(),
        )

        picked: list[RecommendationItem] = []
        for candidate_text, reason in candidates:
            key = normalise_question(candidate_text)
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append(_make_item(candidate_text, reason))
            if len(picked) == count:
                break

        return picked

    def related_records(
        self,
        *,
        chunks: list[Document],
        limit: int = MAX_RELATED_RECORDS,
    ) -> list[RelatedRecordItem]:
        """Return at most `limit` records, at most one per `record_id`.

        A related record is a navigation target pointing at a hansard record, so
        `record_id` is the dedup key — not `transcript_id`. Two transcript
        versions of the same record share a label and a destination, so keying
        on `transcript_id` produced visibly duplicated chips. Chunks whose
        `record_id` is absent fall back to `transcript_id` as the key, keeping
        the old behaviour for un-enriched chunks.

        Chunks are consumed in the order given (relevance order from the
        retriever), so the highest-scoring chunk wins its key.

        The label of each record is the first non-empty value among the source
        chunk's `sitting_title`, `record_title`, `speaker`, and `date`, falling
        back to `Transcript {transcript_id}` so the label is never empty.
        `start_s` is carried through whenever the source chunk has one.

        Args:
            chunks: Retrieved chunks in relevance order. May be empty.
            limit: Maximum number of records to return. Zero or negative yields
                an empty list.

        Returns:
            At most `limit` RelatedRecordItem objects, one per distinct
            `record_id` (or `transcript_id` when `record_id` is None), in the
            order their source chunks were given.
        """
        if limit <= 0:
            return []

        seen: set[tuple[str, int]] = set()
        records: list[RelatedRecordItem] = []

        for chunk in chunks or []:
            record_id = chunk.metadata.get("record_id")
            key = (
                ("record", record_id)
                if record_id is not None
                else ("transcript", chunk.metadata["transcript_id"])
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(_make_related_record(chunk, _record_label(chunk)))
            if len(records) == limit:
                break

        return records


async def fetch_corpus_hints(session_factory) -> CorpusHints:
    """Read distinct entity names and speaker labels from `transcript_chunk`.

    Reads the same two columns that back `GET /api/search/suggestions`, so the
    hints name values the user could also have reached through the filter
    dropdowns.

    This is the only I/O in the module and it is deliberately kept outside
    `RecommendationBuilder`, which stays pure and directly reachable by
    property tests.

    Args:
        session_factory: Async SQLAlchemy session factory.

    Returns:
        Populated `CorpusHints`, or empty hints when the corpus is empty or the
        lookup fails. A failed hint lookup must never fail the answer, so every
        error is logged and swallowed.
    """
    entity_sql = """
        SELECT DISTINCT unnest(entity_names) AS name
        FROM transcript_chunk
        WHERE entity_names IS NOT NULL AND entity_names != '{}'
        ORDER BY name
        LIMIT :limit
    """

    speaker_sql = """
        SELECT DISTINCT speaker
        FROM transcript_chunk
        WHERE speaker IS NOT NULL
        ORDER BY speaker
        LIMIT :limit
    """

    try:
        async with session_factory() as session:
            entity_rows = (
                await session.execute(text(entity_sql), {"limit": _HINT_ENTITY_LIMIT})
            ).fetchall()
            speaker_rows = (
                await session.execute(text(speaker_sql), {"limit": _HINT_SPEAKER_LIMIT})
            ).fetchall()
    except Exception:
        logger.warning("rag.recommendations.hint_lookup_failed", exc_info=True)
        return CorpusHints()

    entities = [value for value in (_clean(row[0]) for row in entity_rows) if value]
    speakers = [value for value in (_clean(row[0]) for row in speaker_rows) if value]

    logger.debug(
        "rag.recommendations.hints_loaded",
        entity_count=len(entities),
        speaker_count=len(speakers),
    )

    return CorpusHints(entities=entities, speakers=speakers)


def finalise_answer(
    *,
    builder: RecommendationBuilder,
    answer: str,
    citations: list[Citation],
    context_chunks: list[RetrievedChunk],
    parsed: list[RecommendationItem],
    hint_chunks: list[RetrievedChunk],
    corpus_hints: CorpusHints | None,
    history_questions: list[str],
    grounded: bool,
    start_time: float,
) -> AnswerResponse:
    """Attach recommendations and related records, then stamp latency.

    The single assembly point for a recommendation set. Every answering path
    routes through here, so no path can return an empty recommendation set
    (Requirements 2.1, 2.2, 2.3).

    `parsed` items keep their order and priority; the builder only tops up to
    TARGET_SUGGESTION_COUNT when the model supplied fewer than that. Items that
    repeat an earlier parsed item are dropped before the top-up so the set stays
    distinct under `normalise_question`. `exclude` carries both the kept parsed
    text and the user questions already in the history, so a top-up never
    restates either.

    No model request is issued here: the builder is deterministic.
    """
    recommendations: list[RecommendationItem] = []
    seen: set[str] = set()

    for item in parsed:
        key = normalise_question(item.text)
        if not key or key in seen:
            continue
        seen.add(key)
        recommendations.append(item)
        if len(recommendations) == TARGET_SUGGESTION_COUNT:
            break

    parsed_kept = len(recommendations)

    if parsed_kept < TARGET_SUGGESTION_COUNT:
        recommendations += builder.suggestions(
            chunks=chunks_to_documents(hint_chunks),
            corpus_hints=corpus_hints,
            exclude=[r.text for r in recommendations] + history_questions,
            count=TARGET_SUGGESTION_COUNT - parsed_kept,
        )

    related = (
        builder.related_records(chunks=chunks_to_documents(context_chunks)) if grounded else []
    )

    latency_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "rag.finalise.recommendations_assembled",
        parsed_count=len(parsed),
        parsed_kept=parsed_kept,
        topped_up=len(recommendations) - parsed_kept,
        related_record_count=len(related),
        latency_ms=round(latency_ms, 1),
    )

    return AnswerResponse(
        answer=answer,
        citations=citations,
        source_chunks=context_chunks,
        recommendations=recommendations,
        related_records=related,
        latency_ms=latency_ms,
    )
