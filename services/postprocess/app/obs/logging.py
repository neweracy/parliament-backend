"""Structured logging configuration for the Postprocessing Service.

Emits JSON records to stdout carrying correlation id, path, method, status,
and latencies. A redaction processor ensures Service_Token values and
authorization headers never appear in log output.

Requirements: 13.1, 13.2, 13.8, 13.10
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.config import Settings


def _make_redaction_processor(settings: "Settings"):
    """Build a structlog processor that redacts sensitive values.

    Replaces:
    - Any occurrence of the SERVICE_TOKEN value with ``***``
    - Any value under a key named ``authorization`` or ``Authorization`` with ``***``
    """
    token: str | None = settings.service_token

    def _redact(
        logger: Any,  # noqa: ARG001 - required by structlog processor interface
        method_name: str,  # noqa: ARG001 - required by structlog processor interface
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        for key, value in list(event_dict.items()):
            # Redact authorization headers regardless of case
            if key.lower() == "authorization":
                event_dict[key] = "***"
                continue
            # Redact any value that contains the token string
            if token and isinstance(value, str) and token in value:
                event_dict[key] = "***"
        return event_dict

    return _redact


def configure_logging(settings: "Settings") -> None:
    """Set up structlog with JSON output to stdout.

    Configures processors for timestamping, log level injection, caller info,
    exception rendering, and sensitive-value redaction.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _make_redaction_processor(settings),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def log_request(
    correlation_id: str,
    path: str,
    method: str,
    status: int,
    latency_ms: float,
) -> None:
    """Log a request completion record at info level.

    Contains correlation id, path, method, HTTP status, and latency — no
    transcript text.
    """
    logger = structlog.get_logger("request")
    logger.info(
        "request.completed",
        correlation_id=correlation_id,
        path=path,
        method=method,
        status=status,
        latency_ms=round(latency_ms, 2),
    )


def log_correction(
    original: str,
    corrected: str,
    strategy: str,
    confidence: float,
    entity_kind: str,
    entity_type: str,
) -> None:
    """Log one ``correction.applied`` record at debug level.

    Emits per Correction_Record. Never logged at info level, so transcript
    text does not appear in production logs by default.
    """
    logger = structlog.get_logger("correction")
    logger.debug(
        "correction.applied",
        original=original,
        corrected=corrected,
        strategy=strategy,
        confidence=round(confidence, 4),
        entity_kind=entity_kind,
        entity_type=entity_type,
    )
