"""Metrics emission for the Postprocessing Service via CloudWatch EMF.

Uses ``aws-embedded-metrics`` when available and configured (production on ECS).
Falls back to structlog-based metric events when EMF is not configured or in
test/local environments. This ensures metric call sites never raise and the
service remains operational without CloudWatch.

Dimensions are CLOSED SET only:
- ``strategy`` for correction counts (bounded by MatchStrategy enum)
- ``error_code`` for error counts (bounded by ErrorEnvelope codes)
- ``llm_status`` for LLM latency (bounded: ok/partial/failed/skipped/unconfigured)
- ``reason`` for LLM chunks dropped (bounded: timeout/error)
- ``path`` for handler latency (bounded: /v1/postprocess, /v1/datasets/reload)

NO transcript text, correlation id, or entity name in any dimension.

Requirements: 13.3, 13.4, 13.5, 13.7, 10.8, 12.10
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger("metrics")

_NAMESPACE = "Postprocessing"
_SERVICE_DIMENSION = "postprocess"


def _try_emit_emf(
    metric_name: str,
    value: float | int,
    unit: str,
    dimensions: dict[str, str],
) -> bool:
    """Attempt to emit a metric via aws-embedded-metrics.

    Returns True if successful, False if EMF is unavailable or fails.
    """
    try:
        from aws_embedded_metrics.config import get_config  # noqa: WPS433
        from aws_embedded_metrics.logger.metrics_logger import MetricsLogger  # noqa: WPS433
    except ImportError:
        return False

    try:
        config = get_config()
        # Only emit via EMF if the environment is configured (ECS/Lambda sets this)
        if not config.log_group_name and not config.namespace:
            return False

        ml = MetricsLogger()
        ml.set_namespace(_NAMESPACE)

        # Set closed-set dimensions — always include 'service'
        all_dims = {"service": _SERVICE_DIMENSION, **dimensions}
        ml.set_dimensions(all_dims)

        ml.put_metric(metric_name, value, unit)
        ml.flush()
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _emit(
    metric_name: str,
    value: float | int,
    unit: str,
    dimensions: dict[str, str] | None = None,
) -> None:
    """Emit a metric — via EMF if configured, otherwise via structlog.

    This is the single internal dispatch point. All public functions call this.
    Never raises.
    """
    dims = dimensions or {}

    # Try EMF first
    if _try_emit_emf(metric_name, value, unit, dims):
        return

    # Fall back to structlog metric event
    logger.info(
        "metric.emitted",
        metric_name=metric_name,
        value=value,
        unit=unit,
        service=_SERVICE_DIMENSION,
        **dims,
    )


# ---------------------------------------------------------------------------
# Public metric functions — one per metric in the design table
# ---------------------------------------------------------------------------


def emit_corrections_applied(strategy: str, count: int = 1) -> None:
    """Emit correction count partitioned by Match_Strategy.

    Dimension: strategy (closed set — exact/fused/joined/initials/
    title_person/phonetic/fuzzy/substring/identity).
    """
    _emit(
        "postprocess.corrections_applied",
        count,
        "Count",
        {"strategy": strategy},
    )


def emit_rule_latency(latency_ms: float) -> None:
    """Emit Rule_Latency (Correction_Engine + Year_Corrector) per request."""
    _emit("postprocess.rule_latency_ms", latency_ms, "Milliseconds")


def emit_llm_latency(latency_ms: float, llm_status: str) -> None:
    """Emit LLM_Refiner latency per request.

    Dimension: llm_status (closed set — ok/partial/failed/skipped/unconfigured).
    """
    _emit(
        "postprocess.llm_latency_ms",
        latency_ms,
        "Milliseconds",
        {"llm_status": llm_status},
    )


def emit_handler_latency(latency_ms: float, path: str = "/v1/postprocess") -> None:
    """Emit total handler latency per request.

    Dimension: path (closed set — /v1/postprocess, /v1/datasets/reload).
    """
    _emit(
        "postprocess.handler_latency_ms",
        latency_ms,
        "Milliseconds",
        {"path": path},
    )


def emit_dataset_cache_records(record_count: int) -> None:
    """Emit the current Dataset_Cache record count after a refresh."""
    _emit("postprocess.dataset_cache_records", record_count, "Count")


def emit_dataset_cache_age(age_seconds: float) -> None:
    """Emit the age of the current Dataset_Cache snapshot in seconds."""
    _emit("postprocess.dataset_cache_age_seconds", age_seconds, "Seconds")


def emit_error(error_code: str) -> None:
    """Emit error count partitioned by error code.

    Dimension: error_code (closed set — INVALID_REQUEST/MISSING_TOKEN/
    INVALID_TOKEN/PIPELINE_FAILED/DATASET_NOT_LOADED).
    """
    _emit(
        "postprocess.errors",
        1,
        "Count",
        {"error_code": error_code},
    )


def emit_llm_prompt_tokens(token_count: int) -> None:
    """Emit prompt token count per LLM invocation."""
    _emit("postprocess.llm_prompt_tokens", token_count, "Count")


def emit_dataset_refresh_failure() -> None:
    """Emit a count of dataset cache refresh failures."""
    _emit("postprocess.dataset_refresh_failures", 1, "Count")


def emit_llm_chunks_dropped(reason: str, count: int = 1) -> None:
    """Emit count of LLM chunks dropped.

    Dimension: reason (closed set — timeout/error).
    """
    _emit(
        "postprocess.llm_chunks_dropped",
        count,
        "Count",
        {"reason": reason},
    )
