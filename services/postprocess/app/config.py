"""Application configuration via pydantic-settings.

Reads all settings from environment variables. Numeric variables that fail to
parse fall back to the documented default with one ``config.invalid_value``
warning. Required secrets (SERVICE_TOKEN, DATABASE_URL) log one
``config.missing_required`` error and exit non-zero when absent.
"""

from __future__ import annotations

import sys
from typing import Any

import structlog
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger("config")


def _safe_int(raw: Any, default: int, name: str) -> int:
    """Parse *raw* as int, falling back to *default* with a warning."""
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("config.invalid_value", variable=name, raw=raw, default=default)
        return default


def _safe_float(raw: Any, default: float, name: str) -> float:
    """Parse *raw* as float, falling back to *default* with a warning."""
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("config.invalid_value", variable=name, raw=raw, default=default)
        return default


class Settings(BaseSettings):
    """Postprocessing Service configuration.

    All values are read from environment variables (prefix-free).
    Secrets (SERVICE_TOKEN, DATABASE_URL) must be injected — never committed.
    """

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8082
    # Read by the Uvicorn process launcher (not by app code)
    uvicorn_workers: int = 2
    # Read by the Uvicorn process launcher (not by app code)
    drain_timeout_seconds: int = 15

    # --- Required secrets (validated at startup) ---
    service_token: str | None = None
    database_url: str | None = None

    # --- Database connection pool ---
    # One shared pool serves the dataset cache, pg_trgm retrieval, and the
    # history writer. Total server-side connections per instance is
    # db_pool_size + db_max_overflow, which must be multiplied by the instance
    # count when sizing against the server's max_connections.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Retire connections before an upstream idle reaper can close them.
    db_pool_recycle_seconds: int = 1800
    # Bound the wait for a free pool slot so exhaustion surfaces as an error.
    db_pool_timeout_seconds: int = 10
    # Bound TCP connection establishment.
    db_connect_timeout_seconds: int = 10
    # Server-side per-connection cap so a runaway query cannot pin a slot.
    db_statement_timeout_ms: int = 15000
    # Set to "require" (or stricter, e.g. "verify-full") for managed Postgres.
    # Ignored when DATABASE_URL already contains an sslmode parameter.
    db_sslmode: str | None = None

    # --- Dataset ---
    dataset_refresh_seconds: int = 300
    dataset_load_retry_seconds: int = 30

    # --- Correction history ---
    # Bounded queue for async history writes. Overflow is dropped rather than
    # blocking the request path.
    history_queue_size: int = 1000
    # Rows older than this are deleted by the retention sweeper. 0 disables it.
    history_retention_days: int = 90
    # How often the retention sweeper runs.
    history_retention_interval_seconds: int = 86400

    # --- Correction thresholds ---
    min_confidence: float = 0.75
    word_accept_threshold: float = 0.90
    fuzzy_score_cutoff: float = 0.70
    min_candidate_length: int = 4

    # --- AWS / Bedrock ---
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # --- RAG agent ---
    # Wall-clock timeout for the full agent loop (seconds). The gateway aborts
    # at 30s, so this must be lower to allow a structured fallback.
    rag_agent_timeout_s: int = 25
    # Embedding batch size for ingestion (Titan supports up to 25 texts)
    rag_embedding_batch_size: int = 10
    # Retry attempts for failed embeddings during ingestion
    rag_embedding_max_retries: int = 2

    # --- LLM Refiner ---
    llm_enabled: bool = True
    llm_chunk_size: int = 300
    llm_max_parallel: int = 3
    llm_chunk_timeout_ms: int = 15000
    # Reserved — not yet dispatched; refiner always uses dataset_store today
    llm_retrieval_mode: str = "dataset_store"
    llm_max_prompt_records: int = 50
    # Reserved — not yet dispatched; requires llm_retrieval_mode=knowledge_base
    knowledge_base_id: str | None = None

    # --- History ---
    history_enabled: bool = True

    # --- Log ---
    log_level: str = "info"

    # --- Feature flag (read by service for awareness, not used by it) ---
    postprocess_mode: str | None = None

    model_config = SettingsConfigDict(
        env_file=None,  # Never read from a .env file in production
        case_sensitive=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_numerics(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Coerce numeric env vars, falling back to defaults on parse failure."""
        numeric_int_fields: dict[str, int] = {
            "PORT": 8082,
            "UVICORN_WORKERS": 2,
            "DRAIN_TIMEOUT_SECONDS": 15,
            "DATASET_REFRESH_SECONDS": 300,
            "DATASET_LOAD_RETRY_SECONDS": 30,
            "MIN_CANDIDATE_LENGTH": 4,
            "LLM_CHUNK_SIZE": 300,
            "LLM_MAX_PARALLEL": 3,
            "LLM_CHUNK_TIMEOUT_MS": 15000,
            "LLM_MAX_PROMPT_RECORDS": 50,
            "RAG_AGENT_TIMEOUT_S": 25,
            "RAG_EMBEDDING_BATCH_SIZE": 10,
            "RAG_EMBEDDING_MAX_RETRIES": 2,
            "DB_POOL_SIZE": 5,
            "DB_MAX_OVERFLOW": 5,
            "DB_POOL_RECYCLE_SECONDS": 1800,
            "DB_POOL_TIMEOUT_SECONDS": 10,
            "DB_CONNECT_TIMEOUT_SECONDS": 10,
            "DB_STATEMENT_TIMEOUT_MS": 15000,
            "HISTORY_QUEUE_SIZE": 1000,
            "HISTORY_RETENTION_DAYS": 90,
            "HISTORY_RETENTION_INTERVAL_SECONDS": 86400,
        }
        numeric_float_fields: dict[str, float] = {
            "MIN_CONFIDENCE": 0.75,
            "WORD_ACCEPT_THRESHOLD": 0.90,
            "FUZZY_SCORE_CUTOFF": 0.70,
        }

        for field_name, default in numeric_int_fields.items():
            key = field_name.lower()
            raw = values.get(key) or values.get(field_name)
            if raw is not None and not isinstance(raw, int):
                values[key] = _safe_int(raw, default, field_name)

        for field_name, default in numeric_float_fields.items():
            key = field_name.lower()
            raw = values.get(key) or values.get(field_name)
            if raw is not None and not isinstance(raw, (int, float)):
                values[key] = _safe_float(raw, default, field_name)

        return values


def validate_required(settings: Settings) -> None:
    """Check required secrets and exit non-zero if any are missing.

    Logs one ``config.missing_required`` error per missing setting.
    """
    missing: list[str] = []

    if not settings.service_token:
        missing.append("SERVICE_TOKEN")

    if not settings.database_url:
        missing.append("DATABASE_URL")

    if missing:
        for name in missing:
            logger.error("config.missing_required", variable=name)
        sys.exit(1)


def get_settings() -> Settings:
    """Build, validate, and return the application settings.

    Exits non-zero if required secrets are absent.
    """
    settings = Settings()
    validate_required(settings)
    return settings
