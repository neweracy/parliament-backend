"""Unit tests for app/rag/parsing.py — citation validation carries navigation ids.

`validate_citations` is the only place a `Citation` is built, so it is the sole
gate through which `sitting_id` and `record_id` reach the API response. A
citation that arrives without them leaves the frontend holding only
`transcript_id`, which addresses a different identifier space and resolves to
the wrong record — or to none at all.
"""

from __future__ import annotations

from app.rag.parsing import validate_citations
from app.rag.retriever import RetrievedChunk


def _chunk(
    *,
    chunk_id: int,
    transcript_id: int,
    sitting_id: int | None,
    record_id: int | None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="The education budget rose by twelve percent.",
        relevance_score=0.9,
        transcript_id=transcript_id,
        speaker="Hon. Ama Mensah",
        start_s=754.0,
        end_s=812.0,
        sitting_id=sitting_id,
        record_id=record_id,
    )


def test_citations_carry_sitting_id_and_record_id():
    """Ids come straight off the matching chunk, independent of transcript_id."""
    # transcript 2 sits inside record 1 of sitting 1 — all three differ, which
    # is what broke the old "transcript_id for everything" navigation.
    chunk = _chunk(chunk_id=63, transcript_id=2, sitting_id=1, record_id=1)

    citations = validate_citations([63], {63: chunk})

    assert len(citations) == 1
    assert citations[0].transcript_id == 2
    assert citations[0].sitting_id == 1
    assert citations[0].record_id == 1


def test_citations_preserve_absent_ids_as_none():
    """An un-enriched chunk yields None ids rather than a substituted value."""
    chunk = _chunk(chunk_id=7, transcript_id=5, sitting_id=None, record_id=None)

    citations = validate_citations([7], {7: chunk})

    assert citations[0].sitting_id is None
    assert citations[0].record_id is None


def test_unknown_chunk_id_is_dropped():
    """A marker with no matching chunk produces no citation."""
    chunk = _chunk(chunk_id=1, transcript_id=1, sitting_id=1, record_id=1)

    citations = validate_citations([1, 404], {1: chunk})

    assert [c.chunk_id for c in citations] == [1]


def test_ids_are_taken_per_chunk_not_shared():
    """Each citation gets the ids of its own chunk."""
    chunks = {
        1: _chunk(chunk_id=1, transcript_id=1, sitting_id=1, record_id=1),
        2: _chunk(chunk_id=2, transcript_id=9, sitting_id=4, record_id=7),
    }

    citations = validate_citations([1, 2], chunks)

    assert [(c.sitting_id, c.record_id) for c in citations] == [(1, 1), (4, 7)]
