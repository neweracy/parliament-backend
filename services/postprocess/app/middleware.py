"""Request logging middleware.

Starlette middleware that records start time before processing and calls
``log_request`` at the end of every request with the correlation id, path,
method, HTTP status, and handler latency.

Requirements: 13.2, 17.1
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.obs.logging import log_request


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every completed request with timing and correlation context."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()

        response = await call_next(request)

        latency_ms = (time.perf_counter() - start) * 1000.0

        # Extract correlation id from the response header (set by get_correlation_id
        # dependency) or from the request header, falling back to a generated UUID.
        correlation_id = (
            response.headers.get("x-correlation-id")
            or request.headers.get("x-correlation-id")
            or str(uuid.uuid4())
        )

        log_request(
            correlation_id=correlation_id,
            path=request.url.path,
            method=request.method,
            status=response.status_code,
            latency_ms=latency_ms,
        )

        return response
