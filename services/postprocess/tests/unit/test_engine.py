"""Unit tests for app/correction/engine.py.

Tests correct_single dispatch (short-circuiting strategy chain, short-input
handling, stopword guard), correction_sort_key deterministic tie-break, and
apply_party_display heuristic.

Requirements: 3.9, 3.10, 3.11, 3.12, 4.7
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.correction.engine import apply_party_display, correct_single, correction_sort_key
from app.correction.strategies import MatchResult
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_test_index_and_snapshot() -> tuple[MatchIndex, DatasetSnapshot]:
    """Build a minimal index and snapshot with a few known entities."""
    records = [
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["koumasi", "kumase"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Ningo-Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=["ningoprampram", "ningo prampram"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Ken Ofori-Atta",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["ken ofori atta"],
            source="persons",
            source_rank=1,
        ),
        EntityRecord(
            canonical="National Democratic Congress",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["ndc"],
            party="NDC",
            source="parties",
            source_rank=2,
        ),
        EntityRecord(
            canonical="Accra",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["akra"],
            source="supplementary",
            source_rank=0,
        ),
        # Short canonical for testing short-input paths
        EntityRecord(
            canonical="Wa",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
            source="supplementary",
            source_rank=0,
        ),
    ]

    index = build_index(records)

    snapshot = DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(timezone.utc),
        index=index,
        block_list=frozenset(["general", "page", "national"]),
        stopwords=frozenset(["the", "a", "an", "is", "of"]),
        word_stopwords=frozenset(["of", "for", "and", "the"]),
        title_prefixes=frozenset(["honorable", "honourable", "minister"]),
    )

    return index, snapshot


@pytest.fixture()
def test_env() -> tuple[MatchIndex, DatasetSnapshot]:
    """Return a (MatchIndex, DatasetSnapshot) pair."""
    return _build_test_index_and_snapshot()


# ---------------------------------------------------------------------------
# correct_single tests
# ---------------------------------------------------------------------------


class TestCorrectSingle:
    """Tests for correct_single — strategy chain dispatch."""

    def test_exact_match(self, test_env):
        index, snapshot = test_env
        result = correct_single("Kumasi", 1, index, snapshot)
        assert result is not None
        assert result.canonical == "Kumasi"
        assert result.strategy == "exact"
        assert result.confidence == 1.00

    def test_exact_match_case_insensitive(self, test_env):
        index, snapshot = test_env
        result = correct_single("KUMASI", 1, index, snapshot)
        assert result is not None
        assert result.canonical == "Kumasi"
        assert result.strategy == "exact"

    def test_fused_match(self, test_env):
        index, snapshot = test_env
        # "ningo prampram" lowered and fused → "ningoprampram" → Ningo-Prampram
        result = correct_single("ningoprampram", 1, index, snapshot)
        assert result is not None
        assert result.canonical == "Ningo-Prampram"
        # Could be exact (via alias) or fused depending on indexing
        assert result.strategy in ("exact", "fused")

    def test_stopword_returns_none(self, test_env):
        index, snapshot = test_env
        result = correct_single("the", 1, index, snapshot)
        assert result is None

    def test_stopword_case_insensitive(self, test_env):
        index, snapshot = test_env
        result = correct_single("THE", 1, index, snapshot)
        assert result is None

    def test_short_input_only_tries_exact_and_fused(self, test_env):
        index, snapshot = test_env
        # "Wa" is 2 chars — should only try exact and fused
        result = correct_single("Wa", 1, index, snapshot)
        # "wa" is in canonical_map (from "Wa" canonical)
        assert result is not None
        assert result.canonical == "Wa"
        assert result.strategy == "exact"

    def test_short_input_no_match(self, test_env):
        index, snapshot = test_env
        # "xyz" is 3 chars, not in any map
        result = correct_single("xyz", 1, index, snapshot)
        assert result is None

    def test_no_match_returns_none(self, test_env):
        index, snapshot = test_env
        result = correct_single("xyzabc123", 1, index, snapshot)
        assert result is None

    def test_fuzzy_match(self, test_env):
        index, snapshot = test_env
        # "kumase" is an alias (exact via last-wins), but "kumase" as a misspelling
        # of "Kumasi" — test a slight misspelling that's fuzzy
        result = correct_single("Kumase", 1, index, snapshot)
        assert result is not None
        assert result.canonical == "Kumasi"

    def test_blocked_word_not_matched_fuzzy(self, test_env):
        """Words in the block list should not get fuzzy-matched."""
        index, snapshot = test_env
        # "general" is blocked — should not match anything
        result = correct_single("general", 1, index, snapshot)
        assert result is None

    def test_party_match(self, test_env):
        index, snapshot = test_env
        # "ndc" is an alias for National Democratic Congress
        result = correct_single("ndc", 1, index, snapshot)
        # "ndc" is 3 chars, so only exact and fused are tried
        assert result is not None
        assert result.canonical == "National Democratic Congress"
        assert result.entity_kind == "party"

    def test_returns_match_result_type(self, test_env):
        index, snapshot = test_env
        result = correct_single("Accra", 1, index, snapshot)
        assert result is not None
        assert isinstance(result, MatchResult)
        assert result.entity_kind == "location"
        assert result.entity_type == "city"


# ---------------------------------------------------------------------------
# correction_sort_key tests
# ---------------------------------------------------------------------------


class TestCorrectionSortKey:
    """Tests for correction_sort_key — deterministic tie-break."""

    def test_higher_confidence_wins(self):
        r1 = MatchResult(
            canonical="A", confidence=0.95, strategy="initials",
            entity_kind="person", entity_type="mp",
        )
        r2 = MatchResult(
            canonical="B", confidence=0.90, strategy="phonetic",
            entity_kind="person", entity_type="mp",
        )
        # Higher confidence → lower sort key
        assert correction_sort_key(r1, 1) < correction_sort_key(r2, 1)

    def test_longer_span_wins_on_equal_confidence(self):
        r = MatchResult(
            canonical="A", confidence=0.95, strategy="initials",
            entity_kind="person", entity_type="mp",
        )
        # span_len=2 should win over span_len=1 at equal confidence
        assert correction_sort_key(r, 2) < correction_sort_key(r, 1)

    def test_lower_strategy_rank_wins_on_equal_confidence_and_span(self):
        r1 = MatchResult(
            canonical="A", confidence=0.90, strategy="exact",
            entity_kind="location", entity_type="city",
        )
        r2 = MatchResult(
            canonical="A", confidence=0.90, strategy="fuzzy",
            entity_kind="location", entity_type="city",
        )
        # exact (rank 0) < fuzzy (rank 6)
        assert correction_sort_key(r1, 1) < correction_sort_key(r2, 1)

    def test_lexicographic_canonical_breaks_final_tie(self):
        r1 = MatchResult(
            canonical="Accra", confidence=0.90, strategy="phonetic",
            entity_kind="location", entity_type="city",
        )
        r2 = MatchResult(
            canonical="Kumasi", confidence=0.90, strategy="phonetic",
            entity_kind="location", entity_type="city",
        )
        # "Accra" < "Kumasi" lexicographically
        assert correction_sort_key(r1, 1) < correction_sort_key(r2, 1)

    def test_confidence_rounded_to_6_decimals(self):
        r1 = MatchResult(
            canonical="A", confidence=0.9000001, strategy="exact",
            entity_kind="location", entity_type="city",
        )
        r2 = MatchResult(
            canonical="A", confidence=0.9000002, strategy="exact",
            entity_kind="location", entity_type="city",
        )
        # After rounding to 6 decimals, both are -0.9 → same
        assert correction_sort_key(r1, 1) == correction_sort_key(r2, 1)

    def test_sort_key_tuple_structure(self):
        r = MatchResult(
            canonical="Accra", confidence=0.88, strategy="fuzzy",
            entity_kind="location", entity_type="city",
        )
        key = correction_sort_key(r, 2)
        assert key == (-round(0.88, 6), -2, 6, "Accra")

    def test_unknown_strategy_gets_rank_99(self):
        r = MatchResult(
            canonical="X", confidence=0.50, strategy="unknown_strategy",
            entity_kind="location", entity_type="city",
        )
        key = correction_sort_key(r, 1)
        assert key[2] == 99


# ---------------------------------------------------------------------------
# apply_party_display tests
# ---------------------------------------------------------------------------


class TestApplyPartyDisplay:
    """Tests for apply_party_display — abbreviation vs full name."""

    def test_non_party_returned_unchanged(self, test_env):
        index, _ = test_env
        result = MatchResult(
            canonical="Kumasi", confidence=1.00, strategy="exact",
            entity_kind="location", entity_type="city",
        )
        displayed = apply_party_display(result, "kumasi", index)
        assert displayed is result  # same object, unchanged

    def test_party_short_input_gets_abbreviation(self, test_env):
        index, _ = test_env
        result = MatchResult(
            canonical="National Democratic Congress", confidence=0.98,
            strategy="fused", entity_kind="party", entity_type="party",
        )
        # Original "ndc" (len 3) <= len("NDC") + 1 = 4
        displayed = apply_party_display(result, "ndc", index)
        assert displayed.canonical == "NDC"
        assert displayed.confidence == 0.98
        assert displayed.strategy == "fused"
        assert displayed.entity_kind == "party"

    def test_party_long_input_gets_full_name(self, test_env):
        index, _ = test_env
        result = MatchResult(
            canonical="National Democratic Congress", confidence=0.88,
            strategy="fuzzy", entity_kind="party", entity_type="party",
        )
        # Original is long → keep canonical
        displayed = apply_party_display(result, "national democratic congress", index)
        assert displayed.canonical == "National Democratic Congress"

    def test_party_without_abbreviation_unchanged(self, test_env):
        index, _ = test_env
        # Create a result for a party that has no abbreviation in the map
        result = MatchResult(
            canonical="Unknown Party", confidence=0.90,
            strategy="exact", entity_kind="party", entity_type="party",
        )
        displayed = apply_party_display(result, "unknown party", index)
        assert displayed.canonical == "Unknown Party"
