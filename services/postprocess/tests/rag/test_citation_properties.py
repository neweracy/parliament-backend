"""Property tests for citation chunk reference validity.

Validates: Requirements 9.3

These tests verify that the GroundedAnswerer:
1. Produces Citation objects whose chunk_id always exists in the source chunk map.
2. Ensures citation metadata (transcript_id, speaker, start_s, end_s) matches
   the referenced chunk's fields.

Properties tested:
- Property 17: Citation chunk reference validity
"""

from __future__ import annotations

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from app.rag.answering import GroundedAnswerer, Citation
from app.rag.retrieval import RetrievedChunk


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_chunk_id_st = st.integers(min_value=1, max_value=100_000)


@st.composite
def retrieved_chunk_strategy(draw: st.DrawFn, chunk_id: int | None = None) -> RetrievedChunk:
    """Generate a RetrievedChunk with realistic metadata."""
    cid = chunk_id if chunk_id is not None else draw(_chunk_id_st)
    text = draw(
        st.text(
            min_size=10,
            max_size=200,
            alphabet=st.characters(whitelist_categories=("L", "Zs", "Nd")),
        )
    )
    transcript_id = draw(st.integers(min_value=1, max_value=10_000))
    speaker = draw(st.one_of(st.none(), st.sampled_from([
        "Hon. Doe", "Speaker A", "Minister X", "Hon. Agyeman", "Madam Chair",
    ])))
    start_s = draw(st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False))
    end_s = start_s + draw(st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False))
    relevance_score = draw(st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False))

    return RetrievedChunk(
        chunk_id=cid,
        text=text,
        relevance_score=relevance_score,
        transcript_id=transcript_id,
        speaker=speaker,
        start_s=round(start_s, 3),
        end_s=round(end_s, 3),
        matched_entities=[],
    )


@st.composite
def source_chunks_strategy(draw: st.DrawFn) -> list[RetrievedChunk]:
    """Generate a list of RetrievedChunks with unique chunk_ids."""
    n = draw(st.integers(min_value=1, max_value=15))
    chunk_ids = draw(
        st.lists(
            st.integers(min_value=1, max_value=100_000),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    chunks = []
    for cid in chunk_ids:
        chunk = draw(retrieved_chunk_strategy(chunk_id=cid))
        chunks.append(chunk)
    return chunks


@st.composite
def answer_text_with_citations(
    draw: st.DrawFn,
    valid_ids: list[int],
    include_invalid: bool = True,
) -> str:
    """Generate a fake Claude answer text with citation markers [chunk_id].

    References some valid chunk IDs from the source chunks and optionally
    includes some invalid IDs to test filtering behavior.
    """
    # Pick a subset of valid IDs to cite
    n_valid_citations = draw(st.integers(min_value=0, max_value=len(valid_ids)))
    cited_valid = draw(
        st.lists(
            st.sampled_from(valid_ids) if valid_ids else st.nothing(),
            min_size=n_valid_citations,
            max_size=n_valid_citations,
        )
    ) if valid_ids else []

    # Optionally add invalid IDs
    invalid_ids: list[int] = []
    if include_invalid:
        n_invalid = draw(st.integers(min_value=0, max_value=5))
        invalid_ids = draw(
            st.lists(
                st.integers(min_value=100_001, max_value=999_999),
                min_size=n_invalid,
                max_size=n_invalid,
            )
        )

    # Combine all cited IDs
    all_cited = cited_valid + invalid_ids

    # Build answer text with citation markers interspersed
    parts: list[str] = []
    sentences = [
        "The budget was discussed at length.",
        "Education spending increased significantly.",
        "The Honourable Member raised concerns.",
        "This aligns with previous parliamentary debates.",
        "Committee findings support this conclusion.",
    ]

    for i, cid in enumerate(all_cited):
        sentence = sentences[i % len(sentences)]
        parts.append(f"{sentence} [{cid}]")

    # Add some text without citations too
    if not parts:
        parts.append("The parliamentary record shows relevant discussion.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Property 17: Citation chunk reference validity
# ---------------------------------------------------------------------------


@given(source_chunks=source_chunks_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_every_validated_citation_references_existing_chunk(
    source_chunks: list[RetrievedChunk],
) -> None:
    """Every validated Citation has a chunk_id that exists in the source chunk map.

    For ANY answer text with citation markers and ANY set of source chunks,
    calling _parse_citations() followed by _validate_citations() SHALL produce
    Citation objects where every chunk_id exists in the source chunk map.

    **Validates: Requirements 9.3**
    """
    assume(len(source_chunks) > 0)

    # Build the chunk map as the answerer would
    chunk_map = {c.chunk_id: c for c in source_chunks}
    valid_ids = list(chunk_map.keys())

    # Build an answer text that references some valid and some invalid chunk IDs
    # Mix of valid references and invalid (out-of-range) ones
    invalid_ids = [200_001, 300_002, 400_003]
    all_refs = valid_ids + invalid_ids

    # Construct answer text
    answer_parts = []
    for cid in all_refs:
        answer_parts.append(f"Statement about parliamentary matters [{cid}].")
    answer_text = " ".join(answer_parts)

    # Parse citations from the answer text
    cited_chunk_ids = GroundedAnswerer._parse_citations(answer_text)

    # Validate citations against the chunk map
    citations = GroundedAnswerer._validate_citations(cited_chunk_ids, chunk_map)

    # PROPERTY: Every citation's chunk_id exists in the source chunk map
    for citation in citations:
        assert citation.chunk_id in chunk_map, (
            f"Citation chunk_id={citation.chunk_id} not found in source chunk map. "
            f"Available IDs: {list(chunk_map.keys())}"
        )


@given(source_chunks=source_chunks_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_citation_metadata_matches_referenced_chunk(
    source_chunks: list[RetrievedChunk],
) -> None:
    """Citation metadata fields match the referenced chunk's fields.

    For ANY set of source chunks and any answer text citing those chunks,
    every validated Citation SHALL have transcript_id, speaker, start_s,
    and end_s matching the referenced chunk's corresponding fields.

    **Validates: Requirements 9.3**
    """
    assume(len(source_chunks) > 0)

    chunk_map = {c.chunk_id: c for c in source_chunks}
    valid_ids = list(chunk_map.keys())

    # Build answer text referencing all valid chunk IDs
    answer_text = " ".join(f"Point [{cid}]." for cid in valid_ids)

    # Parse and validate
    cited_chunk_ids = GroundedAnswerer._parse_citations(answer_text)
    citations = GroundedAnswerer._validate_citations(cited_chunk_ids, chunk_map)

    # PROPERTY: Every citation's metadata matches the source chunk
    for citation in citations:
        chunk = chunk_map[citation.chunk_id]
        assert citation.transcript_id == chunk.transcript_id, (
            f"Citation chunk_id={citation.chunk_id}: "
            f"transcript_id mismatch: {citation.transcript_id} != {chunk.transcript_id}"
        )
        assert citation.speaker == chunk.speaker, (
            f"Citation chunk_id={citation.chunk_id}: "
            f"speaker mismatch: {citation.speaker!r} != {chunk.speaker!r}"
        )
        assert citation.start_s == chunk.start_s, (
            f"Citation chunk_id={citation.chunk_id}: "
            f"start_s mismatch: {citation.start_s} != {chunk.start_s}"
        )
        assert citation.end_s == chunk.end_s, (
            f"Citation chunk_id={citation.chunk_id}: "
            f"end_s mismatch: {citation.end_s} != {chunk.end_s}"
        )


@given(source_chunks=source_chunks_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_invalid_citations_are_excluded(
    source_chunks: list[RetrievedChunk],
) -> None:
    """Invalid citation references are excluded from the output.

    For ANY set of source chunks and an answer text containing ONLY
    invalid chunk IDs (not in the chunk map), _validate_citations()
    SHALL return an empty list.

    **Validates: Requirements 9.3**
    """
    assume(len(source_chunks) > 0)

    chunk_map = {c.chunk_id: c for c in source_chunks}
    valid_ids = set(chunk_map.keys())

    # Generate IDs guaranteed to be invalid (not in chunk_map)
    invalid_ids = [i for i in range(500_001, 500_011) if i not in valid_ids]
    assume(len(invalid_ids) > 0)

    # Build answer text with only invalid IDs
    answer_text = " ".join(f"Claim [{cid}]." for cid in invalid_ids)

    cited_chunk_ids = GroundedAnswerer._parse_citations(answer_text)
    citations = GroundedAnswerer._validate_citations(cited_chunk_ids, chunk_map)

    # PROPERTY: No citations should be produced for invalid references
    assert len(citations) == 0, (
        f"Expected no citations for invalid IDs {invalid_ids}, "
        f"but got {[c.chunk_id for c in citations]}"
    )


@given(source_chunks=source_chunks_strategy())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_citation_count_bounded_by_valid_references(
    source_chunks: list[RetrievedChunk],
) -> None:
    """Citation count is bounded by the number of valid references in the answer.

    For ANY answer text, the number of validated citations SHALL be at most
    the number of unique valid chunk IDs cited in the answer text.

    **Validates: Requirements 9.3**
    """
    assume(len(source_chunks) > 0)

    chunk_map = {c.chunk_id: c for c in source_chunks}
    valid_ids = list(chunk_map.keys())

    # Mix valid and invalid IDs in the answer
    invalid_ids = [700_001, 700_002, 700_003]
    all_refs = valid_ids + invalid_ids

    answer_text = " ".join(f"Evidence [{cid}]." for cid in all_refs)

    cited_chunk_ids = GroundedAnswerer._parse_citations(answer_text)
    citations = GroundedAnswerer._validate_citations(cited_chunk_ids, chunk_map)

    # PROPERTY: citation count <= number of unique valid IDs cited
    valid_cited = set(cited_chunk_ids) & set(valid_ids)
    assert len(citations) <= len(valid_cited), (
        f"Got {len(citations)} citations but only {len(valid_cited)} valid IDs were cited"
    )
    # Also: citation count == number of unique valid IDs cited (exact match)
    assert len(citations) == len(valid_cited), (
        f"Expected exactly {len(valid_cited)} citations for valid IDs, got {len(citations)}"
    )
