"""LangChain-based hybrid retrieval — fulltext + vector retrievers.

Provides LangChain BaseRetriever subclasses wrapping the existing PostgreSQL
tsvector full-text search and pgvector cosine similarity search. Both
retrievers return LangChain Document objects with rich metadata for downstream
fusion via RRF.

The existing database schema (transcript_chunk, transcript, hansard_record,
sitting) is used directly via raw SQL — no migration required.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field
from sqlalchemy import text

logger = structlog.get_logger("rag.retriever")

# Reciprocal Rank Fusion constant
_RRF_K = 60

# Default and maximum retrieval limits
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50

# Number of candidates to fetch from each search arm before fusion
_CANDIDATE_POOL_SIZE = 100


# ---------------------------------------------------------------------------
# Shared dataclasses (public interface, preserved from retrieval.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalFilters:
    """Pre-filters applied before ranking.

    Attributes:
        entity_names: If provided, only chunks whose entity_names array
            overlaps with at least one of these values are returned.
        date_from: If provided, only chunks from transcripts associated
            with sittings on or after this date.
        date_to: If provided, only chunks from transcripts associated
            with sittings on or before this date.
        speaker: If provided, only chunks attributed to this speaker.
    """

    entity_names: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    speaker: str | None = None


@dataclass
class RetrievedChunk:
    """A single retrieval result with metadata.

    Contains all fields needed by the search endpoint response and
    the grounded answering engine.
    """

    chunk_id: int
    text: str
    relevance_score: float
    transcript_id: int
    speaker: str | None
    start_s: float | None
    end_s: float | None
    matched_entities: list[str] = field(default_factory=list)
    record_title: str | None = None
    sitting_title: str | None = None
    date: str | None = None


# ---------------------------------------------------------------------------
# Filter clause builder (shared by both retrievers)
# ---------------------------------------------------------------------------


def _build_filter_clauses(
    filters: RetrievalFilters | None,
) -> tuple[list[str], dict[str, Any]]:
    """Build SQL WHERE clauses and params from RetrievalFilters.

    Returns a tuple of (list of SQL clause strings, params dict).
    Clauses use named parameters compatible with sqlalchemy text().
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters is None:
        return clauses, params

    if filters.entity_names:
        clauses.append("tc.entity_names && :entity_filter")
        params["entity_filter"] = filters.entity_names

    if filters.date_from:
        clauses.append("s.date_from >= :date_from")
        params["date_from"] = filters.date_from.isoformat()

    if filters.date_to:
        clauses.append("s.date_to <= :date_to")
        params["date_to"] = filters.date_to.isoformat()

    if filters.speaker:
        clauses.append("tc.speaker = :speaker")
        params["speaker"] = filters.speaker

    return clauses, params


# ---------------------------------------------------------------------------
# FulltextRetriever — wraps PostgreSQL tsvector search
# ---------------------------------------------------------------------------


class FulltextRetriever(BaseRetriever):
    """LangChain retriever wrapping PostgreSQL full-text search (ts_rank on tsvector).

    Uses the GIN index on the tsv column. Returns candidate chunks
    ranked by ts_rank score as LangChain Document objects.
    """

    session_factory: Any = Field(exclude=True)
    filters: RetrievalFilters | None = Field(default=None, exclude=True)
    pool_size: int = Field(default=_CANDIDATE_POOL_SIZE, exclude=True)

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """Sync retrieval — not supported (async-only service)."""
        raise NotImplementedError(
            "FulltextRetriever is async-only. Use _aget_relevant_documents."
        )

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Any = None,
    ) -> list[Document]:
        """Execute full-text search and return Documents with metadata.

        Uses ts_rank on the tsvector column with plainto_tsquery matching.
        Applies any configured RetrievalFilters as additional WHERE clauses.
        """
        where_clauses, params = _build_filter_clauses(self.filters)
        params["query"] = query
        params["pool_size"] = self.pool_size

        filter_sql = ""
        if where_clauses:
            filter_sql = " AND " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                tc.id AS chunk_id,
                tc.text,
                tc.transcript_id,
                tc.speaker,
                tc.start_s,
                tc.end_s,
                tc.entity_names,
                ts_rank(tc.tsv, plainto_tsquery('english', :query)) AS score
            FROM transcript_chunk tc
            JOIN transcript t ON t.id = tc.transcript_id
            JOIN hansard_record hr ON hr.id = t.record_id
            JOIN sitting s ON s.id = hr.sitting_id
            WHERE tc.tsv @@ plainto_tsquery('english', :query)
            {filter_sql}
            ORDER BY score DESC
            LIMIT :pool_size
        """

        try:
            async with self.session_factory() as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()

                documents: list[Document] = []
                for row in rows:
                    doc = Document(
                        page_content=row[1],
                        metadata={
                            "chunk_id": row[0],
                            "transcript_id": row[2],
                            "speaker": row[3],
                            "start_s": row[4],
                            "end_s": row[5],
                            "entity_names": row[6] or [],
                            "score": float(row[7]),
                        },
                    )
                    documents.append(doc)

                logger.debug(
                    "rag.retriever.fulltext_complete",
                    query_preview=query[:80],
                    results=len(documents),
                )
                return documents

        except Exception:
            logger.error("rag.retriever.fulltext_search_failed", exc_info=True)
            return []


# ---------------------------------------------------------------------------
# VectorRetriever — wraps pgvector cosine similarity search
# ---------------------------------------------------------------------------


class VectorRetriever(BaseRetriever):
    """LangChain retriever wrapping pgvector cosine similarity search.

    Uses the HNSW index on the embedding column. Embeds the query using
    BedrockEmbeddings, then executes cosine distance SQL to find nearest
    neighbours. Returns candidate chunks ranked by cosine similarity score.
    """

    session_factory: Any = Field(exclude=True)
    embeddings: Any = Field(exclude=True)
    filters: RetrievalFilters | None = Field(default=None, exclude=True)
    pool_size: int = Field(default=_CANDIDATE_POOL_SIZE, exclude=True)

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """Sync retrieval — not supported (async-only service)."""
        raise NotImplementedError(
            "VectorRetriever is async-only. Use _aget_relevant_documents."
        )

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Any = None,
    ) -> list[Document]:
        """Embed the query and execute cosine similarity search.

        Calls BedrockEmbeddings.aembed_query() to generate the query vector,
        then executes cosine distance SQL against the transcript_chunk table.
        Applies any configured RetrievalFilters as additional WHERE clauses.
        """
        # Step 1: Embed the query
        try:
            query_embedding = await self.embeddings.aembed_query(query)
        except Exception:
            logger.error(
                "rag.retriever.embedding_failed",
                query_preview=query[:80],
                exc_info=True,
            )
            return []

        # Step 2: Build and execute cosine similarity SQL
        where_clauses, params = _build_filter_clauses(self.filters)
        params["query_embedding"] = str(query_embedding)
        params["pool_size"] = self.pool_size

        filter_sql = ""
        if where_clauses:
            filter_sql = " AND " + " AND ".join(where_clauses)

        sql = f"""
            SELECT
                tc.id AS chunk_id,
                tc.text,
                tc.transcript_id,
                tc.speaker,
                tc.start_s,
                tc.end_s,
                tc.entity_names,
                1 - (tc.embedding <=> :query_embedding::vector) AS score
            FROM transcript_chunk tc
            JOIN transcript t ON t.id = tc.transcript_id
            JOIN hansard_record hr ON hr.id = t.record_id
            JOIN sitting s ON s.id = hr.sitting_id
            WHERE tc.embedding IS NOT NULL
            {filter_sql}
            ORDER BY tc.embedding <=> :query_embedding::vector
            LIMIT :pool_size
        """

        try:
            async with self.session_factory() as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()

                documents: list[Document] = []
                for row in rows:
                    doc = Document(
                        page_content=row[1],
                        metadata={
                            "chunk_id": row[0],
                            "transcript_id": row[2],
                            "speaker": row[3],
                            "start_s": row[4],
                            "end_s": row[5],
                            "entity_names": row[6] or [],
                            "score": float(row[7]),
                        },
                    )
                    documents.append(doc)

                logger.debug(
                    "rag.retriever.vector_complete",
                    query_preview=query[:80],
                    results=len(documents),
                )
                return documents

        except Exception:
            logger.error("rag.retriever.vector_search_failed", exc_info=True)
            return []


# ---------------------------------------------------------------------------
# RRFRetriever — fuses fulltext + vector arms with Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


class RRFRetriever:
    """Fuses FulltextRetriever and VectorRetriever results using Reciprocal Rank Fusion.

    Calls both retrievers in parallel via asyncio.gather, then combines
    their ranked lists using RRF scoring: score = sum(1 / (k + rank)) per
    document across both lists (k=60). Documents are identified by chunk_id.

    This is NOT a LangChain BaseRetriever subclass — it orchestrates the two
    underlying retrievers and returns fused Documents sorted by descending
    fused score.

    Requirements: 3.2, 3.4, 3.5
    """

    def __init__(
        self,
        fulltext_retriever: FulltextRetriever,
        vector_retriever: VectorRetriever,
    ) -> None:
        self._fulltext_retriever = fulltext_retriever
        self._vector_retriever = vector_retriever

    async def retrieve(self, query: str, limit: int = _DEFAULT_LIMIT) -> list[Document]:
        """Retrieve and fuse documents from both search arms.

        Args:
            query: The search query string.
            limit: Maximum number of documents to return (capped at _MAX_LIMIT).

        Returns:
            List of Documents sorted by descending fused RRF score, capped at limit.
        """
        limit = min(limit, _MAX_LIMIT)

        # Call both retrievers in parallel
        fulltext_results, vector_results = await asyncio.gather(
            self._fulltext_retriever.ainvoke(query),
            self._vector_retriever.ainvoke(query),
        )

        # Fuse results using Reciprocal Rank Fusion
        fused = self._reciprocal_rank_fusion(fulltext_results, vector_results)

        logger.debug(
            "rag.retriever.rrf_complete",
            query_preview=query[:80],
            fulltext_count=len(fulltext_results),
            vector_count=len(vector_results),
            fused_count=len(fused),
            returned=min(len(fused), limit),
        )

        return fused[:limit]

    @staticmethod
    def _reciprocal_rank_fusion(
        fulltext_results: list[Document],
        vector_results: list[Document],
    ) -> list[Document]:
        """Combine ranked lists from both arms using RRF (k=60).

        For each document appearing in either list:
            fused_score = sum(1 / (k + rank))
        where rank is 1-based position in the respective list.

        Documents are identified by metadata["chunk_id"]. The fused score
        is stored in metadata["score"], overwriting the per-arm score.
        """
        chunk_map: dict[int, tuple[Document, float]] = {}

        for rank, doc in enumerate(fulltext_results, start=1):
            chunk_id = doc.metadata["chunk_id"]
            rrf_score = 1.0 / (_RRF_K + rank)
            if chunk_id in chunk_map:
                existing_doc, existing_score = chunk_map[chunk_id]
                chunk_map[chunk_id] = (existing_doc, existing_score + rrf_score)
            else:
                chunk_map[chunk_id] = (doc, rrf_score)

        for rank, doc in enumerate(vector_results, start=1):
            chunk_id = doc.metadata["chunk_id"]
            rrf_score = 1.0 / (_RRF_K + rank)
            if chunk_id in chunk_map:
                existing_doc, existing_score = chunk_map[chunk_id]
                chunk_map[chunk_id] = (existing_doc, existing_score + rrf_score)
            else:
                chunk_map[chunk_id] = (doc, rrf_score)

        # Sort by fused score descending
        sorted_items = sorted(chunk_map.values(), key=lambda x: x[1], reverse=True)

        # Set the fused score in metadata and return Documents
        results: list[Document] = []
        for doc, fused_score in sorted_items:
            doc.metadata["score"] = fused_score
            results.append(doc)

        return results


# ---------------------------------------------------------------------------
# HybridRetriever — public facade for the router
# ---------------------------------------------------------------------------


class HybridRetriever:
    """Public facade that orchestrates fulltext + vector + RRF retrieval.

    This is the main entry point called by router.py. It creates both arm
    retrievers, applies filters, fuses results via RRF, converts Documents
    back to RetrievedChunk instances, and enriches them with metadata from
    the hansard_record and sitting tables.

    Requirements: 3.6, 3.7
    """

    def __init__(
        self,
        session_factory: Any,
        embeddings: Any,
        settings: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._embeddings = embeddings
        self._settings = settings

    async def retrieve(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[RetrievedChunk]:
        """Execute hybrid retrieval and return enriched chunks.

        Creates both retriever arms with the given filters, fuses via RRF,
        converts the fused Documents to RetrievedChunk, and enriches with
        metadata (record_title, sitting_title, date).

        Args:
            query: The user's search query.
            filters: Optional pre-filters (entity names, date range, speaker).
            limit: Maximum number of results (default 10, max 50).

        Returns:
            List of RetrievedChunk sorted by descending fused relevance score.
        """
        limit = min(limit, _MAX_LIMIT)

        logger.info(
            "rag.retriever.hybrid_retrieve_start",
            query_preview=query[:80],
            has_filters=filters is not None,
            limit=limit,
        )

        # Step 1: Create both retriever arms with filters applied
        fulltext = FulltextRetriever(
            session_factory=self._session_factory,
            filters=filters,
        )
        vector = VectorRetriever(
            session_factory=self._session_factory,
            embeddings=self._embeddings,
            filters=filters,
        )

        # Step 2: Fuse via RRF
        rrf = RRFRetriever(fulltext, vector)
        fused_docs = await rrf.retrieve(query, limit)

        # Step 3: Convert Documents to RetrievedChunk
        chunks = self._docs_to_chunks(fused_docs)

        # Step 4: Enrich with metadata (record_title, sitting_title, date)
        if chunks:
            chunks = await self._enrich_with_metadata(chunks)

        logger.info(
            "rag.retriever.hybrid_retrieve_complete",
            query_preview=query[:80],
            results=len(chunks),
        )

        return chunks

    @staticmethod
    def _docs_to_chunks(documents: list[Document]) -> list[RetrievedChunk]:
        """Convert fused LangChain Documents to RetrievedChunk instances."""
        chunks: list[RetrievedChunk] = []
        for doc in documents:
            meta = doc.metadata
            chunk = RetrievedChunk(
                chunk_id=meta["chunk_id"],
                text=doc.page_content,
                relevance_score=meta["score"],
                transcript_id=meta["transcript_id"],
                speaker=meta.get("speaker"),
                start_s=meta.get("start_s"),
                end_s=meta.get("end_s"),
                matched_entities=meta.get("entity_names", []),
            )
            chunks.append(chunk)
        return chunks

    async def _enrich_with_metadata(
        self,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Enrich chunks with record title, sitting title, and date.

        Fetches metadata via a single query joining through the
        transcript → hansard_record → sitting chain.
        """
        chunk_ids = [c.chunk_id for c in chunks]

        sql = """
            SELECT
                tc.id AS chunk_id,
                hr.title AS record_title,
                s.title AS sitting_title,
                hr.date::text AS record_date
            FROM transcript_chunk tc
            JOIN transcript t ON t.id = tc.transcript_id
            JOIN hansard_record hr ON hr.id = t.record_id
            JOIN sitting s ON s.id = hr.sitting_id
            WHERE tc.id = ANY(:chunk_ids)
        """

        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    text(sql), {"chunk_ids": chunk_ids}
                )
                rows = result.fetchall()

                metadata_map: dict[int, dict] = {}
                for row in rows:
                    metadata_map[row[0]] = {
                        "record_title": row[1],
                        "sitting_title": row[2],
                        "date": row[3],
                    }

                for chunk in chunks:
                    meta = metadata_map.get(chunk.chunk_id, {})
                    chunk.record_title = meta.get("record_title")
                    chunk.sitting_title = meta.get("sitting_title")
                    chunk.date = meta.get("date")

        except Exception:
            logger.error(
                "rag.retriever.metadata_enrichment_failed",
                exc_info=True,
            )

        return chunks
