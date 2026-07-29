"""Postprocess endpoint — POST /v1/postprocess.

Authenticated. Accepts a CorrectionRequest body, runs the pipeline, and
returns the CorrectionResponse serialized with by_alias=True, exclude_none=True.

Sets X-Postprocess-Stage response header.
Returns 503 if Dataset_Cache not loaded.

Requirements: 1.1, 1.2, 1.4, 1.7, 7.13, 7.15
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.deps import get_correlation_id, verify_service_token
from app.models.request import CorrectionRequest
from app.pipeline import run_pipeline

router = APIRouter()


@router.post("/v1/postprocess", dependencies=[Depends(verify_service_token)])
async def postprocess(
    body: CorrectionRequest,
    request: Request,
    response: Response,
    correlation_id: str = Depends(get_correlation_id),
) -> JSONResponse:
    """Run the full correction pipeline on the given transcript and words.

    Returns 503 if the Dataset_Cache has not completed its first load.
    Otherwise runs the pipeline and returns the CorrectionResponse.
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
            headers={
                "X-Correlation-Id": correlation_id,
                "X-Postprocess-Stage": "none",
            },
        )

    # Inject the correlation ID into the request if not already set
    if body.correlation_id is None:
        body.correlation_id = correlation_id

    # Run the pipeline
    result = await run_pipeline(
        body,
        cache,
        bedrock_client=getattr(request.app.state, "bedrock_client", None),
        settings=getattr(request.app.state, "settings", None),
    )

    # Serialize with by_alias=True, exclude_none=True
    return JSONResponse(
        status_code=200,
        content=result.model_dump(by_alias=True, exclude_none=True),
        headers={
            "X-Postprocess-Stage": "complete",
            "X-Correlation-Id": correlation_id,
        },
    )
