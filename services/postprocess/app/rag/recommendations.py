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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

from app.rag.retrieval import RetrievedChunk

if TYPE_CHECKING:
    from app.rag.answering import RecommendationItem, RelatedRecordItem

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


def _make_item(text: str, reason: str) -> RecommendationItem:
    """Build a `RecommendationItem` from the answering engine's contract.

    The import is deferred to call time on purpose: `app.rag.answering` imports
    this module for the builder, so a module-level import here would create a
    circular import at startup.
    """
    from app.rag.answering import RecommendationItem

    return RecommendationItem(text=text, reason=reason)


def _make_related_record(chunk: RetrievedChunk, label: str) -> RelatedRecordItem:
    """Build a `RelatedRecordItem` from a chunk and its resolved label.

    The import is deferred to call time for the same reason as `_make_item`:
    `app.rag.answering` imports this module, so a module-level import here
    would create a circular import at startup.
    """
    from app.rag.answering import RelatedRecordItem

    return RelatedRecordItem(
        transcript_id=chunk.transcript_id,
        label=label,
        chunk_id=chunk.chunk_id,
        speaker=_clean(chunk.speaker),
        sitting_title=_clean(chunk.sitting_title),
        record_title=_clean(chunk.record_title),
        date=_clean(chunk.date),
        start_s=chunk.start_s,
    )


def _record_label(chunk: RetrievedChunk) -> str:
    """Resolve the display label for a chunk, first non-empty value winning.

    Precedence: `sitting_title` → `record_title` → `speaker` → `date`. The
    `Transcript {transcript_id}` fallback guarantees a non-empty label even for
    a chunk carrying no metadata at all.
    """
    for value in (chunk.sitting_title, chunk.record_title, chunk.speaker, chunk.date):
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return f"Transcript {chunk.transcript_id}"


def _chunk_candidates(chunks: Iterable[RetrievedChunk]) -> Iterator[tuple[str, str]]:
    """Yield (text, reason) pairs naming real values from the given chunks.

    Chunks are consumed in the order given — relevance order from the
    retriever — so suggestions derived from the strongest match come first.
    Templates are keyed by the metadata actually present on the chunk, so the
    emitted text always mentions a value drawn from the record.
    """
    for chunk in chunks:
        speaker = _clean(chunk.speaker)
        entities = [e for e in (_clean(v) for v in (chunk.matched_entities or [])) if e]
        date = _clean(chunk.date)
        sitting_title = _clean(chunk.sitting_title)

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
    RetrievedChunk objects the retriever returned.
    """

    def suggestions(
        self,
        *,
        chunks: list[RetrievedChunk],
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
            chunks: Retrieved chunks to mine for entity, speaker, date, and
                sitting values. May be empty.
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
        chunks: list[RetrievedChunk],
        limit: int = MAX_RELATED_RECORDS,
    ) -> list[RelatedRecordItem]:
        """Return at most `limit` records, at most one per transcript_id.

        Chunks are consumed in the order given (relevance order from the
        retriever), so the highest-scoring chunk wins its transcript_id.

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
            transcript_id, in the order their source chunks were given.
        """
        if limit <= 0:
            return []

        seen: set[int] = set()
        records: list[RelatedRecordItem] = []

        for chunk in chunks or []:
            if chunk.transcript_id in seen:
                continue
            seen.add(chunk.transcript_id)
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
