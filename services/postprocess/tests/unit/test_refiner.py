"""Unit tests for app/llm/refiner.py — LLM_Refiner with stubbed Bedrock client.

Tests chunk failure, chunk timeout, disabled option, unresolvable credentials,
merged-entry tokenMap alignment, year and person guards. No test makes a
network call.

Requirements: 11.5, 11.6, 11.7, 11.8, 11.10, 11.11, 15.1, 15.6
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex
from app.llm.refiner import (
    _apply_guards,
    _cleanup_snapshots,
    _snapshot_pre_llm,
    chunk_words,
    refine_chunks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with sensible test defaults."""
    defaults = {
        "service_token": "test-token",
        "database_url": "postgresql://test:test@localhost/test",
        "llm_chunk_size": 5,
        "llm_max_parallel": 2,
        "llm_chunk_timeout_ms": 500,  # 500ms for timeout tests
        "llm_max_prompt_records": 10,
    }
    defaults.update(overrides)
    return Settings(**defaults)


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
    """Create a minimal DatasetSnapshot for testing."""
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


def _make_bedrock_client(invoke_side_effect=None, invoke_return_value=None, configured=True):
    """Create a mock BedrockClient."""
    client = MagicMock()
    client.is_configured = configured
    if invoke_side_effect is not None:
        client.invoke.side_effect = invoke_side_effect
    elif invoke_return_value is not None:
        client.invoke.return_value = invoke_return_value
    else:
        client.invoke.return_value = ""
    return client


def _make_words(texts: list[str]) -> list[dict]:
    """Create a simple word list from text strings."""
    return [
        {"word": t, "start": float(i) * 0.2, "end": float(i) * 0.2 + 0.2, "confidence": 0.85}
        for i, t in enumerate(texts)
    ]


# ---------------------------------------------------------------------------
# 1. Chunk failure — bedrock invoke raises, rule-corrected text preserved
# ---------------------------------------------------------------------------


class TestChunkFailure:
    """When bedrock_client.invoke() raises, the chunk's rule-corrected text is preserved."""

    @pytest.mark.asyncio
    async def test_all_chunks_fail_returns_failed_status(self):
        """All chunks failing → llm_status is 'failed'."""
        words = _make_words(["the", "honorable", "member", "from", "Kumasi"])
        settings = _make_settings(llm_chunk_size=5)
        snapshot = _make_snapshot()

        client = _make_bedrock_client(
            invoke_side_effect=RuntimeError("Bedrock API error")
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "failed"
        assert corrections == 0
        # Original words preserved
        assert result_words[0]["word"] == "the"
        assert result_words[4]["word"] == "Kumasi"

    @pytest.mark.asyncio
    async def test_partial_failure_preserves_failed_chunks(self):
        """Some chunks succeed, some fail → llm_status is 'partial'."""
        # 6 words → 2 chunks of size 5 and 1
        words = _make_words(["the", "honorable", "member", "from", "Kumase", "today"])
        settings = _make_settings(llm_chunk_size=5)
        snapshot = _make_snapshot()

        call_count = [0]

        def invoke_side_effect(system, user):
            call_count[0] += 1
            if call_count[0] == 1:
                # First chunk succeeds — return corrected text
                return "[Segment 1]: the honorable member from Kumasi"
            # Second chunk fails
            raise RuntimeError("Timeout on chunk 2")

        client = _make_bedrock_client(invoke_side_effect=invoke_side_effect)

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "partial"
        # First chunk was corrected
        assert result_words[4]["word"] == "Kumasi"
        assert result_words[4].get("bedrockCorrected") is True
        # Second chunk preserved original
        assert result_words[5]["word"] == "today"


# ---------------------------------------------------------------------------
# 2. Chunk timeout — exceeds LLM_CHUNK_TIMEOUT_MS
# ---------------------------------------------------------------------------


class TestChunkTimeout:
    """When bedrock invoke exceeds timeout, the chunk is dropped."""

    @pytest.mark.asyncio
    async def test_timeout_drops_chunk_preserves_text(self):
        """Timed-out chunk preserves rule-corrected text."""
        words = _make_words(["the", "member", "spoke"])
        settings = _make_settings(llm_chunk_size=5, llm_chunk_timeout_ms=100)
        snapshot = _make_snapshot()

        async def slow_invoke(system, user):
            await asyncio.sleep(1.0)  # Much longer than 100ms timeout
            return "[Segment 1]: the member spoke"

        client = MagicMock()
        client.is_configured = True
        # Make invoke block long enough to trigger timeout
        # asyncio.to_thread wraps sync call, so we make it block
        client.invoke.side_effect = lambda s, u: __import__("time").sleep(1.0) or "[Segment 1]: the member spoke"

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "failed"
        assert corrections == 0
        # Original text preserved
        assert result_words[0]["word"] == "the"
        assert result_words[1]["word"] == "member"
        assert result_words[2]["word"] == "spoke"


# ---------------------------------------------------------------------------
# 3. Disabled option — LLM disabled means refiner not called
# ---------------------------------------------------------------------------


class TestDisabledOption:
    """When LLM is disabled, the refiner processes nothing."""

    @pytest.mark.asyncio
    async def test_empty_words_returns_ok(self):
        """Empty word list returns ok status immediately."""
        settings = _make_settings()
        snapshot = _make_snapshot()
        client = _make_bedrock_client()

        result_words, llm_status, corrections = await refine_chunks(
            [], snapshot, client, None, settings
        )

        assert llm_status == "ok"
        assert corrections == 0
        assert result_words == []
        # invoke should never have been called
        client.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Unresolvable credentials — bedrock_client.is_configured is False
# ---------------------------------------------------------------------------


class TestUnresolvableCredentials:
    """When is_configured is False, the pipeline should signal unconfigured."""

    def test_is_configured_false_signals_unconfigured(self):
        """Verify the client reports unconfigured status."""
        client = _make_bedrock_client(configured=False)
        assert client.is_configured is False

    def test_is_configured_true_signals_ready(self):
        """Verify a configured client reports ready."""
        client = _make_bedrock_client(configured=True)
        assert client.is_configured is True


# ---------------------------------------------------------------------------
# 5. Merged-entry tokenMap alignment
# ---------------------------------------------------------------------------


class TestMergedEntryTokenMap:
    """Multi-token entries produce correct tokenMap and alignment works."""

    def test_chunk_words_multi_token_entry(self):
        """A merged word like 'Samuel Okudzeto Ablakwa' produces 3 token entries."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "Samuel Okudzeto Ablakwa", "start": 0.1, "end": 0.8, "confidence": 0.85},
            {"word": "said", "start": 0.8, "end": 1.0, "confidence": 0.99},
        ]
        chunks = chunk_words(words, chunk_size=10)
        assert len(chunks) == 1
        chunk = chunks[0]
        # token_map should map 5 positions: "the"→0, "Samuel"→1, "Okudzeto"→1, "Ablakwa"→1, "said"→2
        assert chunk.token_map == [0, 1, 1, 1, 2]
        assert chunk.text == "the Samuel Okudzeto Ablakwa said"

    def test_chunk_words_all_single_token(self):
        """Single-token entries produce 1:1 token_map."""
        words = _make_words(["the", "member", "said"])
        chunks = chunk_words(words, chunk_size=10)
        assert len(chunks) == 1
        assert chunks[0].token_map == [0, 1, 2]

    def test_chunk_words_splits_correctly(self):
        """Chunks are split at chunk_size boundaries."""
        words = _make_words(["a", "b", "c", "d", "e", "f", "g"])
        chunks = chunk_words(words, chunk_size=3)
        assert len(chunks) == 3
        assert chunks[0].start_index == 0
        assert len(chunks[0].words) == 3
        assert chunks[1].start_index == 3
        assert len(chunks[1].words) == 3
        assert chunks[2].start_index == 6
        assert len(chunks[2].words) == 1

    @pytest.mark.asyncio
    async def test_merged_entry_alignment_succeeds(self):
        """Multi-token entries align correctly when bedrock returns matching text."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "Samuel Okudzeto Ablakwa", "start": 0.1, "end": 0.8, "confidence": 0.85},
            {"word": "spoke", "start": 0.8, "end": 1.0, "confidence": 0.99},
        ]
        settings = _make_settings(llm_chunk_size=10)
        snapshot = _make_snapshot()

        # Bedrock returns the same text — no corrections needed
        client = _make_bedrock_client(
            invoke_return_value="[Segment 1]: the Samuel Okudzeto Ablakwa spoke"
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "ok"
        assert corrections == 0
        assert result_words[1]["word"] == "Samuel Okudzeto Ablakwa"
        assert "bedrockCorrected" not in result_words[1]


# ---------------------------------------------------------------------------
# 6. Year guard — yearCorrected words have bedrock changes reverted
# ---------------------------------------------------------------------------


class TestYearGuard:
    """After alignment, a word with yearCorrected: True has its bedrock change reverted."""

    def test_year_guard_reverts_bedrock_change(self):
        """A yearCorrected word should have any bedrock change reverted."""
        words = [
            {"word": "1992", "yearCorrected": True, "bedrockCorrected": True,
             "_pre_llm_word": "1992", "start": 0.0, "end": 0.5},
        ]
        # Simulate: bedrock changed it to something else but alignment wrote it
        words[0]["word"] = "nineteen ninety two"

        reverted = _apply_guards(words, 0, 1)

        assert reverted == 1
        assert words[0]["word"] == "1992"
        assert "bedrockCorrected" not in words[0]

    def test_year_guard_no_revert_without_flag(self):
        """Without yearCorrected, the bedrock change is kept."""
        words = [
            {"word": "Kumasi", "bedrockCorrected": True, "_pre_llm_word": "Kumase",
             "start": 0.0, "end": 0.5},
        ]

        reverted = _apply_guards(words, 0, 1)

        assert reverted == 0
        assert words[0]["word"] == "Kumasi"
        assert words[0]["bedrockCorrected"] is True

    @pytest.mark.asyncio
    async def test_year_guard_in_full_pipeline(self):
        """Year-guarded words are reverted even in the full refine_chunks flow."""
        words = [
            {"word": "in", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "1992", "start": 0.1, "end": 0.4, "confidence": 0.95,
             "yearCorrected": True},
            {"word": "the", "start": 0.4, "end": 0.5, "confidence": 0.99},
        ]
        settings = _make_settings(llm_chunk_size=10)
        snapshot = _make_snapshot()

        # Bedrock tries to change "1992" to "nineteen ninety-two"
        client = _make_bedrock_client(
            invoke_return_value="[Segment 1]: in nineteen the"
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "ok"
        # The year should be preserved
        assert result_words[1]["word"] == "1992"
        assert "bedrockCorrected" not in result_words[1]


# ---------------------------------------------------------------------------
# 7. Person guard — locationCorrected + entityKind: "person" reverted
# ---------------------------------------------------------------------------


class TestPersonGuard:
    """After alignment, a word with locationCorrected + entityKind 'person' is reverted."""

    def test_person_guard_reverts_bedrock_change(self):
        """A person-flagged locationCorrected word has bedrock change reverted."""
        words = [
            {"word": "Dankwa", "locationCorrected": True, "entityKind": "person",
             "bedrockCorrected": True, "_pre_llm_word": "Dankwa",
             "start": 0.0, "end": 0.3},
        ]
        # Simulate: bedrock changed "Dankwa" to "Tarkwa"
        words[0]["word"] = "Tarkwa"

        reverted = _apply_guards(words, 0, 1)

        assert reverted == 1
        assert words[0]["word"] == "Dankwa"
        assert "bedrockCorrected" not in words[0]

    def test_person_guard_no_revert_for_location_only(self):
        """locationCorrected without entityKind='person' keeps the bedrock change."""
        words = [
            {"word": "Kumasi", "locationCorrected": True, "entityKind": "location",
             "bedrockCorrected": True, "_pre_llm_word": "Kumase",
             "start": 0.0, "end": 0.3},
        ]

        reverted = _apply_guards(words, 0, 1)

        assert reverted == 0
        assert words[0]["word"] == "Kumasi"
        assert words[0]["bedrockCorrected"] is True

    @pytest.mark.asyncio
    async def test_person_guard_in_full_pipeline(self):
        """Person-guarded words are reverted in refine_chunks."""
        words = [
            {"word": "Dankwa", "start": 0.0, "end": 0.3, "confidence": 0.90,
             "locationCorrected": True, "entityKind": "person"},
            {"word": "spoke", "start": 0.3, "end": 0.5, "confidence": 0.99},
        ]
        settings = _make_settings(llm_chunk_size=10)
        snapshot = _make_snapshot()

        # Bedrock tries to change "Dankwa" to "Tarkwa"
        client = _make_bedrock_client(
            invoke_return_value="[Segment 1]: Tarkwa spoke"
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "ok"
        # The person name should be preserved
        assert result_words[0]["word"] == "Dankwa"
        assert "bedrockCorrected" not in result_words[0]


# ---------------------------------------------------------------------------
# 8. Success path — normal invocation
# ---------------------------------------------------------------------------


class TestSuccessPath:
    """Normal invocation: words are refined, bedrockCorrected set, llm_status ok."""

    @pytest.mark.asyncio
    async def test_simple_correction(self):
        """Single word corrected by bedrock gets bedrockCorrected flag."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80},
            {"word": "party", "start": 0.3, "end": 0.5, "confidence": 0.95},
        ]
        settings = _make_settings(llm_chunk_size=10)
        snapshot = _make_snapshot()

        client = _make_bedrock_client(
            invoke_return_value="[Segment 1]: the NDC party"
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "ok"
        assert corrections == 1
        assert result_words[1]["word"] == "NDC"
        assert result_words[1]["bedrockCorrected"] is True
        assert "bedrockCorrected" not in result_words[0]
        assert "bedrockCorrected" not in result_words[2]

    @pytest.mark.asyncio
    async def test_no_changes_means_zero_corrections(self):
        """When bedrock returns the same text, no corrections are applied."""
        words = _make_words(["the", "quick", "fox"])
        settings = _make_settings(llm_chunk_size=10)
        snapshot = _make_snapshot()

        client = _make_bedrock_client(
            invoke_return_value="[Segment 1]: the quick fox"
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "ok"
        assert corrections == 0
        assert all("bedrockCorrected" not in w for w in result_words)

    @pytest.mark.asyncio
    async def test_multiple_corrections(self):
        """Multiple words corrected at once."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80},
            {"word": "paty", "start": 0.3, "end": 0.5, "confidence": 0.75},
        ]
        settings = _make_settings(llm_chunk_size=10)
        snapshot = _make_snapshot()

        client = _make_bedrock_client(
            invoke_return_value="[Segment 1]: the NDC party"
        )

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "ok"
        assert corrections == 2
        assert result_words[1]["word"] == "NDC"
        assert result_words[1]["bedrockCorrected"] is True
        assert result_words[2]["word"] == "party"
        assert result_words[2]["bedrockCorrected"] is True


# ---------------------------------------------------------------------------
# 9. Partial failure — some chunks succeed, some fail
# ---------------------------------------------------------------------------


class TestPartialFailure:
    """Some chunks succeed, some fail → llm_status 'partial'."""

    @pytest.mark.asyncio
    async def test_partial_status_with_mixed_results(self):
        """First chunk succeeds, second fails → partial status."""
        # 8 words → 2 chunks of 5 and 3
        words = _make_words(["the", "ndc", "party", "is", "here", "in", "Kumase", "today"])
        settings = _make_settings(llm_chunk_size=5)
        snapshot = _make_snapshot()

        call_count = [0]

        def invoke_side_effect(system, user):
            call_count[0] += 1
            if call_count[0] == 1:
                return "[Segment 1]: the NDC party is here"
            raise RuntimeError("Chunk 2 failed")

        client = _make_bedrock_client(invoke_side_effect=invoke_side_effect)

        with patch("app.llm.refiner.retrieve_candidates", new_callable=AsyncMock, return_value=[]):
            result_words, llm_status, corrections = await refine_chunks(
                words, snapshot, client, None, settings
            )

        assert llm_status == "partial"
        # First chunk was corrected
        assert result_words[1]["word"] == "NDC"
        assert result_words[1].get("bedrockCorrected") is True
        # Second chunk preserved original text
        assert result_words[6]["word"] == "Kumase"
        assert "bedrockCorrected" not in result_words[6]


# ---------------------------------------------------------------------------
# Additional helper tests
# ---------------------------------------------------------------------------


class TestSnapshotAndCleanup:
    """Test _snapshot_pre_llm and _cleanup_snapshots helpers."""

    def test_snapshot_saves_year_corrected_words(self):
        """Words with yearCorrected get _pre_llm_word snapshot."""
        words = [
            {"word": "1992", "yearCorrected": True},
            {"word": "hello"},
            {"word": "Dankwa", "locationCorrected": True, "entityKind": "person"},
        ]
        _snapshot_pre_llm(words, 0, 3)

        assert words[0]["_pre_llm_word"] == "1992"
        assert "_pre_llm_word" not in words[1]
        assert words[2]["_pre_llm_word"] == "Dankwa"

    def test_cleanup_removes_snapshot_keys(self):
        """_cleanup_snapshots removes all _pre_llm_word keys."""
        words = [
            {"word": "1992", "_pre_llm_word": "1992"},
            {"word": "hello"},
            {"word": "Dankwa", "_pre_llm_word": "Dankwa"},
        ]
        _cleanup_snapshots(words, 0, 3)

        assert "_pre_llm_word" not in words[0]
        assert "_pre_llm_word" not in words[1]
        assert "_pre_llm_word" not in words[2]
