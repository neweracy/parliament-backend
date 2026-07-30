"""Health check endpoint — GET /health.

Unauthenticated. Returns 200 when the Dataset_Cache is loaded, 503 otherwise.
The body carries record count, load timestamp, version, LLM configured flag,
and uptime seconds.

Requirements: 1.3, 9.8
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Return service health status.

    200 when Dataset_Cache is loaded (with metadata body).
    503 when Dataset_Cache hasn't completed its first load.
    """
    cache = request.app.state.dataset_cache
    snapshot = cache.get_snapshot() if cache else None

    if snapshot is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "type": "ServiceUnavailable",
                    "code": "DATASET_NOT_LOADED",
                    "message": "Dataset cache has not completed initial load",
                }
            },
        )

    # Calculate uptime from the app start time stored in app.state
    start_time: float = getattr(request.app.state, "start_time", time.time())
    uptime_seconds = round(time.time() - start_time, 1)

    # Check if LLM (Bedrock) is configured
    settings = request.app.state.settings
    llm_configured = bool(settings.llm_enabled)

    return JSONResponse(
        status_code=200,
        content={
            "record_count": snapshot.record_count,
            "loaded_at": snapshot.loaded_at.isoformat(),
            "version": snapshot.version,
            "llm_configured": llm_configured,
            "uptime_seconds": uptime_seconds,
        },
    )
