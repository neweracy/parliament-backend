"""Postprocessing Service — FastAPI application entry point.

Wires routes, exception handlers, lifespan (DatasetCache start/stop),
and binds to HOST/PORT from configuration.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 7.13, 7.14, 7.15, 9.8, 16.3
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

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

logger = structlog.get_logger("main")


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

    # Initialize and start the Dataset_Cache
    cache = DatasetCache(
        database_url=settings.database_url,  # type: ignore[arg-type]
        refresh_seconds=settings.dataset_refresh_seconds,
        load_retry_seconds=settings.dataset_load_retry_seconds,
    )
    app.state.dataset_cache = cache

    await cache.start()

    # Create a shared session factory for pg_trgm retrieval (Req 12.6, 17.5)
    retrieval_engine = make_engine(settings.database_url)  # type: ignore[arg-type]
    retrieval_session_factory = make_session_factory(retrieval_engine)
    app.state.session_factory = retrieval_session_factory

    # Construct BedrockClient if LLM refinement is enabled
    bedrock_client: BedrockClient | None = None
    if settings.llm_enabled:
        try:
            bedrock_client = BedrockClient(settings)
        except Exception:
            logger.error("llm.bedrock.init_failed", exc_info=True)
            bedrock_client = None
    app.state.bedrock_client = bedrock_client

    # Construct CorrectionHistoryWriter if history is enabled (Req 13.9, 17.3)
    history_writer: CorrectionHistoryWriter | None = None
    if settings.history_enabled:
        try:
            history_engine = make_engine(settings.database_url)  # type: ignore[arg-type]
            history_session_factory = make_session_factory(history_engine)
            history_writer = CorrectionHistoryWriter(history_session_factory)
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
    )

    yield

    # Shutdown: stop the history writer (drain remaining items)
    if history_writer is not None:
        await history_writer.stop()

    # Shutdown: stop the cache (cancel refresh tasks, dispose engine)
    await cache.stop()

    # Dispose the retrieval engine
    try:
        await retrieval_engine.dispose()
    except Exception:
        pass

    # Dispose the history engine
    if history_writer is not None:
        try:
            await history_engine.dispose()
        except Exception:
            pass

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
