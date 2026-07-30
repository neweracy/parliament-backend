"""Dataset reload endpoint — POST /v1/datasets/reload.

Authenticated. Triggers cache.reload() and returns 200 with the new record count.

Requirements: 1.4, 9.12
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.deps import verify_service_token

router = APIRouter()


@router.post("/v1/datasets/reload", dependencies=[Depends(verify_service_token)])
async def reload_datasets(request: Request) -> JSONResponse:
    """Trigger an on-demand reload of the Dataset_Cache.

    Returns 200 with the new record count on success.
    Returns 503 if the reload fails and no snapshot is available.
    """
    cache = request.app.state.dataset_cache

    new_snapshot = await cache.reload()

    if new_snapshot is not None:
        return JSONResponse(
            status_code=200,
            content={
                "record_count": new_snapshot.record_count,
                "version": new_snapshot.version,
                "loaded_at": new_snapshot.loaded_at.isoformat(),
            },
        )

    # Reload failed — check if we still have a snapshot
    current = cache.get_snapshot()
    if current is not None:
        return JSONResponse(
            status_code=200,
            content={
                "record_count": current.record_count,
                "version": current.version,
                "loaded_at": current.loaded_at.isoformat(),
                "reload_status": "failed_kept_previous",
            },
        )

    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": "ServiceUnavailable",
                "code": "DATASET_NOT_LOADED",
                "message": "Dataset reload failed and no previous snapshot available",
            }
        },
    )
