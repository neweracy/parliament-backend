"""Property tests for RRF fusion and entity pre-filter clause builder.

Validates: Requirements 8.2, 8.3

These tests verify that the HybridRetriever:
1. Correctly computes Reciprocal Rank Fusion scores as sum(1/(k+rank))
   across both ranked lists, and returns results sorted by descending score.
2. Builds entity pre-filter SQL clauses that enforce array overlap when
   entity_names filters are provided.

Properties tested:
- Property 13: RRF fusion correctness
- Property 14: Entity pre-filter completeness
"""

from __future__ import annotations

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from app.rag.retrieval import HybridRetriever, RetrievalFilters, _RRF_K


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Unique chunk IDs
_chunk_id_st = st.integers(min_value=1, max_value=100_000)


@st.composite
def result_dict(draw: st.DrawFn, chunk_id: int | None = None) -> dict:
    """Generate a single result dict as produced by _vector_search or _fulltext_search.

    Matches the structure returned by both search arms:
    chunk_id, text, transcript_id, speaker, start_s, end_s, entity_names, score.
    """
    cid = chunk_id if chunk_id is not None else draw(_chunk_id_st)
    text = draw(st.text(min_size=10, max_size=100, alphabet=st.characters(whitelist_categories=("L", "Zs"))))
    transcript_id = draw(st.integers(min_value=1, max_value=10_000))
    speaker = draw(st.one_of(st.none(), st.sampled_from(["Hon. Doe", "Speaker A", "Minister X"])))
    start_s = draw(st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False))
    end_s = start_s + draw(st.floats(min_value=0.1, max_value=60.0, allow_nan=False, allow_infinity=False))
    entity_names = draw(st.lists(
        st.sampled_from(["Ghana", "Kumasi", "Accra", "Parliament", "Budget"]),
        min_size=0,
        max_size=3,
    ))
    score = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))

    return {
        "chunk_id": cid,
        "text": text,
        "transcript_id": transcript_id,
        "speaker": speaker,
        "start_s": round(start_s, 3),
        "end_s": round(end_s, 3),
        "entity_names": entity_names,
        "score": score,
    }


@st.composite
def ranked_list_pair(draw: st.DrawFn) -> tuple[list[dict], list[dict]]:
    """Generate two ranked lists with some overlapping chunk_ids.

    Produces vector_results and fulltext_results that may share some chunk_ids
    (simulating a document appearing in both search results).
    """
    # Generate a pool of unique chunk IDs
    n_total = draw(st.integers(min_value=2, max_value=30))
    all_ids = draw(
        st.lists(
            st.integers(min_value=1, max_value=100_000),
            min_size=n_total,
            max_size=n_total,
            unique=True,
        )
    )

    # Decide how many IDs go to vector-only, fulltext-only, and both
    n_vector_only = draw(st.integers(min_value=0, max_value=min(n_total, 15)))
    remaining = n_total - n_vector_only
    n_shared = draw(st.integers(min_value=0, max_value=min(remaining, 10)))
    n_fulltext_only = remaining - n_shared

    vector_ids = all_ids[:n_vector_only] + all_ids[n_vector_only:n_vector_only + n_shared]
    fulltext_ids = all_ids[n_vector_only:n_vector_only + n_shared] + all_ids[n_vector_only + n_shared:]

    # Generate result dicts for each list
    vector_results = [draw(result_dict(chunk_id=cid)) for cid in vector_ids]
    fulltext_results = [draw(result_dict(chunk_id=cid)) for cid in fulltext_ids]

    return vector_results, fulltext_results


@st.composite
def entity_filter_strategy(draw: st.DrawFn) -> list[str]:
    """Generate a non-empty list of entity names for filtering."""
    return draw(st.lists(
        st.sampled_from(["Ghana", "Kumasi", "Accra", "Parliament", "Budget", "Education"]),
        min_size=1,
        max_size=4,
        unique=True,
    ))


# ---------------------------------------------------------------------------
# Property 13: RRF fusion correctness
# ---------------------------------------------------------------------------


@given(data=ranked_list_pair())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_rrf_fusion_score_correctness(data: tuple[list[dict], list[dict]]) -> None:
    """Fused score equals sum(1/(k+rank)) for each list where the chunk appears.

    For ANY two ranked result lists (vector and fulltext), calling
    _reciprocal_rank_fusion() SHALL produce results where each chunk's
    relevance_score equals the sum of 1/(60+rank) for each list the
    chunk appears in (rank is 1-based).

    **Validates: Requirements 8.2**
    """
    vector_results, fulltext_results = data
    assume(len(vector_results) + len(fulltext_results) > 0)

    fused = HybridRetriever._reciprocal_rank_fusion(vector_results, fulltext_results)

    # Build expected scores manually
    expected_scores: dict[int, float] = {}

    for rank, item in enumerate(vector_results, start=1):
        cid = item["chunk_id"]
        expected_scores[cid] = expected_scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)

    for rank, item in enumerate(fulltext_results, start=1):
        cid = item["chunk_id"]
        expected_scores[cid] = expected_scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)

    # Verify each fused chunk has the correct score
    for chunk in fused:
        expected = expected_scores[chunk.chunk_id]
        assert abs(chunk.relevance_score - expected) < 1e-10, (
            f"Chunk {chunk.chunk_id}: expected score {expected}, "
            f"got {chunk.relevance_score}"
        )

    # Verify all expected chunks are present
    fused_ids = {c.chunk_id for c in fused}
    assert fused_ids == set(expected_scores.keys()), (
        f"Mismatch in chunk IDs. Expected {set(expected_scores.keys())}, "
        f"got {fused_ids}"
    )


@given(data=ranked_list_pair())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_rrf_fusion_sorted_descending(data: tuple[list[dict], list[dict]]) -> None:
    """Fused results are sorted by descending fused score.

    For ANY two ranked result lists, the output of _reciprocal_rank_fusion()
    SHALL be sorted in non-increasing order of relevance_score.

    **Validates: Requirements 8.2**
    """
    vector_results, fulltext_results = data
    assume(len(vector_results) + len(fulltext_results) > 0)

    fused = HybridRetriever._reciprocal_rank_fusion(vector_results, fulltext_results)

    scores = [chunk.relevance_score for chunk in fused]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], (
            f"Results not sorted descending at index {i}: "
            f"score[{i}]={scores[i]} < score[{i+1}]={scores[i+1]}"
        )


# ---------------------------------------------------------------------------
# Property 14: Entity pre-filter completeness
# ---------------------------------------------------------------------------


@given(entity_names=entity_filter_strategy())
@settings(max_examples=200)
def test_entity_filter_clause_produces_overlap_condition(entity_names: list[str]) -> None:
    """Entity pre-filter builds a SQL clause enforcing array overlap.

    For ANY non-empty list of entity_names in a RetrievalFilters, calling
    _build_filter_clauses() SHALL produce a SQL clause containing the
    PostgreSQL array overlap operator (&&) on tc.entity_names, ensuring
    that only chunks with at least one matching entity are returned.

    **Validates: Requirements 8.3**
    """
    filters = RetrievalFilters(entity_names=entity_names)
    clauses, params = HybridRetriever._build_filter_clauses(filters)

    # There should be at least one clause
    assert len(clauses) >= 1, "Expected at least one filter clause for entity_names"

    # Find the entity filter clause
    entity_clause = None
    for clause in clauses:
        if "entity_names" in clause and "&&" in clause:
            entity_clause = clause
            break

    assert entity_clause is not None, (
        f"Expected a clause with array overlap (&&) on entity_names. "
        f"Got clauses: {clauses}"
    )

    # Verify the param holds the entity filter values
    assert "entity_filter" in params, (
        f"Expected 'entity_filter' param. Got params: {params}"
    )
    assert params["entity_filter"] == entity_names, (
        f"Expected entity_filter param to equal {entity_names}, "
        f"got {params['entity_filter']}"
    )


@given(
    entity_names=entity_filter_strategy(),
    vector_results=st.lists(result_dict(), min_size=1, max_size=10),
    fulltext_results=st.lists(result_dict(), min_size=1, max_size=10),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_entity_filter_rrf_preserves_entity_names(
    entity_names: list[str],
    vector_results: list[dict],
    fulltext_results: list[dict],
) -> None:
    """RRF fusion preserves entity_names from input results.

    For ANY two ranked lists where results have entity_names arrays,
    the fused output SHALL preserve entity_names in the matched_entities
    field of each RetrievedChunk, enabling downstream filtering to verify
    that every result has at least one matching entity.

    **Validates: Requirements 8.3**
    """
    # Ensure all input results have entity_names containing at least one
    # of the filter values (simulating pre-filtered results from DB)
    for item in vector_results + fulltext_results:
        # Add at least one matching entity to each result
        item["entity_names"] = list(set(item.get("entity_names", []) + [entity_names[0]]))

    fused = HybridRetriever._reciprocal_rank_fusion(vector_results, fulltext_results)

    # Every fused result should have at least one entity from entity_names
    entity_set = set(entity_names)
    for chunk in fused:
        matching = entity_set.intersection(chunk.matched_entities)
        assert len(matching) >= 1, (
            f"Chunk {chunk.chunk_id} has matched_entities={chunk.matched_entities} "
            f"but none match entity_names filter {entity_names}"
        )
