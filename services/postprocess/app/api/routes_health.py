"""Health and readiness endpoints.

Two distinct probes, because they answer different questions:

- ``GET /health`` — liveness. Is the process up and able to serve corrections
  from the cached snapshot? Deliberately does NOT touch the database, so a
  transient database outage cannot trigger a restart loop while the service is
  still successfully serving reads from cache.
- ``GET /ready`` — readiness. Is every dependency actually healthy, database
  included? Load balancers and deployment gates should use this one.

Splitting them matters: with only the cache check, the database could be
completely down while the probe reported 200, so no orchestrator or alarm would
ever notice.

Requirements: 1.3, 9.8
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.datasets.store import check_connectivity
from app.obs.metrics import (
    emit_dataset_cache_age,
    emit_dataset_cache_records,
    emit_db_unavailable,
)

logger = structlog.get_logger("health")

router = APIRouter()


def _uptime_seconds(request: Request) -> float:
    start_time: float = getattr(request.app.state, "start_time", time.time())
    return round(time.time() - start_time, 1)


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Liveness probe — cached snapshot only, no database round-trip.

    200 when the Dataset_Cache is loaded (with metadata body).
    503 when the Dataset_Cache hasn't completed its first load.
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

    settings = request.app.state.settings
    llm_configured = bool(settings.llm_enabled)

    # Emit Dataset_Cache metrics (Req 13.4)
    emit_dataset_cache_records(snapshot.record_count)
    cache_age_seconds = time.time() - snapshot.loaded_at.timestamp()
    emit_dataset_cache_age(cache_age_seconds)

    return JSONResponse(
        status_code=200,
        content={
            "record_count": snapshot.record_count,
            "loaded_at": snapshot.loaded_at.isoformat(),
            "version": snapshot.version,
            "llm_configured": llm_configured,
            "uptime_seconds": _uptime_seconds(request),
        },
    )


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe — verifies the cache AND live database connectivity.

    200 only when the snapshot is loaded and a ``SELECT 1`` succeeds.
    503 when either check fails, with ``checks`` naming the failing dependency.
    """
    cache = request.app.state.dataset_cache
    snapshot = cache.get_snapshot() if cache else None
    session_factory = getattr(request.app.state, "session_factory", None)

    dataset_ok = snapshot is not None
    database_ok = False
    db_error: str | None = None

    if session_factory is not None:
        try:
            async with session_factory() as session:
                await check_connectivity(session)
            database_ok = True
        except Exception as exc:
            db_error = type(exc).__name__
            logger.error("ready.database_unreachable", exc_info=True)
            emit_db_unavailable()
    else:
        db_error = "session_factory_missing"

    checks = {
        "dataset_cache": "ok" if dataset_ok else "not_loaded",
        "database": "ok" if database_ok else "unreachable",
    }

    if dataset_ok and database_ok:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "checks": checks,
                "record_count": snapshot.record_count,  # type: ignore[union-attr]
                "uptime_seconds": _uptime_seconds(request),
            },
        )

    content: dict[str, object] = {
        "error": {
            "type": "ServiceUnavailable",
            "code": "NOT_READY",
            "message": "One or more dependencies are unavailable",
        },
        "checks": checks,
    }
    if db_error:
        content["database_error"] = db_error

    return JSONResponse(status_code=503, content=content)
