"""Error envelope models.

Provides a uniform error response shape matching the Gateway's existing
envelope: ``{ "error": { "type": "...", "code": "...", "message": "..." } }``.
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Inner error payload with type, code, and human-readable message."""

    type: str
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """Top-level error response wrapper.

    Serializes as:
        { "error": { "type": "ValidationError", "code": "INVALID_REQUEST", "message": "..." } }
    """

    error: ErrorDetail
