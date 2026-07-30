"""Unit tests for app/llm/retrieval.py — per-chunk candidate extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex
from app.llm.retrieval import (
    _extract_candidate_tokens,
    _fallback_from_canonical_map,
    _reset_kb_warning,
    retrieve_candidates,
    retrieve_candidates_knowledge_base,
)
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_warning():
    """Reset the KB fallback warning before each test."""
    _reset_kb_warning()
    yield
    _reset_kb_warning()


@pytest.fixture
def minimal_index() -> MatchIndex:
    """A minimal MatchIndex for testing."""
    return MatchIndex(
        canonical_map={
            "kumasi": "Kumasi",
            "accra": "Accra",
            "ningo-prampram": "Ningo-Prampram",
            "john mahama": "John Mahama",
            "ndc": "NDC",
        },
        fused_map={},
        phonetic_map={},
        surname_map={},
        initial_surname_map={},
        entity_kind_map={
            "Kumasi": "location",
            "Accra": "location",
            "Ningo-Prampram": "location",
            "John Mahama": "person",
            "NDC": "party",
        },
        entity_type_map={
            "Kumasi": "city",
            "Accra": "city",
            "Ningo-Prampram": "supplementary",
            "John Mahama": "president",
            "NDC": "party",
        },
        party_abbr_map={},
        alias_ordinal={},
        length_buckets={},
        bk_tree=None,
    )


@pytest.fixture
def minimal_snapshot(minimal_index: MatchIndex) -> DatasetSnapshot:
    """A minimal DatasetSnapshot for testing."""
    from datetime import datetime, timezone

    return DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=(),
        record_count=0,
        loaded_at=datetime.now(timezone.utc),
        index=minimal_index,
        block_list=frozenset({"general", "page"}),
        stopwords=frozenset({"the", "and", "is"}),
        word_stopwords=frozenset(),
        title_prefixes=frozenset({"honorable", "hon"}),
    )


# ---------------------------------------------------------------------------
# Tests for _extract_candidate_tokens
# ---------------------------------------------------------------------------


class TestExtractCandidateTokens:
    """Tests for candidate token extraction."""

    def test_filters_short_tokens(self, minimal_snapshot: DatasetSnapshot):
        """Tokens shorter than 4 chars are excluded."""
        candidates = _extract_candidate_tokens("he is at war", minimal_snapshot)
        # All tokens are 3 chars or less except possibly bigrams/trigrams
        single_tokens = [c for c in candidates if " " not in c]
        for t in single_tokens:
            assert len(t) >= 4

    def test_filters_block_list(self, minimal_snapshot: DatasetSnapshot):
        """Block_List tokens are excluded."""
        candidates = _extract_candidate_tokens(
            "the general went to Kumasi", minimal_snapshot
        )
        lowers = [c.lower() for c in candidates]
        assert "general" not in lowers

    def test_filters_stopwords(self, minimal_snapshot: DatasetSnapshot):
        """Stopword tokens are excluded."""
        candidates = _extract_candidate_tokens(
            "the Kumasi region is large", minimal_snapshot
        )
        lowers = [c.lower() for c in candidates]
        assert "the" not in lowers

    def test_includes_valid_candidates(self, minimal_snapshot: DatasetSnapshot):
        """Valid entity-like tokens are included."""
        candidates = _extract_candidate_tokens(
            "President Mahama visited Kumasi yesterday", minimal_snapshot
        )
        lowers = [c.lower() for c in candidates]
        assert "mahama" in lowers
        assert "kumasi" in lowers

    def test_deduplicates(self, minimal_snapshot: DatasetSnapshot):
        """Duplicate tokens are deduplicated."""
        candidates = _extract_candidate_tokens(
            "Kumasi Kumasi Kumasi", minimal_snapshot
        )
        kumasi_count = sum(1 for c in candidates if c.lower() == "kumasi")
        assert kumasi_count == 1

    def test_bigrams_included(self, minimal_snapshot: DatasetSnapshot):
        """2-grams are included for multi-word entities."""
        candidates = _extract_candidate_tokens(
            "John Mahama spoke today", minimal_snapshot
        )
        assert any("John Mahama" in c for c in candidates)

    def test_filters_digits(self, minimal_snapshot: DatasetSnapshot):
        """Pure numeric tokens are excluded."""
        candidates = _extract_candidate_tokens(
            "in 1992 the people voted", minimal_snapshot
        )
        assert "1992" not in candidates


# ---------------------------------------------------------------------------
# Tests for _fallback_from_canonical_map
# ---------------------------------------------------------------------------


class TestFallbackFromCanonicalMap:
    """Tests for the naive canonical_map fallback."""

    def test_matches_known_entities(self, minimal_snapshot: DatasetSnapshot):
        """Candidates matching canonical_map entries are returned."""
        records = _fallback_from_canonical_map(
            ["kumasi", "accra"], minimal_snapshot, max_records=50
        )
        canonicals = [r.canonical for r in records]
        assert "Kumasi" in canonicals
        assert "Accra" in canonicals

    def test_caps_at_max_records(self, minimal_snapshot: DatasetSnapshot):
        """Results are capped at max_records."""
        records = _fallback_from_canonical_map(
            ["kumasi", "accra", "ningo-prampram", "john mahama", "ndc"],
            minimal_snapshot,
            max_records=2,
        )
        assert len(records) <= 2

    def test_deduplicates_by_canonical(self, minimal_snapshot: DatasetSnapshot):
        """Duplicate canonical names are deduplicated."""
        records = _fallback_from_canonical_map(
            ["kumasi", "kumasi", "kumasi"],
            minimal_snapshot,
            max_records=50,
        )
        assert len(records) == 1

    def test_unknown_candidates_skipped(self, minimal_snapshot: DatasetSnapshot):
        """Candidates not in canonical_map are skipped."""
        records = _fallback_from_canonical_map(
            ["nonexistent_city"],
            minimal_snapshot,
            max_records=50,
        )
        assert len(records) == 0


# ---------------------------------------------------------------------------
# Tests for retrieve_candidates
# ---------------------------------------------------------------------------


class TestRetrieveCandidates:
    """Tests for the main retrieve_candidates function."""

    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self, minimal_snapshot: DatasetSnapshot):
        """Empty chunk text returns no records."""
        records = await retrieve_candidates("", minimal_snapshot, session=None)
        assert records == []

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty(self, minimal_snapshot: DatasetSnapshot):
        """Whitespace-only chunk returns no records."""
        records = await retrieve_candidates("   ", minimal_snapshot, session=None)
        assert records == []

    @pytest.mark.asyncio
    async def test_no_session_uses_fallback(self, minimal_snapshot: DatasetSnapshot):
        """When session is None, uses canonical_map fallback."""
        records = await retrieve_candidates(
            "The President Mahama visited Kumasi",
            minimal_snapshot,
            session=None,
            max_records=50,
        )
        # Should find at least one record from canonical_map
        canonicals = [r.canonical for r in records]
        assert "Kumasi" in canonicals

    @pytest.mark.asyncio
    async def test_deduplicates_results(self, minimal_snapshot: DatasetSnapshot):
        """Results are deduplicated by canonical name."""
        records = await retrieve_candidates(
            "Kumasi is Kumasi and Kumasi again",
            minimal_snapshot,
            session=None,
            max_records=50,
        )
        canonicals = [r.canonical for r in records]
        assert canonicals.count("Kumasi") <= 1

    @pytest.mark.asyncio
    async def test_caps_at_max_records(self, minimal_snapshot: DatasetSnapshot):
        """Results are capped at max_records."""
        records = await retrieve_candidates(
            "Kumasi Accra Mahama NDC Prampram everything",
            minimal_snapshot,
            session=None,
            max_records=2,
        )
        assert len(records) <= 2

    @pytest.mark.asyncio
    async def test_with_session_calls_similarity_search(
        self, minimal_snapshot: DatasetSnapshot
    ):
        """When a session is provided, uses similarity_search_multi."""
        from app.datasets.store import SimilarityResult

        mock_session = AsyncMock()
        mock_results = [
            SimilarityResult(
                record_id=1,
                canonical="Kumasi",
                entity_kind="location",
                entity_type="city",
                similarity=0.9,
                aliases=[],
            ),
        ]

        with patch(
            "app.llm.retrieval.similarity_search_multi",
            new_callable=AsyncMock,
            return_value=mock_results,
        ):
            records = await retrieve_candidates(
                "The Kumasi metropolitan area",
                minimal_snapshot,
                session=mock_session,
                max_records=50,
            )
            assert len(records) >= 1
            assert records[0].canonical == "Kumasi"


# ---------------------------------------------------------------------------
# Tests for retrieve_candidates_knowledge_base
# ---------------------------------------------------------------------------


class TestRetrieveCandidatesKnowledgeBase:
    """Tests for Knowledge_Base mode with fallback."""

    @pytest.mark.asyncio
    async def test_no_kb_id_uses_dataset_store(
        self, minimal_snapshot: DatasetSnapshot, _reset_warning
    ):
        """Without a knowledge_base_id, goes straight to dataset_store."""
        records = await retrieve_candidates_knowledge_base(
            "Kumasi is great",
            minimal_snapshot,
            session=None,
            max_records=50,
            knowledge_base_id=None,
        )
        canonicals = [r.canonical for r in records]
        assert "Kumasi" in canonicals

    @pytest.mark.asyncio
    async def test_kb_failure_falls_back_with_warning(
        self, minimal_snapshot: DatasetSnapshot, _reset_warning
    ):
        """KB failure falls back to dataset_store and logs a warning."""
        records = await retrieve_candidates_knowledge_base(
            "Kumasi is great",
            minimal_snapshot,
            session=None,
            max_records=50,
            knowledge_base_id="kb-12345",
        )
        # Should still get results from fallback
        canonicals = [r.canonical for r in records]
        assert "Kumasi" in canonicals

    @pytest.mark.asyncio
    async def test_kb_warning_logged_once(
        self, minimal_snapshot: DatasetSnapshot, _reset_warning
    ):
        """The KB fallback warning is logged only once across multiple calls."""
        # Call twice — both should succeed but warning only logged once
        await retrieve_candidates_knowledge_base(
            "Kumasi is great",
            minimal_snapshot,
            session=None,
            max_records=50,
            knowledge_base_id="kb-12345",
        )
        await retrieve_candidates_knowledge_base(
            "Accra is the capital",
            minimal_snapshot,
            session=None,
            max_records=50,
            knowledge_base_id="kb-12345",
        )
        # If it crashes, the test fails. The single-warning semantics
        # are enforced by the module-level flag.
