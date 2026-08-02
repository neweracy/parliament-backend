"""Tests for app/rag/retriever.py — LangChain-based hybrid retrieval."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.rag.retriever import (
    _RRF_K,
    HybridRetriever,
    RetrievalFilters,
    RRFRetriever,
    _build_filter_clauses,
)


class TestBuildFilterClauses:
    """Tests for _build_filter_clauses — SQL WHERE fragment builder."""

    def test_none_filters(self):
        """None filters produces empty clauses and params."""
        clauses, params = _build_filter_clauses(None)
        assert clauses == []
        assert params == {}

    def test_entity_filter(self):
        """Entity names filter produces array overlap clause."""
        filters = RetrievalFilters(entity_names=["budget", "tax"])
        clauses, params = _build_filter_clauses(filters)
        assert "tc.entity_names && :entity_filter" in clauses
        assert params["entity_filter"] == ["budget", "tax"]

    def test_speaker_filter(self):
        """Speaker filter produces equality clause."""
        filters = RetrievalFilters(speaker="Hon. Doe")
        clauses, params = _build_filter_clauses(filters)
        assert "tc.speaker = :speaker" in clauses
        assert params["speaker"] == "Hon. Doe"

    def test_date_from_only(self):
        """date_from alone produces a single date clause."""
        filters = RetrievalFilters(date_from=date(2024, 1, 1))
        clauses, params = _build_filter_clauses(filters)
        assert len(clauses) == 1
        assert params["date_from"] == "2024-01-01"

    def test_date_to_only(self):
        """date_to alone produces a single date clause."""
        filters = RetrievalFilters(date_to=date(2024, 6, 30))
        clauses, params = _build_filter_clauses(filters)
        assert len(clauses) == 1
        assert params["date_to"] == "2024-06-30"

    def test_date_range(self):
        """Both date_from and date_to produce two clauses."""
        filters = RetrievalFilters(date_from=date(2024, 1, 1), date_to=date(2024, 6, 30))
        clauses, params = _build_filter_clauses(filters)
        assert len(clauses) == 2
        assert params["date_from"] == "2024-01-01"
        assert params["date_to"] == "2024-06-30"

    def test_all_filters_combined(self):
        """All filters together produce multiple clauses."""
        filters = RetrievalFilters(
            entity_names=["education"],
            date_from=date(2024, 1, 1),
            date_to=date(2024, 12, 31),
            speaker="Minister Smith",
        )
        clauses, params = _build_filter_clauses(filters)
        assert len(clauses) == 4
        assert params["entity_filter"] == ["education"]
        assert params["date_from"] == "2024-01-01"
        assert params["date_to"] == "2024-12-31"
        assert params["speaker"] == "Minister Smith"

    def test_empty_entity_list_not_added(self):
        """Empty entity_names list does not generate a clause."""
        filters = RetrievalFilters(entity_names=[])
        clauses, params = _build_filter_clauses(filters)
        assert clauses == []
        assert params == {}


class TestRRFRetriever:
    """Tests for RRFRetriever — Reciprocal Rank Fusion logic."""

    def _make_doc(self, chunk_id: int, text: str = "text", score: float = 0.9):
        """Helper to build a Document with standard metadata."""
        return Document(
            page_content=text,
            metadata={
                "chunk_id": chunk_id,
                "score": score,
                "transcript_id": chunk_id * 10,
                "speaker": None,
                "start_s": None,
                "end_s": None,
                "entity_names": [],
            },
        )

    def test_fusion_disjoint(self):
        """Disjoint results — each chunk in only one arm gets equal RRF score."""
        fulltext = [self._make_doc(1)]
        vector = [self._make_doc(2)]

        fused = RRFRetriever._reciprocal_rank_fusion(fulltext, vector)
        assert len(fused) == 2
        # Both at rank 1 in their respective arm → same RRF score
        expected_score = 1.0 / (_RRF_K + 1)
        assert fused[0].metadata["score"] == pytest.approx(expected_score)
        assert fused[1].metadata["score"] == pytest.approx(expected_score)

    def test_fusion_overlap_ranks_higher(self):
        """Chunk appearing in both arms scores higher than single-arm chunks."""
        shared = self._make_doc(1, "Shared")
        unique = self._make_doc(2, "Unique")

        fulltext = [shared, unique]
        vector = [self._make_doc(1, "Shared")]

        fused = RRFRetriever._reciprocal_rank_fusion(fulltext, vector)
        # chunk_id=1 appears in both arms, should be ranked first
        assert fused[0].metadata["chunk_id"] == 1
        assert fused[0].metadata["score"] > fused[1].metadata["score"]

    def test_fusion_overlap_score_math(self):
        """Verify exact RRF math for overlapping chunk."""
        fulltext = [self._make_doc(1)]  # rank 1
        vector = [self._make_doc(1)]  # rank 1

        fused = RRFRetriever._reciprocal_rank_fusion(fulltext, vector)
        assert len(fused) == 1
        expected = 1.0 / (_RRF_K + 1) + 1.0 / (_RRF_K + 1)
        assert fused[0].metadata["score"] == pytest.approx(expected)

    def test_fusion_empty_arms(self):
        """Empty results from both arms yields empty list."""
        fused = RRFRetriever._reciprocal_rank_fusion([], [])
        assert fused == []

    def test_fusion_one_arm_empty(self):
        """One arm empty — other arm's results still returned."""
        fulltext = [self._make_doc(1), self._make_doc(2)]
        vector = []

        fused = RRFRetriever._reciprocal_rank_fusion(fulltext, vector)
        assert len(fused) == 2
        # First result has higher rank → higher score
        assert fused[0].metadata["chunk_id"] == 1
        assert fused[0].metadata["score"] > fused[1].metadata["score"]

    def test_fusion_preserves_ordering_by_score(self):
        """Multiple documents are ordered by descending fused score."""
        fulltext = [self._make_doc(1), self._make_doc(2), self._make_doc(3)]
        vector = [self._make_doc(3), self._make_doc(2)]

        fused = RRFRetriever._reciprocal_rank_fusion(fulltext, vector)
        scores = [doc.metadata["score"] for doc in fused]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_retrieve_calls_both_arms(self):
        """retrieve() calls both retrievers in parallel."""
        fulltext = AsyncMock()
        fulltext.ainvoke = AsyncMock(return_value=[])
        vector = AsyncMock()
        vector.ainvoke = AsyncMock(return_value=[])

        rrf = RRFRetriever(fulltext, vector)
        result = await rrf.retrieve("test query")

        fulltext.ainvoke.assert_called_once_with("test query")
        vector.ainvoke.assert_called_once_with("test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_retrieve_respects_limit(self):
        """retrieve() caps returned results at the given limit."""
        docs = [self._make_doc(i) for i in range(20)]
        fulltext = AsyncMock()
        fulltext.ainvoke = AsyncMock(return_value=docs)
        vector = AsyncMock()
        vector.ainvoke = AsyncMock(return_value=[])

        rrf = RRFRetriever(fulltext, vector)
        result = await rrf.retrieve("test query", limit=5)

        assert len(result) == 5


class TestHybridRetrieverDocsToChunks:
    """Tests for HybridRetriever._docs_to_chunks — Document→RetrievedChunk."""

    def test_converts_documents_to_chunks(self, sample_documents):
        """_docs_to_chunks converts Documents to RetrievedChunk objects."""
        chunks = HybridRetriever._docs_to_chunks(sample_documents)
        assert len(chunks) == 3
        assert chunks[0].chunk_id == 1
        assert chunks[0].text == "The budget was discussed in parliament."
        assert chunks[0].relevance_score == 0.85
        assert chunks[0].speaker == "Hon. Doe"
        assert chunks[0].transcript_id == 100
        assert chunks[0].start_s == 10.0
        assert chunks[0].end_s == 25.0
        assert chunks[0].matched_entities == ["budget", "parliament"]

    def test_handles_missing_optional_metadata(self):
        """Handles documents with None in optional metadata fields."""
        doc = Document(
            page_content="Some text",
            metadata={
                "chunk_id": 99,
                "transcript_id": 200,
                "score": 0.5,
                "speaker": None,
                "start_s": None,
                "end_s": None,
                "entity_names": [],
            },
        )
        chunks = HybridRetriever._docs_to_chunks([doc])
        assert len(chunks) == 1
        assert chunks[0].chunk_id == 99
        assert chunks[0].speaker is None
        assert chunks[0].start_s is None
        assert chunks[0].end_s is None
        assert chunks[0].matched_entities == []

    def test_empty_document_list(self):
        """Empty document list returns empty chunk list."""
        chunks = HybridRetriever._docs_to_chunks([])
        assert chunks == []


class TestHybridRetrieverEnrichWithMetadata:
    """Tests for HybridRetriever._enrich_with_metadata.

    The enrichment join already reaches `hansard_record` and `sitting`, so it is
    the single place where a chunk learns its real navigation target. A chunk
    that comes back without `sitting_id`/`record_id` leaves the frontend with
    nothing but `transcript_id`, which addresses a different identifier space.
    """

    @staticmethod
    def _retriever_with_rows(rows: list[tuple]) -> tuple[HybridRetriever, dict]:
        """Build a HybridRetriever whose session returns `rows`, capturing params."""
        captured: dict = {}

        class _Result:
            def fetchall(self):
                return rows

        class _Session:
            async def execute(self, statement, params=None):
                captured["sql"] = str(statement)
                captured["params"] = params
                return _Result()

        class _Factory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *exc):
                return None

        return HybridRetriever(_Factory(), embeddings=None), captured

    @pytest.mark.asyncio
    async def test_populates_sitting_id_and_record_id(self):
        """Enrichment sets sitting_id and record_id alongside the titles and date."""
        chunks = HybridRetriever._docs_to_chunks(
            [
                Document(
                    page_content="Budget text",
                    metadata={
                        "chunk_id": 11,
                        "transcript_id": 2,
                        "score": 0.9,
                        "speaker": "Hon. Doe",
                        "start_s": 1.0,
                        "end_s": 2.0,
                        "entity_names": [],
                    },
                )
            ]
        )
        # transcript 2 belongs to record 1 in sitting 1 — the exact shape that
        # made the old "transcript_id as recordId" behaviour fail.
        retriever, _ = self._retriever_with_rows(
            [(11, "Budget Debate 2026", "First Sitting", "2026-02-01", 1, 1)]
        )

        enriched = await retriever._enrich_with_metadata(chunks)

        assert enriched[0].record_id == 1
        assert enriched[0].sitting_id == 1
        assert enriched[0].transcript_id == 2
        assert enriched[0].record_title == "Budget Debate 2026"
        assert enriched[0].sitting_title == "First Sitting"
        assert enriched[0].date == "2026-02-01"

    @pytest.mark.asyncio
    async def test_uses_a_single_query(self):
        """Enrichment issues one query that already joins record and sitting."""
        chunks = HybridRetriever._docs_to_chunks(
            [
                Document(
                    page_content="text",
                    metadata={
                        "chunk_id": 5,
                        "transcript_id": 7,
                        "score": 0.5,
                        "speaker": None,
                        "start_s": None,
                        "end_s": None,
                        "entity_names": [],
                    },
                )
            ]
        )
        retriever, captured = self._retriever_with_rows([(5, "R", "S", "2026-01-01", 3, 4)])

        await retriever._enrich_with_metadata(chunks)

        assert captured["params"] == {"chunk_ids": [5]}
        assert "hr.id" in captured["sql"]
        assert "s.id" in captured["sql"]

    @pytest.mark.asyncio
    async def test_unmatched_chunk_gets_none_ids(self):
        """A chunk with no metadata row keeps None ids rather than a stale value."""
        chunks = HybridRetriever._docs_to_chunks(
            [
                Document(
                    page_content="text",
                    metadata={
                        "chunk_id": 99,
                        "transcript_id": 7,
                        "score": 0.5,
                        "speaker": None,
                        "start_s": None,
                        "end_s": None,
                        "entity_names": [],
                    },
                )
            ]
        )
        retriever, _ = self._retriever_with_rows([])

        enriched = await retriever._enrich_with_metadata(chunks)

        assert enriched[0].sitting_id is None
        assert enriched[0].record_id is None
