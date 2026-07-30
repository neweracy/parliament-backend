"""FastAPI dependencies — authentication and correlation ID handling."""

from __future__ import annotations

import secrets
import uuid

from fastapi import Depends, Header, HTTPException, Response

from app.config import Settings


def _get_settings() -> Settings:
    """Return cached settings instance.

    Separated so tests can override via dependency_overrides.
    """
    from app.config import get_settings

    return get_settings()


def verify_service_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(_get_settings),
) -> None:
    """Validate the Bearer Service_Token using constant-time comparison.

    Raises HTTP 401 with the standard error envelope when the token is
    missing or invalid.
    """
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "type": "AuthenticationError",
                    "code": "MISSING_TOKEN",
                    "message": "Authorization header is required",
                }
            },
        )

    # Expect "Bearer <token>"
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "type": "AuthenticationError",
                    "code": "INVALID_TOKEN",
                    "message": "Invalid authorization header format",
                }
            },
        )

    token = parts[1]
    if not secrets.compare_digest(token, settings.service_token or ""):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "type": "AuthenticationError",
                    "code": "INVALID_TOKEN",
                    "message": "Invalid service token",
                }
            },
        )


async def get_correlation_id(
    response: Response,
    x_correlation_id: str | None = Header(default=None),
) -> str:
    """Extract or generate a correlation ID and echo it in the response.

    If the ``X-Correlation-Id`` request header is present, returns it as-is.
    If absent, generates a new UUID4. In both cases the value is echoed back
    via the ``X-Correlation-Id`` response header.
    """
    correlation_id = x_correlation_id if x_correlation_id else str(uuid.uuid4())
    response.headers["X-Correlation-Id"] = correlation_id
    return correlation_id
