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
from app.models.response import Metadata
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


# ---------------------------------------------------------------------------
# 6. Gate-rejection total metadata counter (task 6.1, Req 13.6, 14.4, 14.11)
# ---------------------------------------------------------------------------


class TestGateRejectionMetadata:
    """The single gate-rejection total is reported when > 0 and omitted at 0.

    Req 13.6: report the total as a single counter across every gate value when
    the count is greater than zero. Req 14.4: omit the counter when zero. Req
    14.11: the field is additive under the ``gateRejections`` camelCase alias.
    """

    def test_metadata_field_serializes_under_alias_when_positive(self):
        """A positive total serializes under the gateRejections alias."""
        meta = Metadata(gate_rejections=3)
        dumped = meta.model_dump(by_alias=True, exclude_none=True)
        assert dumped["gateRejections"] == 3

    def test_metadata_field_omitted_when_none(self):
        """None (the default, used for a zero total) is omitted from output."""
        meta = Metadata(gate_rejections=None)
        dumped = meta.model_dump(by_alias=True, exclude_none=True)
        assert "gateRejections" not in dumped
        # No snake_case leakage either.
        assert "gate_rejections" not in dumped

    @pytest.mark.asyncio
    async def test_pipeline_reports_total_when_rejections_occur(self):
        """When gate observability reports a positive total the metadata carries it."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            correlation_id="corr-gate",
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        # Force a positive gate-rejection total without changing correction output.
        with patch("app.pipeline._emit_gate_observability", return_value=4):
            response = await run_pipeline(request, cache)

        assert response.metadata.gate_rejections == 4
        response_dict = response.model_dump(by_alias=True, exclude_none=True)
        assert response_dict["metadata"]["gateRejections"] == 4

    @pytest.mark.asyncio
    async def test_pipeline_omits_total_when_zero_rejections(self):
        """Baseline path (zero rejections) omits the field entirely (Req 14.4)."""
        request = CorrectionRequest(
            transcript="nothing to correct here",
            words=[],
            correlation_id="corr-baseline",
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot=snapshot)

        # Default flag-off path yields a total of zero; assert it is omitted.
        response = await run_pipeline(request, cache)

        assert response.metadata.gate_rejections is None
        response_dict = response.model_dump(by_alias=True, exclude_none=True)
        assert "gateRejections" not in response_dict["metadata"]
        assert "gate_rejections" not in response_dict["metadata"]


# ---------------------------------------------------------------------------
# Provider profile threading at the pipeline boundary (task 2.12.3;
# Req 1.5, 2.6, 2.7, 12.9, 14.1, 14.10)
# ---------------------------------------------------------------------------

from app.config import Settings  # noqa: E402
from app.correction.provider_profiles import default_profile  # noqa: E402


def _capture_gate_context(request: CorrectionRequest, settings: Settings):
    """Run _run_rule_stages capturing the GateContext handed to the engine.

    Patches correct_text (the first engine call) to record its gate_context
    kwarg, so tests can assert what provider/profile the boundary resolved.
    """
    captured = {}

    real_snapshot = _make_snapshot()

    def _fake_correct_text(*args, **kwargs):
        captured["gate_context"] = kwargs.get("gate_context")
        from app.correction.engine import TextCorrectionResult

        return TextCorrectionResult(text=request.transcript, corrections=[], entities_found=[])

    def _fake_correct_words(*args, **kwargs):
        from app.correction.engine import WordCorrectionResult

        return WordCorrectionResult(words=[], corrections=[], entities_found=[])

    with (
        patch("app.pipeline.correct_text", _fake_correct_text),
        patch("app.pipeline.correct_words", _fake_correct_words),
    ):
        _run_rule_stages(request, real_snapshot, settings, None)

    return captured["gate_context"]


class TestPipelineProviderProfileThreading:
    """The pipeline boundary threads request.options.provider and its resolved
    profile onto the GateContext (task 2.12.3)."""

    def test_flag_off_resolves_deepgram_for_khaya(self):
        # Req 12.9: with provider_profiles_enabled off (default), a khaya request
        # still resolves the deepgram profile — baseline-equivalent.
        settings = Settings(service_token="x", database_url="x")
        assert settings.provider_profiles_enabled is False
        request = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello")],
            options=CorrectionOptions(provider="khaya"),
        )
        ctx = _capture_gate_context(request, settings)
        assert ctx.provider == "khaya"
        # Flag off → deepgram profile for every provider (reject_unknown=True).
        assert ctx.profile == default_profile("deepgram")
        assert ctx.profile.lexicon_gate_reject_unknown is True

    def test_flag_off_default_provider_is_deepgram(self):
        # Req 14.1: an omitted provider defaults to "deepgram".
        settings = Settings(service_token="x", database_url="x")
        request = CorrectionRequest(transcript="hello", words=[Word(word="hello")])
        ctx = _capture_gate_context(request, settings)
        assert ctx.provider == "deepgram"
        assert ctx.profile == default_profile("deepgram")

    def test_flag_on_resolves_khaya_policy(self):
        # Task 2.12.1: with the flag on, a khaya request resolves the khaya
        # profile whose Unknown-confidence policy opts out of rejection.
        settings = Settings(
            service_token="x",
            database_url="x",
            provider_profiles_enabled=True,
        )
        request = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello")],
            options=CorrectionOptions(provider="khaya"),
        )
        ctx = _capture_gate_context(request, settings)
        assert ctx.provider == "khaya"
        assert ctx.profile.lexicon_gate_reject_unknown is False

    def test_flag_on_deepgram_keeps_reject_policy(self):
        # Task 2.12.1: deepgram keeps the Req 2.6 rejection even with the flag on.
        settings = Settings(
            service_token="x",
            database_url="x",
            provider_profiles_enabled=True,
        )
        request = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello")],
            options=CorrectionOptions(provider="deepgram"),
        )
        ctx = _capture_gate_context(request, settings)
        assert ctx.provider == "deepgram"
        assert ctx.profile.lexicon_gate_reject_unknown is True
