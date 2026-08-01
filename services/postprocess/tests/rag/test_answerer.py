"""Tests for app/rag/answerer.py — GroundedAnsweringChain."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.rag.answerer import GroundedAnsweringChain
from app.rag.parsing import (
    GENERATION_FAILURE_TEXT,
    parse_citations,
    split_recommendations,
    strip_citation_markers,
)


class TestParseCitations:
    def test_extracts_citations(self):
        text = "Budget was discussed [1] and education [2] also [1]."
        ids = parse_citations(text)
        assert ids == [1, 2]  # deduplicated, order of first appearance

    def test_no_citations(self):
        ids = parse_citations("No citations here.")
        assert ids == []

    def test_multiple_same_chunk(self):
        ids = parse_citations("[5][5][5]")
        assert ids == [5]


class TestStripCitationMarkers:
    def test_strips_markers(self):
        text = "The budget [42] was discussed."
        result = strip_citation_markers(text)
        assert "[42]" not in result
        assert "budget" in result

    def test_cleans_whitespace(self):
        text = "text [1] , more"
        result = strip_citation_markers(text)
        assert "  " not in result


class TestSplitRecommendations:
    def test_splits_correctly(self):
        raw = "Answer text.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        answer, recs = split_recommendations(raw)
        assert answer == "Answer text."
        assert len(recs) == 3
        assert recs[0].text == "Q1"
        assert recs[0].reason == "R1"

    def test_no_recommendations_section(self):
        raw = "Just an answer with no recommendations."
        answer, recs = split_recommendations(raw)
        assert answer == raw
        assert recs == []

    def test_limits_to_three(self):
        raw = "Answer.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3\n- Q4 | R4"
        answer, recs = split_recommendations(raw)
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
        messages = GroundedAnsweringChain._build_messages("Hello!", [], grounded=False)
        assert "NO SOURCE CHUNKS AVAILABLE" in messages[1].content


class TestAnswer:
    @pytest.mark.asyncio
    async def test_grounded_answer(self, mock_chat_model, mock_settings, sample_retrieved_chunks):
        chain = GroundedAnsweringChain(mock_chat_model, mock_settings)

        response = await chain.answer("What was discussed?", sample_retrieved_chunks)

        assert response.answer  # non-empty
        assert response.latency_ms > 0
        # All 3 come from parsing the model stub's RECOMMENDATIONS block
        assert len(response.recommendations) == 3
        # 2 chunks across 2 distinct transcript_ids, answer is grounded
        assert len(response.related_records) >= 1
        mock_chat_model.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_path(self, mock_settings, sample_retrieved_chunks):
        failing_model = AsyncMock()
        failing_model.ainvoke = AsyncMock(side_effect=Exception("Bedrock timeout"))

        chain = GroundedAnsweringChain(failing_model, mock_settings)

        response = await chain.answer("What?", sample_retrieved_chunks)

        assert response.answer == GENERATION_FAILURE_TEXT
        # Real builder tops up from chunk metadata → static set
        assert len(response.recommendations) == 3
