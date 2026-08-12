"""RAG API endpoints — search, recommendations, ask, ingest, and reindex.

Exposes the RAG pipeline via five POST endpoints:
- POST /rag/search — hybrid retrieval over indexed transcript chunks
- POST /rag/recommendations — search-query suggestions for the search box
- POST /rag/ask — grounded Q&A with citations
- POST /rag/ingest — trigger async ingestion for a transcript
- POST /rag/reindex — bulk re-ingest transcripts with missing embeddings

All endpoints are authenticated via the service token (same as /v1/postprocess).

Requirements: 8.1, 9.1, 7.7
"""

from __future__ import annotations

import time
from datetime import date

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.deps import verify_service_token
from app.rag.agent import HansardChatAgent
from app.rag.ingestion import TranscriptIngestionWorker
from app.rag.recommendations import (
    MAX_SEARCH_RECOMMENDATION_COUNT,
    TARGET_SEARCH_RECOMMENDATION_COUNT,
    fetch_corpus_hints,
)
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.rag.search_recommendations import generate_search_recommendations

logger = structlog.get_logger("rag.router")

router = APIRouter(prefix="/rag", dependencies=[Depends(verify_service_token)])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """Request body for POST /rag/search."""

    query: str = Field(..., min_length=1, description="Natural language search query")
    entity_filter: list[str] | None = Field(
        default=None, description="Filter by entity names (array overlap)"
    )
    date_from: date | None = Field(
        default=None, description="Include only chunks from sittings on or after this date"
    )
    date_to: date | None = Field(
        default=None, description="Include only chunks from sittings on or before this date"
    )
    speaker: str | None = Field(default=None, description="Filter by speaker label")
    limit: int | None = Field(
        default=10, ge=1, le=50, description="Max results (default 10, max 50)"
    )


class SearchResultItem(BaseModel):
    """A single search result chunk with metadata."""

    chunk_id: int
    chunk_text: str
    relevance_score: float
    transcript_id: int
    speaker: str | None
    start_s: float | None
    end_s: float | None
    matched_entities: list[str]
    record_title: str | None
    sitting_title: str | None
    date: str | None
    sitting_id: int | None = None
    record_id: int | None = None


class SearchResponse(BaseModel):
    """Response body for POST /rag/search."""

    results: list[SearchResultItem]
    total_matched: int
    latency_ms: float = 0.0


class ChatMessage(BaseModel):
    """A single message in a conversation (user question or assistant answer)."""

    role: str = Field(..., pattern="^(user|assistant)$", description="Message role")
    content: str = Field(..., min_length=1, description="Message content")


class AskRequest(BaseModel):
    """Request body for POST /rag/ask."""

    question: str = Field(..., min_length=1, description="Natural language question")
    entity_filter: list[str] | None = Field(default=None, description="Filter by entity names")
    date_from: date | None = Field(
        default=None, description="Include only chunks from sittings on or after this date"
    )
    date_to: date | None = Field(
        default=None, description="Include only chunks from sittings on or before this date"
    )
    speaker: str | None = Field(default=None, description="Filter by speaker label")
    conversation_history: list[ChatMessage] | None = Field(
        default=None,
        description="Prior conversation messages for multi-turn context (max 20)",
    )


class CitationItem(BaseModel):
    """A citation linking an answer statement to a source chunk."""

    transcript_id: int
    chunk_id: int
    speaker: str | None
    start_s: float | None
    end_s: float | None
    excerpt: str
    sitting_id: int | None = None
    record_id: int | None = None


class SourceChunkItem(BaseModel):
    """A source chunk used as context for the answer."""

    chunk_id: int
    text: str
    relevance_score: float
    transcript_id: int
    speaker: str | None
    start_s: float | None
    end_s: float | None
    matched_entities: list[str]
    record_title: str | None = None
    sitting_title: str | None = None
    date: str | None = None
    sitting_id: int | None = None
    record_id: int | None = None


class Recommendation(BaseModel):
    """A suggested follow-up question or topic based on the current answer."""

    text: str = Field(..., description="Suggested follow-up question")
    reason: str = Field(..., description="Why this is relevant")


class SearchRecommendationRequest(BaseModel):
    """Request body for POST /rag/recommendations.

    `query` is optional because the search box asks for recommendations before
    the user has typed anything — that call yields entry points into the corpus
    rather than refinements of a query.
    """

    query: str | None = Field(
        default=None, description="What the researcher has typed so far, if anything"
    )
    entity_filter: list[str] | None = Field(default=None, description="Filter by entity names")
    date_from: date | None = Field(
        default=None, description="Include only chunks from sittings on or after this date"
    )
    date_to: date | None = Field(
        default=None, description="Include only chunks from sittings on or before this date"
    )
    speaker: str | None = Field(default=None, description="Filter by speaker label")
    limit: int | None = Field(
        default=TARGET_SEARCH_RECOMMENDATION_COUNT,
        ge=1,
        le=MAX_SEARCH_RECOMMENDATION_COUNT,
        description=(
            f"Number of recommendations to return "
            f"(default {TARGET_SEARCH_RECOMMENDATION_COUNT}, "
            f"max {MAX_SEARCH_RECOMMENDATION_COUNT})"
        ),
    )


class SearchRecommendationResponse(BaseModel):
    """Response body for POST /rag/recommendations.

    `recommendations` always holds exactly the requested count — the generator
    tops up from the deterministic builder when the model returns too few, so
    consumers never have to handle an empty set.
    """

    recommendations: list[Recommendation] = Field(default_factory=list)
    latency_ms: float = 0.0
    source: str = Field(
        default="deterministic",
        description="'model', 'mixed', or 'deterministic' — where the items came from",
    )
    model_used: bool = Field(
        default=False, description="Whether the model call ran and returned usable text"
    )


class RelatedRecord(BaseModel):
    """A sitting, record, or speaker behind the answer."""

    transcript_id: int
    label: str
    chunk_id: int
    speaker: str | None = None
    sitting_title: str | None = None
    record_title: str | None = None
    date: str | None = None
    start_s: float | None = None
    sitting_id: int | None = None
    record_id: int | None = None


class AskResponse(BaseModel):
    """Response body for POST /rag/ask."""

    answer: str
    citations: list[CitationItem]
    source_chunks: list[SourceChunkItem]
    recommendations: list[Recommendation] = Field(
        default_factory=list,
        description="Suggested follow-up questions based on the transcript context",
    )
    related_records: list[RelatedRecord] = Field(
        default_factory=list,
        description="Sittings, records, or speakers behind the answer",
    )
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

    embeddings = request.app.state.embeddings
    retriever = HybridRetriever(session_factory, embeddings, settings)

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
            sitting_id=c.sitting_id,
            record_id=c.record_id,
        )
        for c in chunks
    ]

    return SearchResponse(
        results=results,
        total_matched=len(results),
        latency_ms=round(latency_ms, 1),
    )


@router.post("/recommendations", response_model=SearchRecommendationResponse)
async def rag_recommendations(
    body: SearchRecommendationRequest, request: Request
) -> SearchRecommendationResponse:
    """Suggest search queries for the search box.

    Unlike /rag/ask this never returns 503 when the chat model is unconfigured.
    A search box that silently stops offering suggestions is a worse failure than
    one offering deterministic corpus-grounded terms, and the generator always
    has those to fall back on.

    Filters are forwarded to the grounding retrieval so suggestions stay inside
    the slice of the record the user is currently looking at.
    """
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings
    chat_model = getattr(request.app.state, "chat_model", None)

    filters: RetrievalFilters | None = None
    if any([body.entity_filter, body.date_from, body.date_to, body.speaker]):
        filters = RetrievalFilters(
            entity_names=body.entity_filter,
            date_from=body.date_from,
            date_to=body.date_to,
            speaker=body.speaker,
        )

    embeddings = request.app.state.embeddings
    retriever = HybridRetriever(session_factory, embeddings, settings)

    corpus_hints = await fetch_corpus_hints(session_factory)

    count = body.limit if body.limit is not None else TARGET_SEARCH_RECOMMENDATION_COUNT

    result = await generate_search_recommendations(
        chat_model=chat_model,
        query=body.query,
        retriever=retriever,
        filters=filters,
        corpus_hints=corpus_hints,
        count=count,
    )

    return SearchRecommendationResponse(
        recommendations=[
            Recommendation(text=item.text, reason=item.reason) for item in result.recommendations
        ],
        latency_ms=round(result.latency_ms, 1),
        source=result.source,
        model_used=result.model_used,
    )


@router.post("/ask", response_model=AskResponse)
async def rag_ask(body: AskRequest, request: Request) -> AskResponse:
    """Answer a question via the conversational HansardChatAgent.

    The agent decides for itself whether the turn needs a search_hansard call,
    returning the answer with citations, source chunks, and latency.
    """
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings
    chat_model = request.app.state.chat_model

    if chat_model is None:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={
                "error": {
                    "type": "ServiceUnavailable",
                    "code": "LLM_NOT_CONFIGURED",
                    "message": "Chat model is not available for answering",
                }
            },
        )

    # Retrieve relevant chunks
    embeddings = request.app.state.embeddings
    retriever = HybridRetriever(session_factory, embeddings, settings)

    filters: RetrievalFilters | None = None
    if any([body.entity_filter, body.date_from, body.date_to, body.speaker]):
        filters = RetrievalFilters(
            entity_names=body.entity_filter,
            date_from=body.date_from,
            date_to=body.date_to,
            speaker=body.speaker,
        )

    # The agent decides whether to search, so the router cannot know in advance
    # whether hints will be needed. The lookup is two indexed DISTINCT queries
    # and it never fails the answer, so it is fetched up front.
    corpus_hints = await fetch_corpus_hints(session_factory)

    # Build conversation history for the answerer
    conversation_history: list[tuple[str, str]] | None = None
    if body.conversation_history:
        # Limit to last 20 messages to avoid exceeding context window
        recent = body.conversation_history[-20:]
        conversation_history = [(m.role, m.content) for m in recent]

    # Generate a conversational, tool-driven answer with conversation context
    agent = HansardChatAgent(chat_model, retriever, settings)
    answer_response = await agent.chat(
        question=body.question,
        filters=filters,
        conversation_history=conversation_history,
        corpus_hints=corpus_hints,
    )

    # Map to response models
    citations = [
        CitationItem(
            transcript_id=c.transcript_id,
            chunk_id=c.chunk_id,
            speaker=c.speaker,
            start_s=c.start_s,
            end_s=c.end_s,
            excerpt=c.excerpt,
            sitting_id=c.sitting_id,
            record_id=c.record_id,
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
            record_title=c.record_title,
            sitting_title=c.sitting_title,
            date=c.date,
            sitting_id=c.sitting_id,
            record_id=c.record_id,
        )
        for c in answer_response.source_chunks
    ]

    related_records = [
        RelatedRecord(
            transcript_id=r.transcript_id,
            label=r.label,
            chunk_id=r.chunk_id,
            speaker=r.speaker,
            sitting_title=r.sitting_title,
            record_title=r.record_title,
            date=r.date,
            start_s=r.start_s,
            sitting_id=r.sitting_id,
            record_id=r.record_id,
        )
        for r in answer_response.related_records
    ]

    return AskResponse(
        answer=answer_response.answer,
        citations=citations,
        source_chunks=source_chunks,
        recommendations=[
            Recommendation(text=r.text, reason=r.reason) for r in answer_response.recommendations
        ],
        related_records=related_records,
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
        embeddings = request.app.state.embeddings
        ingestion_worker = TranscriptIngestionWorker(
            session_factory, settings, embeddings=embeddings
        )
        ingestion_worker.start()
        request.app.state.ingestion_worker = ingestion_worker

    ingestion_worker.enqueue(body.transcript_id)

    logger.info("rag.ingest.enqueued", transcript_id=body.transcript_id)

    return IngestResponse(
        status="queued",
        transcript_id=body.transcript_id,
    )


# ---------------------------------------------------------------------------
# Bulk re-index endpoint — fixes the "NULL embedding" problem
# ---------------------------------------------------------------------------


class ReindexRequest(BaseModel):
    """Request body for POST /rag/reindex."""

    limit: int = Field(
        default=50, ge=1, le=500, description="Max transcripts to re-ingest (default 50, max 500)"
    )


class ReindexResponse(BaseModel):
    """Response body for POST /rag/reindex."""

    status: str = "queued"
    transcript_ids: list[int] = Field(default_factory=list)
    total_unindexed_chunks: int = 0
    message: str = ""


@router.post("/reindex", status_code=202, response_model=ReindexResponse)
async def rag_reindex(body: ReindexRequest, request: Request) -> ReindexResponse:
    """Find transcripts with unindexed chunks and re-queue them for ingestion.

    Identifies transcript_chunk rows where embedding IS NULL (meaning the
    embedding failed during initial ingestion — typically because AWS
    credentials were unavailable) and enqueues the owning transcripts for
    re-ingestion.

    This is idempotent: re-ingestion deletes existing chunks for a transcript
    before storing new ones, so it is safe to call repeatedly.
    """
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings

    # Find transcripts that have chunks without embeddings
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT DISTINCT tc.transcript_id, COUNT(*) AS unindexed_count "
                    "FROM transcript_chunk tc "
                    "WHERE tc.embedding IS NULL "
                    "GROUP BY tc.transcript_id "
                    "ORDER BY unindexed_count DESC "
                    "LIMIT :limit"
                ),
                {"limit": body.limit},
            )
            rows = result.fetchall()
    except Exception:
        logger.error("rag.reindex.query_failed", exc_info=True)
        return ReindexResponse(
            status="error",
            message="Failed to query unindexed chunks",
        )

    if not rows:
        return ReindexResponse(
            status="complete",
            message="No transcripts with unindexed chunks found — all embeddings are present",
        )

    transcript_ids = [row[0] for row in rows]
    total_unindexed = sum(row[1] for row in rows)

    # Ensure the ingestion worker is running
    ingestion_worker: TranscriptIngestionWorker | None = getattr(
        request.app.state, "ingestion_worker", None
    )

    if ingestion_worker is None:
        embeddings = request.app.state.embeddings
        if embeddings is None:
            return ReindexResponse(
                status="error",
                transcript_ids=transcript_ids,
                total_unindexed_chunks=total_unindexed,
                message=(
                    "Cannot re-index: embedding client is not configured. "
                    "Check that LLM_ENABLED=true and AWS credentials are available."
                ),
            )
        ingestion_worker = TranscriptIngestionWorker(
            session_factory, settings, embeddings=embeddings
        )
        ingestion_worker.start()
        request.app.state.ingestion_worker = ingestion_worker

    # Enqueue each transcript for re-ingestion
    queued_count = 0
    for transcript_id in transcript_ids:
        ingestion_worker.enqueue(transcript_id)
        queued_count += 1

    logger.info(
        "rag.reindex.queued",
        transcript_count=queued_count,
        total_unindexed_chunks=total_unindexed,
    )

    return ReindexResponse(
        status="queued",
        transcript_ids=transcript_ids,
        total_unindexed_chunks=total_unindexed,
        message=(
            f"Queued {queued_count} transcripts for re-ingestion "
            f"({total_unindexed} unindexed chunks)"
        ),
    )
