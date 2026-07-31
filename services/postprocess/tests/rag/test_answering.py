"""Unit tests for the GroundedAnswerer class.

Tests core logic: relevance filtering, prompt building, citation parsing,
and citation validation — all without mocking external services for the
pure functions, and with minimal mocking for the async answer() flow.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.rag.answering import (
    AnswerResponse,
    Citation,
    GroundedAnswerer,
    _DEFAULT_RELEVANCE_THRESHOLD,
)
from app.rag.retrieval import RetrievedChunk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_chunk(
    chunk_id: int = 1,
    text: str = "The budget was discussed at length.",
    relevance_score: float = 0.05,
    transcript_id: int = 100,
    speaker: str | None = "Hon. Smith",
    start_s: float | None = 10.0,
    end_s: float | None = 25.0,
) -> RetrievedChunk:
    """Create a RetrievedChunk for testing."""
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        relevance_score=relevance_score,
        transcript_id=transcript_id,
        speaker=speaker,
        start_s=start_s,
        end_s=end_s,
        matched_entities=["Budget"],
    )


def _make_answerer(
    relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
    max_context_chunks: int = 10,
) -> GroundedAnswerer:
    """Create a GroundedAnswerer with a mock BedrockClient."""
    mock_bedrock = MagicMock()
    mock_bedrock.is_configured = True
    mock_bedrock.model_id = "anthropic.claude-3-sonnet-20240229-v1:0"

    mock_settings = MagicMock()
    mock_settings.aws_region = "us-east-1"

    return GroundedAnswerer(
        bedrock_client=mock_bedrock,
        settings=mock_settings,
        relevance_threshold=relevance_threshold,
        max_context_chunks=max_context_chunks,
    )


# ---------------------------------------------------------------------------
# Tests: Relevance threshold filtering
# ---------------------------------------------------------------------------


class TestRelevanceFiltering:
    """Tests for relevance threshold filtering behavior."""

    def test_no_chunks_returns_no_evidence(self) -> None:
        """Empty chunk list returns 'no supporting evidence' response."""
        answerer = _make_answerer()
        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=[])
        )
        assert "No supporting evidence" in result.answer
        assert result.citations == []
        assert result.source_chunks == []
        assert result.latency_ms > 0

    def test_chunks_below_threshold_returns_no_evidence(self) -> None:
        """All chunks below threshold returns 'no supporting evidence'."""
        answerer = _make_answerer(relevance_threshold=0.5)
        chunks = [
            _make_chunk(chunk_id=1, relevance_score=0.01),
            _make_chunk(chunk_id=2, relevance_score=0.1),
            _make_chunk(chunk_id=3, relevance_score=0.49),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )
        assert "No supporting evidence" in result.answer
        assert result.citations == []
        assert result.source_chunks == []

    def test_some_chunks_above_threshold_passes_only_relevant(self) -> None:
        """Only chunks above threshold are used as context."""
        answerer = _make_answerer(relevance_threshold=0.05)
        # Mock invoke to return a simple answer
        answerer._bedrock_client.invoke.return_value = (
            "The budget was discussed. [1]"
        )

        chunks = [
            _make_chunk(chunk_id=1, relevance_score=0.1),  # above
            _make_chunk(chunk_id=2, relevance_score=0.001),  # below
            _make_chunk(chunk_id=3, relevance_score=0.06),  # above
        ]
        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )
        # source_chunks should only include chunks above threshold
        assert len(result.source_chunks) == 2
        source_ids = {c.chunk_id for c in result.source_chunks}
        assert source_ids == {1, 3}


# ---------------------------------------------------------------------------
# Tests: Prompt building
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    """Tests for the prompt construction logic."""

    def test_prompt_includes_chunk_ids(self) -> None:
        """Prompt includes chunk IDs in the context section."""
        chunks = [
            _make_chunk(chunk_id=42, text="Budget allocation increased."),
            _make_chunk(chunk_id=15, text="The Speaker called for order."),
        ]
        prompt = GroundedAnswerer._build_user_prompt("What about the budget?", chunks)

        assert "[Chunk ID: 42]" in prompt
        assert "[Chunk ID: 15]" in prompt

    def test_prompt_includes_speaker_labels(self) -> None:
        """Prompt includes speaker metadata for each chunk."""
        chunks = [
            _make_chunk(chunk_id=1, speaker="Hon. Smith"),
            _make_chunk(chunk_id=2, speaker=None),
        ]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "Speaker: Hon. Smith" in prompt
        assert "Speaker: Unknown Speaker" in prompt

    def test_prompt_includes_timestamps(self) -> None:
        """Prompt includes start/end timestamps for each chunk."""
        chunks = [
            _make_chunk(chunk_id=1, start_s=10.5, end_s=25.3),
            _make_chunk(chunk_id=2, start_s=None, end_s=None),
        ]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "10.5s" in prompt
        assert "25.3s" in prompt
        assert "N/A" in prompt

    def test_prompt_includes_chunk_text(self) -> None:
        """Prompt includes the full text of each chunk."""
        chunks = [
            _make_chunk(chunk_id=1, text="The fiscal policy was debated."),
        ]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "The fiscal policy was debated." in prompt

    def test_prompt_includes_question(self) -> None:
        """Prompt includes the user's question."""
        chunks = [_make_chunk(chunk_id=1)]
        prompt = GroundedAnswerer._build_user_prompt(
            "What was the budget allocation?", chunks
        )

        assert "What was the budget allocation?" in prompt

    def test_prompt_includes_citation_instruction(self) -> None:
        """Prompt instructs the model to cite using [chunk_id] notation."""
        chunks = [_make_chunk(chunk_id=1)]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "[chunk_id]" in prompt


# ---------------------------------------------------------------------------
# Tests: Citation parsing
# ---------------------------------------------------------------------------


class TestCitationParsing:
    """Tests for extracting citation markers from answer text."""

    def test_single_citation(self) -> None:
        """Parses a single citation marker."""
        ids = GroundedAnswerer._parse_citations("The budget increased [42].")
        assert ids == [42]

    def test_multiple_citations(self) -> None:
        """Parses multiple citation markers."""
        ids = GroundedAnswerer._parse_citations(
            "Budget increased [42]. Spending rose [15]. See also [42][7]."
        )
        # Deduplicated, first-appearance order
        assert ids == [42, 15, 7]

    def test_no_citations(self) -> None:
        """Returns empty list when no citations are present."""
        ids = GroundedAnswerer._parse_citations("No citations here.")
        assert ids == []

    def test_non_numeric_brackets_ignored(self) -> None:
        """Non-numeric bracket content is ignored."""
        ids = GroundedAnswerer._parse_citations(
            "See [source] and [chunk_id] but also [123]."
        )
        assert ids == [123]

    def test_adjacent_citations(self) -> None:
        """Adjacent citations are parsed correctly."""
        ids = GroundedAnswerer._parse_citations("Statement [1][2][3].")
        assert ids == [1, 2, 3]

    def test_large_chunk_ids(self) -> None:
        """Large chunk IDs are parsed correctly."""
        ids = GroundedAnswerer._parse_citations("Evidence [999999].")
        assert ids == [999999]


# ---------------------------------------------------------------------------
# Tests: Citation validation
# ---------------------------------------------------------------------------


class TestCitationValidation:
    """Tests for validating citations against source chunks."""

    def test_valid_citation_produces_citation_object(self) -> None:
        """Valid chunk_id produces a Citation with correct fields."""
        chunk = _make_chunk(
            chunk_id=42,
            transcript_id=100,
            speaker="Hon. Smith",
            start_s=10.0,
            end_s=25.0,
            text="The budget was discussed thoroughly in committee.",
        )
        chunk_map = {42: chunk}

        citations = GroundedAnswerer._validate_citations([42], chunk_map)

        assert len(citations) == 1
        c = citations[0]
        assert c.chunk_id == 42
        assert c.transcript_id == 100
        assert c.speaker == "Hon. Smith"
        assert c.start_s == 10.0
        assert c.end_s == 25.0
        assert "The budget was discussed" in c.excerpt

    def test_invalid_citation_is_dropped(self) -> None:
        """Citation referencing non-existent chunk is dropped."""
        chunk = _make_chunk(chunk_id=42)
        chunk_map = {42: chunk}

        citations = GroundedAnswerer._validate_citations([42, 999], chunk_map)

        assert len(citations) == 1
        assert citations[0].chunk_id == 42

    def test_all_invalid_returns_empty(self) -> None:
        """All invalid citations return empty list."""
        chunk_map = {42: _make_chunk(chunk_id=42)}

        citations = GroundedAnswerer._validate_citations([100, 200], chunk_map)

        assert citations == []

    def test_excerpt_truncated_for_long_text(self) -> None:
        """Excerpt is truncated to 150 chars with ellipsis for long text."""
        long_text = "A" * 200
        chunk = _make_chunk(chunk_id=1, text=long_text)
        chunk_map = {1: chunk}

        citations = GroundedAnswerer._validate_citations([1], chunk_map)

        assert len(citations[0].excerpt) == 153  # 150 + "..."
        assert citations[0].excerpt.endswith("...")

    def test_excerpt_not_truncated_for_short_text(self) -> None:
        """Short text is not truncated."""
        short_text = "Short."
        chunk = _make_chunk(chunk_id=1, text=short_text)
        chunk_map = {1: chunk}

        citations = GroundedAnswerer._validate_citations([1], chunk_map)

        assert citations[0].excerpt == "Short."


# ---------------------------------------------------------------------------
# Tests: Full answer() flow
# ---------------------------------------------------------------------------


class TestAnswerFlow:
    """Integration tests for the full answer() flow with mocked Bedrock."""

    def test_successful_answer_with_citations(self) -> None:
        """Full flow: relevant chunks → Claude response → parsed citations."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.return_value = (
            "The budget allocation increased by 15% [1]. "
            "The education sector received the most [2]."
        )

        chunks = [
            _make_chunk(chunk_id=1, relevance_score=0.1, text="Budget increased."),
            _make_chunk(chunk_id=2, relevance_score=0.08, text="Education funding."),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )

        assert "budget allocation increased" in result.answer
        assert len(result.citations) == 2
        assert result.citations[0].chunk_id == 1
        assert result.citations[1].chunk_id == 2
        assert len(result.source_chunks) == 2
        assert result.latency_ms > 0

    def test_claude_failure_returns_error_message(self) -> None:
        """Claude invocation failure returns a user-friendly error."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.side_effect = RuntimeError("Bedrock unavailable")

        chunks = [_make_chunk(chunk_id=1, relevance_score=0.1)]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )

        assert "Unable to generate an answer" in result.answer
        assert result.citations == []
        assert len(result.source_chunks) == 1  # chunks still returned
        assert result.latency_ms > 0

    def test_max_context_chunks_limit(self) -> None:
        """Only top max_context_chunks chunks are used."""
        answerer = _make_answerer(max_context_chunks=2)
        answerer._bedrock_client.invoke.return_value = "Answer [1]."

        chunks = [
            _make_chunk(chunk_id=i, relevance_score=0.1 - i * 0.01)
            for i in range(1, 6)
        ]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("Question?", chunks=chunks)
        )

        # Only 2 chunks should be in source_chunks
        assert len(result.source_chunks) == 2

    def test_answer_response_dataclass_fields(self) -> None:
        """AnswerResponse has all required fields."""
        resp = AnswerResponse(
            answer="Test answer",
            citations=[
                Citation(
                    transcript_id=1,
                    chunk_id=42,
                    speaker="Hon. X",
                    start_s=0.0,
                    end_s=10.0,
                    excerpt="Test",
                )
            ],
            source_chunks=[_make_chunk(chunk_id=42)],
            latency_ms=123.4,
        )

        assert resp.answer == "Test answer"
        assert len(resp.citations) == 1
        assert resp.citations[0].chunk_id == 42
        assert len(resp.source_chunks) == 1
        assert resp.latency_ms == 123.4
