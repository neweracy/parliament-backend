"""RAG pipeline diagnostics — comprehensive health check for every component.

Reports the status of database connectivity, embedding coverage, corpus size,
AWS credentials, chat model, embeddings client, circuit breaker, and timeout
configuration. Authenticated via service token.

This endpoint surfaces *why* the pipeline is failing, as opposed to /health
and /ready which only report whether the service is up. Use it when queries
return "no supporting evidence" to distinguish a genuine data gap from an
infrastructure outage.
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.deps import verify_service_token
from app.rag.agent import _bedrock_breaker
from app.rag.clients import probe_credentials

logger = structlog.get_logger("rag.diagnostics")

router = APIRouter(prefix="/rag", dependencies=[Depends(verify_service_token)])


@router.get("/diagnostics")
async def rag_diagnostics(request: Request) -> JSONResponse:
    """Comprehensive RAG pipeline health check.

    Reports the status of every component in the RAG pipeline:
    database, embeddings, chat model, circuit breaker, and corpus coverage.
    Authenticated via service token — not exposed publicly.
    """
    start_time = time.perf_counter()
    checks: dict = {}
    overall_healthy = True

    session_factory = getattr(request.app.state, "session_factory", None)
    settings = getattr(request.app.state, "settings", None)

    # 1. Database connectivity
    db_status: dict = {"status": "unknown"}
    if session_factory is not None:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            db_status = {"status": "ok"}
        except Exception as exc:
            db_status = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            overall_healthy = False
    else:
        db_status = {"status": "error", "error": "session_factory not available"}
        overall_healthy = False
    checks["database"] = db_status

    # 2. Embedding coverage
    embedding_coverage: dict = {"status": "unknown"}
    if session_factory is not None and db_status["status"] == "ok":
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text(
                        "SELECT "
                        "  COUNT(*) AS total_chunks, "
                        "  COUNT(embedding) AS has_embedding, "
                        "  COUNT(*) - COUNT(embedding) AS null_embeddings, "
                        "  COUNT(tsv) AS has_tsvector "
                        "FROM transcript_chunk"
                    )
                )
                row = result.fetchone()
                if row:
                    total = row[0]
                    has_emb = row[1]
                    null_emb = row[2]
                    has_tsv = row[3]
                    coverage_pct = round((has_emb / total * 100), 1) if total > 0 else 0
                    embedding_coverage = {
                        "status": "ok" if null_emb == 0 else "degraded",
                        "total_chunks": total,
                        "has_embedding": has_emb,
                        "null_embeddings": null_emb,
                        "has_tsvector": has_tsv,
                        "embedding_coverage_pct": coverage_pct,
                    }
                    if null_emb > 0:
                        embedding_coverage["action"] = (
                            f"Run POST /rag/reindex to re-embed {null_emb} chunks. "
                            "Vector search is blind to these chunks."
                        )
                else:
                    embedding_coverage = {"status": "empty", "total_chunks": 0}
        except Exception as exc:
            embedding_coverage = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
    checks["embedding_coverage"] = embedding_coverage

    # 3. Corpus statistics
    corpus_stats: dict = {"status": "unknown"}
    if session_factory is not None and db_status["status"] == "ok":
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text(
                        "SELECT "
                        "  COUNT(DISTINCT tc.transcript_id) AS indexed_transcripts, "
                        "  COUNT(DISTINCT t.record_id) AS indexed_records, "
                        "  COUNT(DISTINCT hr.sitting_id) AS indexed_sittings "
                        "FROM transcript_chunk tc "
                        "JOIN transcript t ON t.id = tc.transcript_id "
                        "JOIN hansard_record hr ON hr.id = t.record_id"
                    )
                )
                row = result.fetchone()
                if row:
                    corpus_stats = {
                        "status": "ok",
                        "indexed_transcripts": row[0],
                        "indexed_records": row[1],
                        "indexed_sittings": row[2],
                    }
                    if row[0] == 0:
                        corpus_stats["warning"] = (
                            "No transcripts indexed — all queries will return empty results"
                        )
                        corpus_stats["status"] = "empty"
        except Exception as exc:
            corpus_stats = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    checks["corpus"] = corpus_stats

    # 4. AWS credentials
    creds_ok = probe_credentials()
    checks["aws_credentials"] = {
        "status": "ok" if creds_ok else "error",
        "resolved": creds_ok,
    }
    if not creds_ok:
        checks["aws_credentials"]["error"] = (
            "No AWS credentials found via the default credential chain. "
            "Chat model and embedding API calls will fail."
        )
        overall_healthy = False

    # 5. Chat model
    chat_model = getattr(request.app.state, "chat_model", None)
    checks["chat_model"] = {
        "status": "ok" if chat_model is not None else "unavailable",
        "configured": chat_model is not None,
        "model_id": settings.bedrock_model_id if settings else "unknown",
    }
    if chat_model is None:
        checks["chat_model"]["error"] = (
            "Chat model is not initialized. /rag/ask returns 503. "
            "Check LLM_ENABLED=true and AWS credentials."
        )
        overall_healthy = False

    # 6. Embeddings client
    embeddings = getattr(request.app.state, "embeddings", None)
    checks["embeddings_client"] = {
        "status": "ok" if embeddings is not None else "unavailable",
        "configured": embeddings is not None,
    }
    if embeddings is None:
        checks["embeddings_client"]["error"] = (
            "Embeddings client is not initialized. Vector search arm is disabled — "
            "only fulltext search is active. Check LLM_ENABLED and AWS credentials."
        )

    # 7. Test embedding (only if client is available)
    if embeddings is not None:
        try:
            test_start = time.perf_counter()
            test_embedding = await embeddings.aembed_query("parliamentary budget debate")
            test_ms = (time.perf_counter() - test_start) * 1000
            checks["embedding_api"] = {
                "status": "ok",
                "test_query": "parliamentary budget debate",
                "embedding_dimension": len(test_embedding),
                "latency_ms": round(test_ms, 1),
            }
        except Exception as exc:
            checks["embedding_api"] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "action": (
                    "Check AWS credentials and Bedrock access for "
                    "amazon.titan-embed-text-v2:0"
                ),
            }
            overall_healthy = False
    else:
        checks["embedding_api"] = {
            "status": "skipped",
            "reason": "Embeddings client not configured",
        }

    # 8. Circuit breaker state
    breaker_state = _bedrock_breaker.state.value
    checks["circuit_breaker"] = {
        "status": "ok" if breaker_state == "closed" else "degraded",
        "state": breaker_state,
        "failure_count": _bedrock_breaker._failure_count,
        "failure_threshold": _bedrock_breaker._failure_threshold,
        "recovery_timeout_s": _bedrock_breaker._recovery_timeout_s,
    }
    if breaker_state == "open":
        checks["circuit_breaker"]["error"] = (
            f"Circuit breaker is OPEN after {_bedrock_breaker._failure_count} consecutive "
            "Bedrock failures. All /rag/ask agent-path requests are being rejected. "
            f"Will auto-recover after {_bedrock_breaker._recovery_timeout_s}s."
        )
        overall_healthy = False

    # 9. Timeout configuration
    checks["timeouts"] = {
        "rag_agent_timeout_s": settings.rag_agent_timeout_s if settings else "unknown",
        "rag_model_timeout_s": settings.rag_model_timeout_s if settings else "unknown",
        "gateway_ask_timeout_s": 60,
        "gateway_search_timeout_s": 30,
    }

    latency_ms = (time.perf_counter() - start_time) * 1000

    status_code = 200 if overall_healthy else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if overall_healthy else "degraded",
            "checks": checks,
            "diagnostics_latency_ms": round(latency_ms, 1),
        },
    )
