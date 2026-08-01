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
