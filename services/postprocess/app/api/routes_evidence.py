"""Correction evidence read endpoint — GET /v1/transcripts/{id}/correction-evidence.

Feature: transcript-evidence-navigation (task 5.1, Req 10.4, 10.5).

Authenticated by the ``SERVICE_TOKEN`` Bearer dependency, this read-only endpoint
returns the persisted :class:`~app.models.evidence.CorrectionEvidence` for a
transcript version. The Gateway proxies it and maps snake_case -> camelCase at
the boundary (task 6.1); Python is the snake_case source of truth (Req 8.2), so
the response here uses snake_case field names — the model is serialized WITHOUT
``by_alias`` (the camelCase aliases are the Gateway/frontend contract, not the
Python API surface).

A version that carries no evidence returns ``CorrectionEvidence`` with an empty
``entries`` list rather than a 404 (Req 10.5): the editor treats an empty list
identically to Baseline "no evidence", which keeps the read path simple and
backward-compatible. When no ``version`` query parameter is supplied, the latest
version that carries evidence is read (``MAX(version)``); a transcript with no
evidence at all resolves to version ``0`` with empty ``entries``.

The endpoint is read-only: it issues only ``SELECT`` statements via
``app/correction/evidence_reader.py`` and never writes the correction table
(Req 8.5, 8.6).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse

from app.correction.evidence_reader import load_correction_evidence
from app.deps import get_correlation_id, verify_service_token
from app.models.evidence import CorrectionEvidence

router = APIRouter()


@router.get(
    "/v1/transcripts/{transcript_id}/correction-evidence",
    dependencies=[Depends(verify_service_token)],
)
async def get_correction_evidence(
    request: Request,
    transcript_id: int = Path(..., ge=1, description="Transcript id to read evidence for"),
    version: int | None = Query(
        default=None,
        ge=1,
        description="Transcript version; when omitted, the latest version with evidence",
    ),
    correlation_id: str = Depends(get_correlation_id),
) -> JSONResponse:
    """Return the persisted Correction_Evidence for a transcript version.

    Read-only. Returns 200 with a snake_case :class:`CorrectionEvidence`; a
    version carrying no evidence returns an empty ``entries`` list (Req 10.5).
    Returns 503 when the shared session factory is not available (service not
    fully started).
    """
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "type": "ServiceUnavailable",
                    "code": "DATABASE_NOT_READY",
                    "message": "Database session factory is not available",
                }
            },
            headers={"X-Correlation-Id": correlation_id},
        )

    evidence: CorrectionEvidence = await load_correction_evidence(
        session_factory, transcript_id, version
    )

    # snake_case source of truth (Req 8.2): serialize WITHOUT by_alias. Keep
    # exclude_none so unsupplied optional fields (provenance, entity
    # classification, mapping_confidence) are omitted rather than emitted as
    # placeholders (Req 10.4), matching the additive Baseline contract.
    return JSONResponse(
        status_code=200,
        content=evidence.model_dump(exclude_none=True),
        headers={"X-Correlation-Id": correlation_id},
    )
