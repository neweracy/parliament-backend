"""Deterministic recommendation derivation — no language model involved.

Derives Suggested_Question items from data the retriever already returned, so a
recommendation set costs nothing extra and stays grounded in the corpus.

`RecommendationBuilder` is pure: no I/O, no model call, deterministic for a
given input. The one piece of data it needs that is not already in hand —
corpus-wide entity and speaker names for the no-chunk case — is supplied by the
caller as a `CorpusHints` value.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.rag.retrieval import RetrievedChunk

if TYPE_CHECKING:
    from app.rag.answering import RecommendationItem

TARGET_SUGGESTION_COUNT = 3
MAX_RELATED_RECORDS = 3


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
    """Derives Suggested_Question items from retrieval data.

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

        seen: set[str] = {normalise_question(text) for text in (exclude or [])}

        candidates = itertools.chain(
            _chunk_candidates(chunks or []),
            _hint_candidates(corpus_hints),
            iter(_STATIC_SUGGESTIONS),
            _filler_candidates(),
        )

        picked: list[RecommendationItem] = []
        for text, reason in candidates:
            key = normalise_question(text)
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append(_make_item(text, reason))
            if len(picked) == count:
                break

        return picked
