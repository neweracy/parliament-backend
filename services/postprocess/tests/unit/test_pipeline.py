"""Unit tests for app/pipeline.py — pipeline stage orchestration.

Tests the pipeline's assembly logic, metadata construction, and Entity_Summary
deduplication. Uses a minimal mock of the DatasetCache with a real snapshot
to verify end-to-end flow without a database.

Requirements: 6.6, 2.1, 2.3, 2.4, 10.7, 10.8
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex
from app.models.request import CorrectionOptions, CorrectionRequest, Word
from app.pipeline import _build_entity_summary, _run_rule_stages, run_pipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_empty_index() -> MatchIndex:
    """Create a minimal empty MatchIndex for testing."""
    return MatchIndex(
        canonical_map={},
        fused_map={},
        phonetic_map={},
        surname_map={},
        initial_surname_map={},
        entity_kind_map={},
        entity_type_map={},
        party_abbr_map={},
        alias_ordinal={},
        length_buckets={},
        bk_tree=None,
    )


def _make_snapshot(index: MatchIndex | None = None) -> DatasetSnapshot:
    """Create a minimal DatasetSnapshot for testing."""
    return DatasetSnapshot(
        version="2026-07-09T10:00:00+00:00",
        records=(),
        record_count=0,
        loaded_at=datetime(2026, 7, 9, 10, 0, 0, tzinfo=timezone.utc),
        index=index or _make_empty_index(),
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )


def _make_cache(snapshot: DatasetSnapshot | None = None) -> DatasetCache:
    """Create a mock DatasetCache that returns the given snapshot."""
    cache = MagicMock(spec=DatasetCache)
    cache.get_snapshot.return_value = snapshot
    return cache


# ---------------------------------------------------------------------------
# 1. Pipeline returns passthrough when snapshot is None
# ---------------------------------------------------------------------------


class TestPipelineNoSnapshot:
    """Pipeline returns passthrough response when snapshot is missing."""

    @pytest.mark.asyncio
    async def test_returns_passthrough_when_no_snapshot(self):
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            correlation_id="test-123",
        )
        cache = _make_cache(snapshot=None)

        response = await run_pipeline(request, cache)

        assert response.transcript == "hello world"
        assert len(response.words) == 2
        assert response.metadata.llm_status == "unconfigured"
        assert response.metadata.postprocessing_status == "skipped"
        assert response.metadata.correlation_id == "test-123"
        assert response.entities == []
        assert response.corrections == []


# ---------------------------------------------------------------------------
# 2. Pipeline runs rule stages and returns complete response
# ---------------------------------------------------------------------------


class TestPipelineBasicFlow:
    """Pipeline runs stages and assembles the response correctly."""

    @pytest.mark.asyncio
    async def test_empty_transcript(self):
        """Empty transcript passes through without error."""
        request = CorrectionRequest(
            transcript="",
            words=[],
            correlation_id="corr-empty",
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        assert response.transcript == ""
        assert response.words == []
        assert response.metadata.postprocessing_status == "applied"
        assert response.metadata.dataset_version == snapshot.version
        assert response.metadata.correlation_id == "corr-empty"

    @pytest.mark.asyncio
    async def test_no_corrections_needed(self):
        """Text with no entities passes through unchanged."""
        request = CorrectionRequest(
            transcript="this is a test sentence",
            words=[
                Word(word="this", start=0.0, end=0.2, confidence=0.99),
                Word(word="is", start=0.2, end=0.3, confidence=0.99),
                Word(word="a", start=0.3, end=0.4, confidence=0.99),
                Word(word="test", start=0.4, end=0.6, confidence=0.99),
                Word(word="sentence", start=0.6, end=1.0, confidence=0.99),
            ],
            correlation_id="corr-nochange",
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        assert response.transcript == "this is a test sentence"
        assert len(response.words) == 5
        assert response.metadata.postprocessing_status == "applied"
        # Zero-valued counters should be None (omitted on serialization)
        assert response.metadata.location_corrections is None
        assert response.metadata.year_corrections is None
        assert response.metadata.bedrock_corrections is None

    @pytest.mark.asyncio
    async def test_rule_latency_recorded(self):
        """Rule latency is always recorded as a non-negative integer."""
        request = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello")],
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        assert response.metadata.rule_latency_ms is not None
        assert response.metadata.rule_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_llm_latency_recorded(self):
        """LLM latency is recorded (even when LLM is a no-op placeholder)."""
        request = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello")],
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        assert response.metadata.llm_latency_ms is not None
        assert response.metadata.llm_latency_ms >= 0


# ---------------------------------------------------------------------------
# 3. LLM gate behaviour
# ---------------------------------------------------------------------------


class TestLLMGate:
    """LLM_Refiner gate sets the correct status."""

    @pytest.mark.asyncio
    async def test_llm_skipped_when_option_false(self):
        """LLM status is 'skipped' when options.llm_refine is False."""
        request = CorrectionRequest(
            transcript="hello",
            words=[],
            options=CorrectionOptions(llm_refine=False),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        assert response.metadata.llm_status == "skipped"

    @pytest.mark.asyncio
    async def test_llm_unconfigured_when_option_true(self):
        """LLM status is 'unconfigured' when enabled but not implemented."""
        request = CorrectionRequest(
            transcript="hello",
            words=[],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        assert response.metadata.llm_status == "unconfigured"


# ---------------------------------------------------------------------------
# 4. Entity_Summary deduplication
# ---------------------------------------------------------------------------


class TestEntitySummaryBuilder:
    """_build_entity_summary deduplicates and counts mentions."""

    def test_empty_input(self):
        result = _build_entity_summary([], [])
        assert result == []

    def test_single_entity_one_mention(self):
        result = _build_entity_summary(
            [("Kumasi", "location", "city")],
            [],
        )
        assert len(result) == 1
        assert result[0].name == "Kumasi"
        assert result[0].kind == "location"
        assert result[0].type == "city"
        assert result[0].mentions == 1

    def test_deduplicated_with_mention_count(self):
        """Same entity appearing multiple times is deduplicated with total mentions."""
        text_entities = [
            ("Kumasi", "location", "city"),
            ("Kumasi", "location", "city"),
        ]
        word_entities = [
            ("Kumasi", "location", "city"),
            ("Accra", "location", "city"),
        ]
        result = _build_entity_summary(text_entities, word_entities)

        assert len(result) == 2
        kumasi = next(e for e in result if e.name == "Kumasi")
        accra = next(e for e in result if e.name == "Accra")
        assert kumasi.mentions == 3
        assert accra.mentions == 1

    def test_preserves_first_appearance_order(self):
        """Entities are ordered by their first appearance."""
        text_entities = [
            ("B.B. Carboo", "person", "mp"),
            ("Ningo-Prampram", "location", "supplementary"),
        ]
        word_entities = [
            ("Ningo-Prampram", "location", "supplementary"),
        ]
        result = _build_entity_summary(text_entities, word_entities)

        assert result[0].name == "B.B. Carboo"
        assert result[1].name == "Ningo-Prampram"
        assert result[1].mentions == 2

    def test_different_types_same_name_not_merged(self):
        """Same name with different kind/type is treated as separate entities."""
        text_entities = [
            ("National", "location", "supplementary"),
            ("National", "party", "party"),
        ]
        result = _build_entity_summary(text_entities, [])

        assert len(result) == 2


# ---------------------------------------------------------------------------
# 5. Metadata counter omission
# ---------------------------------------------------------------------------


class TestMetadataCounterOmission:
    """Zero-valued counters are set to None so exclude_none omits them."""

    @pytest.mark.asyncio
    async def test_zero_counters_are_none(self):
        """When no corrections are applied, counters are None (not 0)."""
        request = CorrectionRequest(
            transcript="nothing to correct here",
            words=[],
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        # Serialize to verify omission
        response_dict = response.model_dump(by_alias=True, exclude_none=True)
        meta = response_dict["metadata"]

        assert "location_corrections" not in meta
        assert "year_corrections" not in meta
        assert "bedrock_corrections" not in meta

    @pytest.mark.asyncio
    async def test_correlation_id_preserved(self):
        """correlationId from request appears in metadata."""
        request = CorrectionRequest(
            transcript="test",
            words=[],
            correlation_id="abc-123-def",
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        response = await run_pipeline(request, cache)

        response_dict = response.model_dump(by_alias=True, exclude_none=True)
        assert response_dict["metadata"]["correlationId"] == "abc-123-def"
