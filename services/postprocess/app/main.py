"""Postprocessing Service — FastAPI application entry point.

Wires routes, exception handlers, lifespan (DatasetCache start/stop),
and binds to HOST/PORT from configuration.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 7.13, 7.14, 7.15, 9.8, 16.3
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes_datasets import router as datasets_router
from app.api.routes_health import router as health_router
from app.api.routes_postprocess import router as postprocess_router
from app.config import get_settings
from app.datasets.cache import DatasetCache
from app.datasets.store import make_engine, make_session_factory
from app.history.writer import CorrectionHistoryWriter
from app.llm.bedrock import BedrockClient
from app.middleware import RequestLoggingMiddleware
from app.obs.metrics import emit_error
from app.rag.clients import create_chat_model, create_embeddings, probe_credentials
from app.rag.diagnostics import router as rag_diagnostics_router
from app.rag.router import router as rag_router

logger = structlog.get_logger("main")

# ---------------------------------------------------------------------------
# Sentry error monitoring (initializes only when SENTRY_DSN is set)
# ---------------------------------------------------------------------------

_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=os.environ.get("SENTRY_RELEASE", "postprocess@0.1.0"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
    )


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Rewrite Pydantic validation errors into the standard error envelope.

    Returns 422 with { "error": { "type": "ValidationError", "code": "INVALID_REQUEST", "message": ... } }
    """
    # Emit error metric (Req 13.5)
    emit_error("INVALID_REQUEST")

    # Build a human-readable message from the validation errors
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = " → ".join(str(part) for part in first.get("loc", []))
        msg = first.get("msg", "Validation error")
        message = f"{loc}: {msg}" if loc else msg
    else:
        message = "Request validation failed"

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "type": "ValidationError",
                "code": "INVALID_REQUEST",
                "message": message,
            }
        },
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Pass through HTTPExceptions preserving the error envelope format.

    The auth dependency (deps.py) raises HTTPException with detail already
    in the error envelope dict format. We pass that through. For other
    HTTPExceptions, wrap in the envelope.
    """
    detail = exc.detail

    # Emit error metric (Req 13.5) — extract code from detail if available
    if isinstance(detail, dict) and "error" in detail:
        error_code = detail["error"].get("code", "HTTP_ERROR")
    else:
        error_code = "HTTP_ERROR"
    emit_error(error_code)

    # If detail is already in our envelope format (dict with "error" key),
    # pass it through directly.
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=detail,
        )

    # Otherwise, wrap in a generic envelope
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "type": "HTTPError",
                "code": "HTTP_ERROR",
                "message": str(detail) if detail else "An error occurred",
            }
        },
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for unhandled pipeline exceptions.

    Returns 500 with { "error": { "type": "PostprocessingError", "code": "PIPELINE_FAILED", "message": ... } }
    """
    # Emit error metric (Req 13.5)
    emit_error("PIPELINE_FAILED")

    logger.error(
        "pipeline.unhandled_exception",
        exc_info=True,
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "type": "PostprocessingError",
                "code": "PIPELINE_FAILED",
                "message": "An internal error occurred during post-processing",
            }
        },
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle.

    Startup: Load settings, configure logging, start Dataset_Cache,
    construct BedrockClient (if LLM enabled), record start time.
    Shutdown: Stop Dataset_Cache (completes in-flight refresh).
    """
    # Load settings and store on app state
    settings = get_settings()
    app.state.settings = settings
    app.state.start_time = time.time()

    # Configure structured logging before any log calls
    from app.obs.logging import configure_logging  # noqa: WPS433

    configure_logging(settings)

    # ONE engine for the whole process. Previously the dataset cache, pg_trgm
    # retrieval, and the history writer each built their own, so a single
    # instance held three independent pools (up to 30 server-side connections)
    # and instance count had to be divided by three when sizing against the
    # server's max_connections. They share one pool now.
    engine = make_engine(
        settings.database_url,  # type: ignore[arg-type]
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle_seconds=settings.db_pool_recycle_seconds,
        pool_timeout_seconds=settings.db_pool_timeout_seconds,
        connect_timeout_seconds=settings.db_connect_timeout_seconds,
        statement_timeout_ms=settings.db_statement_timeout_ms,
        sslmode=settings.db_sslmode,
    )
    session_factory = make_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Initialize and start the Dataset_Cache on the shared pool
    cache = DatasetCache(
        session_factory=session_factory,
        refresh_seconds=settings.dataset_refresh_seconds,
        load_retry_seconds=settings.dataset_load_retry_seconds,
    )
    app.state.dataset_cache = cache

    await cache.start()

    # Construct BedrockClient if LLM refinement is enabled (for correction pipeline)
    bedrock_client: BedrockClient | None = None
    if settings.llm_enabled:
        try:
            bedrock_client = BedrockClient(settings)
        except Exception:
            logger.error("llm.bedrock.init_failed", exc_info=True)
            bedrock_client = None
    app.state.bedrock_client = bedrock_client

    # Create LangChain clients for the RAG pipeline
    chat_model = None
    embeddings = None
    if settings.llm_enabled and probe_credentials():
        try:
            chat_model = create_chat_model(settings)
            embeddings = create_embeddings(settings)
        except Exception:
            logger.error("rag.clients.init_failed", exc_info=True)
    app.state.chat_model = chat_model
    app.state.embeddings = embeddings

    # Construct CorrectionHistoryWriter if history is enabled (Req 13.9, 17.3)
    history_writer: CorrectionHistoryWriter | None = None
    if settings.history_enabled:
        try:
            history_writer = CorrectionHistoryWriter(
                session_factory,
                max_queue_size=settings.history_queue_size,
                retention_days=settings.history_retention_days,
                retention_interval_seconds=settings.history_retention_interval_seconds,
            )
            history_writer.start()
        except Exception:
            logger.error("history.writer.init_failed", exc_info=True)
            history_writer = None
    app.state.history_writer = history_writer

    logger.info(
        "service.started",
        host=settings.host,
        port=settings.port,
        llm_enabled=settings.llm_enabled,
        bedrock_configured=bedrock_client.is_configured if bedrock_client else False,
        rag_chat_model=chat_model is not None,
        rag_embeddings=embeddings is not None,
        db_max_connections=settings.db_pool_size + settings.db_max_overflow,
        db_sslmode=settings.db_sslmode or "unset",
    )

    yield

    # Shutdown: stop the history writer (drain remaining items)
    if history_writer is not None:
        await history_writer.stop()

    # Shutdown: stop the RAG ingestion worker if it was started
    ingestion_worker = getattr(app.state, "ingestion_worker", None)
    if ingestion_worker is not None:
        await ingestion_worker.stop()

    # Shutdown: stop the cache (cancels refresh tasks; it does not own the engine)
    await cache.stop()

    # Dispose the single shared engine last, once nothing else can use it.
    try:
        await engine.dispose()
    except Exception:
        logger.error("db.engine_dispose_failed", exc_info=True)

    logger.info("service.stopped")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Postprocessing Service",
    description="Transcript post-processing: entity correction, year/date correction, and LLM refinement",
    version="0.1.0",
    lifespan=lifespan,
)

# Register exception handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]

# Register request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Include route modules
app.include_router(postprocess_router)
app.include_router(health_router)
app.include_router(datasets_router)
app.include_router(rag_router)
app.include_router(rag_diagnostics_router)
