"""Tests for app/rag/answerer.py — GroundedAnsweringChain."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.rag.answerer import GroundedAnsweringChain, _GENERATION_FAILURE_TEXT
from app.rag.recommendations import RecommendationItem


class TestParseCitations:
    def test_extracts_citations(self):
        text = "Budget was discussed [1] and education [2] also [1]."
        ids = GroundedAnsweringChain._parse_citations(text)
        assert ids == [1, 2]  # deduplicated, order of first appearance

    def test_no_citations(self):
        ids = GroundedAnsweringChain._parse_citations("No citations here.")
        assert ids == []

    def test_multiple_same_chunk(self):
        ids = GroundedAnsweringChain._parse_citations("[5][5][5]")
        assert ids == [5]


class TestStripCitationMarkers:
    def test_strips_markers(self):
        text = "The budget [42] was discussed."
        result = GroundedAnsweringChain._strip_citation_markers(text)
        assert "[42]" not in result
        assert "budget" in result

    def test_cleans_whitespace(self):
        text = "text [1] , more"
        result = GroundedAnsweringChain._strip_citation_markers(text)
        assert "  " not in result


class TestSplitRecommendations:
    def test_splits_correctly(self):
        raw = "Answer text.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        answer, recs = GroundedAnsweringChain._split_recommendations(raw)
        assert answer == "Answer text."
        assert len(recs) == 3
        assert recs[0].text == "Q1"
        assert recs[0].reason == "R1"

    def test_no_recommendations_section(self):
        raw = "Just an answer with no recommendations."
        answer, recs = GroundedAnsweringChain._split_recommendations(raw)
        assert answer == raw
        assert recs == []

    def test_limits_to_three(self):
        raw = "Answer.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3\n- Q4 | R4"
        answer, recs = GroundedAnsweringChain._split_recommendations(raw)
        assert len(recs) == 3


class TestBuildMessages:
    def test_grounded_messages(self, sample_retrieved_chunks):
        messages = GroundedAnsweringChain._build_messages(
            "What about the budget?", sample_retrieved_chunks, grounded=True
        )
        assert len(messages) == 2
        assert isinstance(messages[0], SystemMessage)
        assert isinstance(messages[1], HumanMessage)
        assert "Chunk ID: 1" in messages[1].content
        assert "budget" in messages[1].content.lower()

    def test_ungrounded_messages(self):
        messages = GroundedAnsweringChain._build_messages(
            "Hello!", [], grounded=False
        )
        assert "NO SOURCE CHUNKS AVAILABLE" in messages[1].content


class TestAnswer:
    @pytest.mark.asyncio
    async def test_grounded_answer(self, mock_chat_model, mock_settings, sample_retrieved_chunks):
        chain = GroundedAnsweringChain(mock_chat_model, mock_settings)

        # Patch builder methods to avoid Document vs RetrievedChunk mismatch
        chain._builder.suggestions = MagicMock(return_value=[])
        chain._builder.related_records = MagicMock(return_value=[])

        response = await chain.answer("What was discussed?", sample_retrieved_chunks)

        assert response.answer  # non-empty
        assert response.latency_ms > 0
        assert len(response.recommendations) == 3
        mock_chat_model.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_path(self, mock_settings, sample_retrieved_chunks):
        failing_model = AsyncMock()
        failing_model.ainvoke = AsyncMock(side_effect=Exception("Bedrock timeout"))

        chain = GroundedAnsweringChain(failing_model, mock_settings)

        # Patch builder to return fallback suggestions (simulating top-up)
        chain._builder.suggestions = MagicMock(
            return_value=[
                RecommendationItem(text="Q1", reason="R1"),
                RecommendationItem(text="Q2", reason="R2"),
                RecommendationItem(text="Q3", reason="R3"),
            ]
        )
        chain._builder.related_records = MagicMock(return_value=[])

        response = await chain.answer("What?", sample_retrieved_chunks)

        assert response.answer == _GENERATION_FAILURE_TEXT
        assert len(response.recommendations) == 3  # builder tops up
