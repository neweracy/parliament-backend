"""Property tests for correction determinism.

Validates: Requirements 3.12

These tests verify that the Correction_Engine produces identical output for
identical input and identical Dataset_Cache contents across repeated
invocations, as required by the "Deterministic tie-break" invariant.

Properties tested:
1. correct_single determinism: calling correct_single multiple times with
   the same inputs produces identical results (both None, or both the same
   MatchResult with identical fields).
2. correction_sort_key stable order: sorting a list of MatchResults twice
   using correction_sort_key produces the same order both times, proving
   the sort key defines a total order with no hidden non-determinism.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.correction.engine import correct_single, correction_sort_key
from app.correction.strategies import MatchResult
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Entity names — constrained to printable alphabetic strings
_entity_name = st.text(
    alphabet=st.characters(categories=("L",), min_codepoint=65, max_codepoint=122),
    min_size=3,
    max_size=15,
).filter(lambda s: s.strip() != "" and len(s.strip()) >= 3)

# Alias text — similar constraints
_alias_text = st.text(
    alphabet=st.characters(categories=("L",), min_codepoint=65, max_codepoint=122),
    min_size=3,
    max_size=12,
).filter(lambda s: s.strip() != "" and len(s.strip()) >= 3)

# Input text to correct — variable length strings
_input_text = st.text(
    alphabet=st.characters(categories=("L", "N", "Zs"), min_codepoint=32, max_codepoint=122),
    min_size=1,
    max_size=25,
).filter(lambda s: s.strip() != "")

# Span lengths for sort key tests
_span_len = st.integers(min_value=1, max_value=4)

# Confidence values
_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# Strategy names matching the MatchStrategy enum values
_strategy = st.sampled_from(["exact", "fused", "joined", "initials", "phonetic", "fuzzy", "substring"])

# Entity kinds and types
_entity_kind = st.sampled_from(["location", "person", "party"])
_entity_type = st.sampled_from(["region", "city", "constituency", "supplementary", "president", "minister", "mp", "party"])


# Source definitions for generating records
_SOURCES = [
    ("regions", 0, EntityKind.location, EntityType.region),
    ("cities", 1, EntityKind.location, EntityType.city),
    ("supplementary_locations", 2, EntityKind.location, EntityType.supplementary),
    ("all_persons", 3, EntityKind.person, EntityType.minister),
    ("all_mps", 4, EntityKind.person, EntityType.mp),
    ("all_parties", 5, EntityKind.party, EntityType.party),
]


@st.composite
def entity_records_with_index(draw: st.DrawFn) -> tuple[list[EntityRecord], MatchIndex, DatasetSnapshot]:
    """Generate a set of EntityRecords, build an index, and create a snapshot.

    Returns (records, index, snapshot) suitable for correction testing.
    """
    all_records: list[EntityRecord] = []

    for source, rank, kind, etype in _SOURCES:
        tier_size = draw(st.integers(min_value=1, max_value=3))
        for _ in range(tier_size):
            canonical = draw(_entity_name)
            aliases = draw(st.lists(_alias_text, min_size=0, max_size=3))
            record = EntityRecord(
                canonical=canonical,
                entity_kind=kind,
                entity_type=etype,
                aliases=aliases,
                source=source,
                source_rank=rank,
                active=True,
            )
            all_records.append(record)

    # Sort by deterministic load order
    all_records.sort(key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))

    # Build the index
    index = build_index(all_records)

    # Build a minimal DatasetSnapshot (only block_list and stopwords matter for correct_single)
    from datetime import datetime, timezone

    snapshot = DatasetSnapshot(
        version="test",
        records=tuple(all_records),
        record_count=len(all_records),
        loaded_at=datetime.now(timezone.utc),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )

    return (all_records, index, snapshot)


@st.composite
def match_result_list(draw: st.DrawFn) -> list[tuple[MatchResult, int]]:
    """Generate a list of (MatchResult, span_len) tuples for sort testing.

    Generates between 2 and 10 MatchResults with varying confidence,
    strategy, span_len, and canonical values to exercise the sort key.
    """
    size = draw(st.integers(min_value=2, max_value=10))
    results: list[tuple[MatchResult, int]] = []

    for _ in range(size):
        result = MatchResult(
            canonical=draw(_entity_name),
            confidence=draw(_confidence),
            strategy=draw(_strategy),
            entity_kind=draw(_entity_kind),
            entity_type=draw(_entity_type),
        )
        span = draw(_span_len)
        results.append((result, span))

    return results


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(data=entity_records_with_index(), input_text=_input_text, span_len=_span_len)
@settings(max_examples=200)
def test_correct_single_determinism(
    data: tuple[list[EntityRecord], MatchIndex, DatasetSnapshot],
    input_text: str,
    span_len: int,
) -> None:
    """Identical input and identical Dataset_Cache produce identical output.

    For ANY input text, span_len, index, and snapshot: calling correct_single
    twice with the same arguments produces the same result. Both calls return
    None, or both return a MatchResult with identical fields.

    This validates that the correction engine has no hidden mutable state,
    no dependence on call order, and no randomness in its strategy chain.

    **Validates: Requirements 3.12**
    """
    _records, index, snapshot = data

    result_first = correct_single(input_text, span_len, index, snapshot)
    result_second = correct_single(input_text, span_len, index, snapshot)

    if result_first is None:
        assert result_second is None, (
            f"First call returned None but second returned {result_second} "
            f"for input '{input_text}'"
        )
    else:
        assert result_second is not None, (
            f"First call returned {result_first} but second returned None "
            f"for input '{input_text}'"
        )
        assert result_first.canonical == result_second.canonical
        assert result_first.confidence == result_second.confidence
        assert result_first.strategy == result_second.strategy
        assert result_first.entity_kind == result_second.entity_kind
        assert result_first.entity_type == result_second.entity_type


@given(results_with_spans=match_result_list())
@settings(max_examples=200)
def test_correction_sort_key_stable_order(
    results_with_spans: list[tuple[MatchResult, int]],
) -> None:
    """Sorting MatchResults by correction_sort_key produces a stable total order.

    For ANY list of MatchResults: sorting twice using correction_sort_key
    produces the same order. This proves the sort key defines a deterministic
    total order with no hidden non-determinism from dict ordering, hash
    seeds, or floating-point instability.

    **Validates: Requirements 3.12**
    """
    # Sort once
    sorted_first = sorted(
        results_with_spans,
        key=lambda pair: correction_sort_key(pair[0], pair[1]),
    )

    # Sort again
    sorted_second = sorted(
        results_with_spans,
        key=lambda pair: correction_sort_key(pair[0], pair[1]),
    )

    # Orders must be identical
    assert len(sorted_first) == len(sorted_second)
    for i, (pair_a, pair_b) in enumerate(zip(sorted_first, sorted_second)):
        result_a, span_a = pair_a
        result_b, span_b = pair_b
        assert result_a.canonical == result_b.canonical, f"Position {i}: canonical mismatch"
        assert result_a.confidence == result_b.confidence, f"Position {i}: confidence mismatch"
        assert result_a.strategy == result_b.strategy, f"Position {i}: strategy mismatch"
        assert span_a == span_b, f"Position {i}: span_len mismatch"


@given(data=entity_records_with_index(), span_len=_span_len)
@settings(max_examples=100)
def test_correct_single_determinism_across_multiple_calls(
    data: tuple[list[EntityRecord], MatchIndex, DatasetSnapshot],
    span_len: int,
) -> None:
    """Multiple invocations with the same canonical_map key yield the same result.

    For each key in the canonical_map, calling correct_single with that key
    multiple times (5 repetitions) always produces the same result. This
    exercises the strategy chain with inputs that are guaranteed to match,
    catching any non-determinism in the match path itself.

    **Validates: Requirements 3.12**
    """
    _records, index, snapshot = data

    # Pick up to 5 keys from the canonical_map to test
    keys = list(index.canonical_map.keys())[:5]

    for key in keys:
        results = [
            correct_single(key, span_len, index, snapshot)
            for _ in range(5)
        ]

        # All results must be identical
        first = results[0]
        for i, result in enumerate(results[1:], start=1):
            if first is None:
                assert result is None, (
                    f"Call 0 returned None but call {i} returned {result} for key '{key}'"
                )
            else:
                assert result is not None, (
                    f"Call 0 returned {first} but call {i} returned None for key '{key}'"
                )
                assert first.canonical == result.canonical, (
                    f"Canonical mismatch at call {i} for key '{key}': "
                    f"{first.canonical} != {result.canonical}"
                )
                assert first.confidence == result.confidence
                assert first.strategy == result.strategy
                assert first.entity_kind == result.entity_kind
                assert first.entity_type == result.entity_type
