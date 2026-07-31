"""Hybrid retrieval engine — dense + lexical search with RRF fusion.

Executes parallel vector similarity search (cosine distance via HNSW) and
PostgreSQL full-text search (ts_rank on tsvector), then fuses results using
Reciprocal Rank Fusion (k=60). Supports pre-filtering by entity names,
date range, and speaker label.

Follows the same async pattern as ingestion.py: uses sqlalchemy text() for
raw SQL, boto3 for Bedrock embedding, and structlog for structured logging.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date

import boto3
import structlog
from botocore.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.config import Settings

logger = structlog.get_logger("rag.retrieval")

# Embedding model — must match the model used during ingestion
_EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
_EMBEDDING_DIMENSION = 1024

# Reciprocal Rank Fusion constant
_RRF_K = 60

# Default and maximum retrieval limits
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50

# Number of candidates to fetch from each search arm before fusion
_CANDIDATE_POOL_SIZE = 100


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


class HybridRetriever:
    """Executes dense + lexical search with RRF fusion.

    Usage::

        retriever = HybridRetriever(session_factory, settings)
        results = await retriever.retrieve(
            query="budget allocation for education",
            filters=RetrievalFilters(speaker="Hon. Doe"),
            limit=10,
        )
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._bedrock_client = self._build_bedrock_client(settings)

    async def retrieve(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[RetrievedChunk]:
        """Execute hybrid retrieval and return top-K fused results.

        Steps:
            1. Embed the query string using Bedrock Titan V2.
            2. Execute vector similarity search (cosine, HNSW index).
            3. Execute full-text search (ts_rank on tsvector).
            4. Fuse results using Reciprocal Rank Fusion (k=60).
            5. Apply entity/date/speaker pre-filters.
            6. Return top-K merged results with metadata.

        Args:
            query: The natural language search query.
            filters: Optional pre-filters to narrow candidates.
            limit: Maximum number of results to return (clamped to 50).

        Returns:
            A list of RetrievedChunk objects sorted by descending
            relevance score (fused RRF score).
        """
        # Clamp limit
        limit = min(max(limit, 1), _MAX_LIMIT)

        logger.info(
            "rag.retrieval.start",
            query_preview=query[:80],
            limit=limit,
            has_filters=filters is not None,
        )

        # Step 1: Embed the query
        try:
            query_embedding = await self._generate_embedding(query)
        except Exception:
            logger.error(
                "rag.retrieval.embedding_failed",
                query_preview=query[:80],
                exc_info=True,
            )
            return []

        # Steps 2 & 3: Execute both searches in parallel
        vector_results, fulltext_results = await asyncio.gather(
            self._vector_search(query_embedding, filters),
            self._fulltext_search(query, filters),
        )

        # Step 4: Reciprocal Rank Fusion
        fused_results = self._reciprocal_rank_fusion(
            vector_results, fulltext_results
        )

        # Step 5: Truncate to limit
        top_k = fused_results[:limit]

        # Step 6: Enrich with metadata
        if top_k:
            top_k = await self._enrich_with_metadata(top_k)

        logger.info(
            "rag.retrieval.complete",
            query_preview=query[:80],
            vector_candidates=len(vector_results),
            fulltext_candidates=len(fulltext_results),
            fused_total=len(fused_results),
            returned=len(top_k),
        )

        return top_k

    # ------------------------------------------------------------------
    # Search arms
    # ------------------------------------------------------------------

    async def _vector_search(
        self,
        query_embedding: list[float],
        filters: RetrievalFilters | None,
    ) -> list[dict]:
        """Execute vector similarity search using cosine distance.

        Uses the HNSW index on the embedding column. Returns candidate
        chunks ranked by cosine similarity score.
        """
        where_clauses, params = self._build_filter_clauses(filters)
        params["query_embedding"] = str(query_embedding)
        params["pool_size"] = _CANDIDATE_POOL_SIZE

        # Cosine similarity: 1 - (embedding <=> query_embedding)
        # The <=> operator computes cosine distance; subtract from 1 for similarity.
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
            {' AND ' + ' AND '.join(where_clauses) if where_clauses else ''}
            ORDER BY tc.embedding <=> :query_embedding::vector
            LIMIT :pool_size
        """

        try:
            async with self._session_factory() as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
                return [
                    {
                        "chunk_id": row[0],
                        "text": row[1],
                        "transcript_id": row[2],
                        "speaker": row[3],
                        "start_s": row[4],
                        "end_s": row[5],
                        "entity_names": row[6] or [],
                        "score": float(row[7]),
                    }
                    for row in rows
                ]
        except Exception:
            logger.error("rag.retrieval.vector_search_failed", exc_info=True)
            return []

    async def _fulltext_search(
        self,
        query: str,
        filters: RetrievalFilters | None,
    ) -> list[dict]:
        """Execute full-text search using ts_rank on tsvector.

        Uses the GIN index on the tsv column. Returns candidate chunks
        ranked by ts_rank score.
        """
        where_clauses, params = self._build_filter_clauses(filters)
        params["query"] = query
        params["pool_size"] = _CANDIDATE_POOL_SIZE

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
            {' AND ' + ' AND '.join(where_clauses) if where_clauses else ''}
            ORDER BY score DESC
            LIMIT :pool_size
        """

        try:
            async with self._session_factory() as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
                return [
                    {
                        "chunk_id": row[0],
                        "text": row[1],
                        "transcript_id": row[2],
                        "speaker": row[3],
                        "start_s": row[4],
                        "end_s": row[5],
                        "entity_names": row[6] or [],
                        "score": float(row[7]),
                    }
                    for row in rows
                ]
        except Exception:
            logger.error("rag.retrieval.fulltext_search_failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # RRF Fusion
    # ------------------------------------------------------------------

    @staticmethod
    def _reciprocal_rank_fusion(
        vector_results: list[dict],
        fulltext_results: list[dict],
    ) -> list[RetrievedChunk]:
        """Fuse two ranked lists using Reciprocal Rank Fusion (k=60).

        For each document appearing in either list, the fused score is:
            fused_score = sum(1 / (k + rank)) across all lists
        where rank is 1-based position in each list.

        Returns results sorted by descending fused score.
        """
        # Build a map of chunk_id -> accumulated data
        chunk_map: dict[int, dict] = {}

        # Process vector results (rank is 1-based)
        for rank, item in enumerate(vector_results, start=1):
            chunk_id = item["chunk_id"]
            rrf_score = 1.0 / (_RRF_K + rank)

            if chunk_id in chunk_map:
                chunk_map[chunk_id]["fused_score"] += rrf_score
            else:
                chunk_map[chunk_id] = {
                    "chunk_id": chunk_id,
                    "text": item["text"],
                    "transcript_id": item["transcript_id"],
                    "speaker": item["speaker"],
                    "start_s": item["start_s"],
                    "end_s": item["end_s"],
                    "entity_names": item["entity_names"],
                    "fused_score": rrf_score,
                }

        # Process fulltext results (rank is 1-based)
        for rank, item in enumerate(fulltext_results, start=1):
            chunk_id = item["chunk_id"]
            rrf_score = 1.0 / (_RRF_K + rank)

            if chunk_id in chunk_map:
                chunk_map[chunk_id]["fused_score"] += rrf_score
            else:
                chunk_map[chunk_id] = {
                    "chunk_id": chunk_id,
                    "text": item["text"],
                    "transcript_id": item["transcript_id"],
                    "speaker": item["speaker"],
                    "start_s": item["start_s"],
                    "end_s": item["end_s"],
                    "entity_names": item["entity_names"],
                    "fused_score": rrf_score,
                }

        # Sort by descending fused score
        sorted_chunks = sorted(
            chunk_map.values(),
            key=lambda x: x["fused_score"],
            reverse=True,
        )

        # Convert to RetrievedChunk dataclass instances
        return [
            RetrievedChunk(
                chunk_id=item["chunk_id"],
                text=item["text"],
                relevance_score=item["fused_score"],
                transcript_id=item["transcript_id"],
                speaker=item["speaker"],
                start_s=item["start_s"],
                end_s=item["end_s"],
                matched_entities=item["entity_names"],
            )
            for item in sorted_chunks
        ]

    # ------------------------------------------------------------------
    # Metadata enrichment
    # ------------------------------------------------------------------

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
                "rag.retrieval.metadata_enrichment_failed",
                exc_info=True,
            )

        return chunks

    # ------------------------------------------------------------------
    # Filter clause builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter_clauses(
        filters: RetrievalFilters | None,
    ) -> tuple[list[str], dict]:
        """Build SQL WHERE clauses and params from RetrievalFilters.

        Returns a tuple of (list of SQL clause strings, params dict).
        Clauses use named parameters compatible with sqlalchemy text().
        """
        clauses: list[str] = []
        params: dict = {}

        if filters is None:
            return clauses, params

        # Entity pre-filter: array overlap (GIN index on entity_names)
        if filters.entity_names:
            clauses.append("tc.entity_names && :entity_filter")
            params["entity_filter"] = filters.entity_names

        # Date range filter: join through transcript → record → sitting
        if filters.date_from:
            clauses.append("s.date_from >= :date_from")
            params["date_from"] = filters.date_from.isoformat()

        if filters.date_to:
            clauses.append("s.date_to <= :date_to")
            params["date_to"] = filters.date_to.isoformat()

        # Speaker filter
        if filters.speaker:
            clauses.append("tc.speaker = :speaker")
            params["speaker"] = filters.speaker

        return clauses, params

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    @staticmethod
    def _build_bedrock_client(settings: Settings):
        """Build a boto3 bedrock-runtime client for embedding generation."""
        config = Config(
            region_name=settings.aws_region,
            read_timeout=30.0,
            connect_timeout=10.0,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        return boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            config=config,
        )

    async def _generate_embedding(self, text_content: str) -> list[float]:
        """Call Bedrock Titan Text Embeddings V2 to generate a query embedding.

        Runs the synchronous boto3 call in a thread executor to avoid
        blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._invoke_embedding_model, text_content
        )

    def _invoke_embedding_model(self, text_content: str) -> list[float]:
        """Synchronous call to Bedrock Titan Text Embeddings V2."""
        body = json.dumps({
            "inputText": text_content,
            "dimensions": _EMBEDDING_DIMENSION,
            "normalize": True,
        })

        response = self._bedrock_client.invoke_model(
            modelId=_EMBEDDING_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=body,
        )

        response_body = json.loads(response["body"].read())
        return response_body["embedding"]
