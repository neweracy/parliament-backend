"""Unit tests for the LLM_Refiner stage wiring in app/pipeline.py.

Covers:
- Example: correction count reported with a stub client
- Example: `skipped` branch when llm_refine is false
- Example: `skipped` branch when LLM_ENABLED is false
- Edge case: refiner raises an unexpected exception → degraded
- Edge case: every chunk times out → degraded
- Edge case: one chunk times out, pre-LLM words survive for that chunk,
  others still apply

Requirements: 4.1, 4.2, 4.3, 9.4
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex
from app.llm.bedrock import BedrockClient
from app.models.request import CorrectionOptions, CorrectionRequest, Word
from app.pipeline import run_pipeline


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


def _make_snapshot() -> DatasetSnapshot:
    """Create a minimal DatasetSnapshot."""
    return DatasetSnapshot(
        version="2026-07-09T10:00:00+00:00",
        records=(),
        record_count=0,
        loaded_at=datetime(2026, 7, 9, 10, 0, 0, tzinfo=timezone.utc),
        index=_make_empty_index(),
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )


def _make_cache(snapshot: DatasetSnapshot | None = None) -> DatasetCache:
    """Create a mock DatasetCache."""
    cache = MagicMock(spec=DatasetCache)
    cache.get_snapshot.return_value = snapshot
    return cache


def _make_settings(**overrides) -> Settings:
    """Create Settings with required secrets filled in."""
    defaults = {
        "service_token": "test-token",
        "database_url": "postgresql+psycopg://u:p@localhost/test",
        "llm_enabled": True,
        "bedrock_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_bedrock_client(configured: bool = True) -> MagicMock:
    """Create a mock BedrockClient."""
    client = MagicMock(spec=BedrockClient)
    client.is_configured = configured
    client.model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    return client


# ---------------------------------------------------------------------------
# Example tests: skipped branch
# ---------------------------------------------------------------------------


class TestLLMSkippedBranches:
    """LLM stage returns 'skipped' when disabled."""

    @pytest.mark.asyncio
    async def test_skipped_when_llm_refine_false(self):
        """llm_status is 'skipped' when request.options.llm_refine is False."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=False),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client()

        response = await run_pipeline(
            request, cache, bedrock_client=client, settings=settings
        )

        assert response.metadata.llm_status == "skipped"
        assert response.metadata.bedrock_corrections is None

    @pytest.mark.asyncio
    async def test_skipped_when_llm_enabled_false(self):
        """llm_status is 'skipped' when settings.llm_enabled is False."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=False)
        client = _make_bedrock_client()

        response = await run_pipeline(
            request, cache, bedrock_client=client, settings=settings
        )

        assert response.metadata.llm_status == "skipped"

    @pytest.mark.asyncio
    async def test_unconfigured_when_client_none(self):
        """llm_status is 'unconfigured' when bedrock_client is None."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)

        response = await run_pipeline(
            request, cache, bedrock_client=None, settings=settings
        )

        assert response.metadata.llm_status == "unconfigured"

    @pytest.mark.asyncio
    async def test_unconfigured_when_client_not_configured(self):
        """llm_status is 'unconfigured' when client.is_configured is False."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client(configured=False)

        response = await run_pipeline(
            request, cache, bedrock_client=client, settings=settings
        )

        assert response.metadata.llm_status == "unconfigured"


# ---------------------------------------------------------------------------
# Example test: correction count reported
# ---------------------------------------------------------------------------


class TestLLMCorrectionCount:
    """LLM stage reports correction count when invoked."""

    @pytest.mark.asyncio
    async def test_correction_count_reported(self):
        """bedrock_corrections is populated from refine_chunks return."""
        request = CorrectionRequest(
            transcript="hello world kumase",
            words=[
                Word(word="hello"),
                Word(word="world"),
                Word(word="kumase"),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client(configured=True)

        # Mock refine_chunks to return a correction count
        with patch("app.pipeline.refine_chunks") as mock_refine:
            mock_refine.return_value = (
                [{"word": "hello"}, {"word": "world"}, {"word": "Kumasi"}],
                "ok",
                1,
            )

            response = await run_pipeline(
                request, cache, bedrock_client=client, settings=settings
            )

        assert response.metadata.llm_status == "applied"
        assert response.metadata.bedrock_corrections == 1

    @pytest.mark.asyncio
    async def test_partial_status_maps_to_degraded(self):
        """Internal 'partial' status maps to external 'degraded'."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client(configured=True)

        with patch("app.pipeline.refine_chunks") as mock_refine:
            mock_refine.return_value = (
                [{"word": "hello"}, {"word": "world"}],
                "partial",
                0,
            )

            response = await run_pipeline(
                request, cache, bedrock_client=client, settings=settings
            )

        assert response.metadata.llm_status == "degraded"


# ---------------------------------------------------------------------------
# Edge case: refiner raises
# ---------------------------------------------------------------------------


class TestLLMRefinerRaises:
    """Pipeline handles refiner exceptions gracefully."""

    @pytest.mark.asyncio
    async def test_refiner_raises_returns_degraded(self):
        """Unexpected exception in refine_chunks → llm_status 'degraded'."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client(configured=True)

        with patch("app.pipeline.refine_chunks") as mock_refine:
            mock_refine.side_effect = RuntimeError("Bedrock exploded")

            response = await run_pipeline(
                request, cache, bedrock_client=client, settings=settings
            )

        assert response.metadata.llm_status == "degraded"
        assert response.metadata.bedrock_corrections is None
        # Words should still be present (rule stage results preserved)
        assert len(response.words) == 2

    @pytest.mark.asyncio
    async def test_all_chunks_timeout_returns_degraded(self):
        """When all chunks time out → 'failed' internal → 'degraded'."""
        request = CorrectionRequest(
            transcript="hello world",
            words=[Word(word="hello"), Word(word="world")],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client(configured=True)

        with patch("app.pipeline.refine_chunks") as mock_refine:
            mock_refine.return_value = (
                [{"word": "hello"}, {"word": "world"}],
                "failed",
                0,
            )

            response = await run_pipeline(
                request, cache, bedrock_client=client, settings=settings
            )

        assert response.metadata.llm_status == "degraded"
        assert response.metadata.bedrock_corrections is None

    @pytest.mark.asyncio
    async def test_one_chunk_timeout_partial_apply(self):
        """One chunk timeout → 'partial', others still apply corrections."""
        request = CorrectionRequest(
            transcript="hello world kumase accra",
            words=[
                Word(word="hello"),
                Word(word="world"),
                Word(word="kumase"),
                Word(word="accra"),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        snapshot = _make_snapshot()
        cache = _make_cache(snapshot)
        settings = _make_settings(llm_enabled=True)
        client = _make_bedrock_client(configured=True)

        with patch("app.pipeline.refine_chunks") as mock_refine:
            # Partial: one chunk succeeded with 1 correction,
            # one chunk timed out
            mock_refine.return_value = (
                [
                    {"word": "hello"},
                    {"word": "world"},
                    {"word": "Kumasi"},
                    {"word": "accra"},
                ],
                "partial",
                1,
            )

            response = await run_pipeline(
                request, cache, bedrock_client=client, settings=settings
            )

        assert response.metadata.llm_status == "degraded"
        assert response.metadata.bedrock_corrections == 1
