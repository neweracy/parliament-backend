"""RAG API endpoints — search, ask, and ingest.

Exposes the RAG pipeline via three POST endpoints:
- POST /rag/search — hybrid retrieval over indexed transcript chunks
- POST /rag/ask — grounded Q&A with citations
- POST /rag/ingest — trigger async ingestion for a transcript

All endpoints are authenticated via the service token (same as /v1/postprocess).

Requirements: 8.1, 9.1, 7.7
"""

from __future__ import annotations

import time
from datetime import date
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.deps import verify_service_token
from app.rag.answering import GroundedAnswerer
from app.rag.ingestion import TranscriptIngestionWorker
from app.rag.retrieval import HybridRetriever, RetrievalFilters

logger = structlog.get_logger("rag.router")

router = APIRouter(prefix="/rag", dependencies=[Depends(verify_service_token)])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """Request body for POST /rag/search."""

    query: str = Field(..., min_length=1, description="Natural language search query")
    entity_filter: Optional[list[str]] = Field(
        default=None, description="Filter by entity names (array overlap)"
    )
    date_from: Optional[date] = Field(
        default=None, description="Include only chunks from sittings on or after this date"
    )
    date_to: Optional[date] = Field(
        default=None, description="Include only chunks from sittings on or before this date"
    )
    speaker: Optional[str] = Field(
        default=None, description="Filter by speaker label"
    )
    limit: Optional[int] = Field(
        default=10, ge=1, le=50, description="Max results (default 10, max 50)"
    )


class SearchResultItem(BaseModel):
    """A single search result chunk with metadata."""

    chunk_id: int
    chunk_text: str
    relevance_score: float
    transcript_id: int
    speaker: Optional[str]
    start_s: Optional[float]
    end_s: Optional[float]
    matched_entities: list[str]
    record_title: Optional[str]
    sitting_title: Optional[str]
    date: Optional[str]


class SearchResponse(BaseModel):
    """Response body for POST /rag/search."""

    results: list[SearchResultItem]
    total_matched: int
    latency_ms: float = 0.0


class AskRequest(BaseModel):
    """Request body for POST /rag/ask."""

    question: str = Field(..., min_length=1, description="Natural language question")
    entity_filter: Optional[list[str]] = Field(
        default=None, description="Filter by entity names"
    )
    date_from: Optional[date] = Field(
        default=None, description="Include only chunks from sittings on or after this date"
    )
    date_to: Optional[date] = Field(
        default=None, description="Include only chunks from sittings on or before this date"
    )
    speaker: Optional[str] = Field(
        default=None, description="Filter by speaker label"
    )


class CitationItem(BaseModel):
    """A citation linking an answer statement to a source chunk."""

    transcript_id: int
    chunk_id: int
    speaker: Optional[str]
    start_s: Optional[float]
    end_s: Optional[float]
    excerpt: str


class SourceChunkItem(BaseModel):
    """A source chunk used as context for the answer."""

    chunk_id: int
    text: str
    relevance_score: float
    transcript_id: int
    speaker: Optional[str]
    start_s: Optional[float]
    end_s: Optional[float]
    matched_entities: list[str]


class AskResponse(BaseModel):
    """Response body for POST /rag/ask."""

    answer: str
    citations: list[CitationItem]
    source_chunks: list[SourceChunkItem]
    latency_ms: float


class IngestRequest(BaseModel):
    """Request body for POST /rag/ingest."""

    transcript_id: int = Field(..., gt=0, description="ID of the transcript to ingest")


class IngestResponse(BaseModel):
    """Response body for POST /rag/ingest (202 Accepted)."""

    status: str = "queued"
    transcript_id: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/search", response_model=SearchResponse)
async def rag_search(body: SearchRequest, request: Request) -> SearchResponse:
    """Execute hybrid retrieval over indexed transcript chunks.

    Calls HybridRetriever.retrieve() with the provided query and filters,
    then returns the results as a structured response.
    """
    start_time = time.perf_counter()

    session_factory = request.app.state.session_factory
    settings = request.app.state.settings

    retriever = HybridRetriever(session_factory, settings)

    # Build filters
    filters: RetrievalFilters | None = None
    if any([body.entity_filter, body.date_from, body.date_to, body.speaker]):
        filters = RetrievalFilters(
            entity_names=body.entity_filter,
            date_from=body.date_from,
            date_to=body.date_to,
            speaker=body.speaker,
        )

    limit = body.limit if body.limit is not None else 10

    chunks = await retriever.retrieve(query=body.query, filters=filters, limit=limit)

    latency_ms = (time.perf_counter() - start_time) * 1000

    results = [
        SearchResultItem(
            chunk_id=c.chunk_id,
            chunk_text=c.text,
            relevance_score=c.relevance_score,
            transcript_id=c.transcript_id,
            speaker=c.speaker,
            start_s=c.start_s,
            end_s=c.end_s,
            matched_entities=c.matched_entities,
            record_title=c.record_title,
            sitting_title=c.sitting_title,
            date=c.date,
        )
        for c in chunks
    ]

    return SearchResponse(
        results=results,
        total_matched=len(results),
        latency_ms=round(latency_ms, 1),
    )


@router.post("/ask", response_model=AskResponse)
async def rag_ask(body: AskRequest, request: Request) -> AskResponse:
    """Answer a question using retrieved chunks and Claude.

    Calls HybridRetriever.retrieve() then GroundedAnswerer.answer(),
    returning the answer with citations, source chunks, and latency.
    """
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings
    bedrock_client = request.app.state.bedrock_client

    if bedrock_client is None:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={
                "error": {
                    "type": "ServiceUnavailable",
                    "code": "LLM_NOT_CONFIGURED",
                    "message": "Bedrock client is not available for answering",
                }
            },
        )

    # Retrieve relevant chunks
    retriever = HybridRetriever(session_factory, settings)

    filters: RetrievalFilters | None = None
    if any([body.entity_filter, body.date_from, body.date_to, body.speaker]):
        filters = RetrievalFilters(
            entity_names=body.entity_filter,
            date_from=body.date_from,
            date_to=body.date_to,
            speaker=body.speaker,
        )

    chunks = await retriever.retrieve(query=body.question, filters=filters, limit=10)

    # Generate grounded answer
    answerer = GroundedAnswerer(bedrock_client, settings)
    answer_response = await answerer.answer(question=body.question, chunks=chunks)

    # Map to response models
    citations = [
        CitationItem(
            transcript_id=c.transcript_id,
            chunk_id=c.chunk_id,
            speaker=c.speaker,
            start_s=c.start_s,
            end_s=c.end_s,
            excerpt=c.excerpt,
        )
        for c in answer_response.citations
    ]

    source_chunks = [
        SourceChunkItem(
            chunk_id=c.chunk_id,
            text=c.text,
            relevance_score=c.relevance_score,
            transcript_id=c.transcript_id,
            speaker=c.speaker,
            start_s=c.start_s,
            end_s=c.end_s,
            matched_entities=c.matched_entities,
        )
        for c in answer_response.source_chunks
    ]

    return AskResponse(
        answer=answer_response.answer,
        citations=citations,
        source_chunks=source_chunks,
        latency_ms=round(answer_response.latency_ms, 1),
    )


@router.post("/ingest", status_code=202, response_model=IngestResponse)
async def rag_ingest(body: IngestRequest, request: Request) -> IngestResponse:
    """Trigger async ingestion for a transcript.

    Enqueues the transcript_id on the TranscriptIngestionWorker.
    Returns 202 Accepted immediately — ingestion proceeds in the background.
    """
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings

    # Get or create the ingestion worker from app state
    ingestion_worker: TranscriptIngestionWorker | None = getattr(
        request.app.state, "ingestion_worker", None
    )

    if ingestion_worker is None:
        # Lazily initialize and start the worker on first use
        ingestion_worker = TranscriptIngestionWorker(session_factory, settings)
        ingestion_worker.start()
        request.app.state.ingestion_worker = ingestion_worker

    ingestion_worker.enqueue(body.transcript_id)

    logger.info("rag.ingest.enqueued", transcript_id=body.transcript_id)

    return IngestResponse(
        status="queued",
        transcript_id=body.transcript_id,
    )
