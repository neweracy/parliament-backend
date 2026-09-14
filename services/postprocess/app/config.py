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

from app.correction.provider_profiles import (
    PROFILE_FLOAT_PARAMS,
    PROFILE_FLOAT_RANGE,
    SUPPORTED_PROVIDERS,
    ProviderGateProfile,
    default_profile,
    provider_env_var,
)

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

    # --- Correction precision gating tunables (Req 12) ---
    # Each is read from its own SCREAMING_SNAKE_CASE env var. Fractional values
    # clamp to [0, 1]; whole-number values clamp to the range noted alongside.
    # Ranges are enforced once at startup by ``clamp_ranges``.
    # Span_Confidence at or above which a Span is restricted to deterministic
    # strategies. Range [0.0, 1.0].
    high_confidence_threshold: float = 0.90
    # Span_Confidence below which a lexicon Span is exempt from the Lexicon_Gate.
    # Range [0.0, 1.0].
    lexicon_override_threshold: float = 0.60
    # Maximum Levenshtein distance / Span length accepted for a fuzzy match.
    # Range [0.0, 1.0].
    max_relative_distance: float = 0.25
    # Minimum Phonetic_Key length required to attempt a phonetic match.
    # Range [1, 12].
    min_phonetic_key_length: int = 4
    # Minimum Normalized_Similarity required for a phonetic candidate.
    # Range [0.0, 1.0].
    min_phonetic_similarity: float = 0.60
    # Minimum Span length required to attempt a Component_Match. Range [1, 20].
    component_match_min_length: int = 6
    # Maximum number of fuzzy candidates scored for one Span. Range [1, 5000].
    max_candidates_per_span: int = 200
    # Number of Words either side of a Span forming the Context_Window when no
    # speaker attribution is present. Range [1, 500].
    context_window_words: int = 40
    # Amount subtracted from the Evidence_Score of an out-of-scope person
    # candidate. Range [0.0, 1.0].
    out_of_scope_penalty: float = 0.10

    # --- Correction precision gating feature flags (Req 12) ---
    # Each flag is resolved independently from its own SCREAMING_SNAKE_CASE env
    # var and never reads another flag. When every flag is disabled the engine
    # is baseline-equivalent (Req 12.9).
    lexicon_gate_enabled: bool = True
    evidence_confidence_enabled: bool = True
    context_gate_enabled: bool = True
    sitting_scope_enabled: bool = False
    llm_veto_enabled: bool = False

    # --- Per-provider gate profiles (Req 12; spec task 2.12.2) ---
    # ONE flag gates the whole per-provider mechanism. Defaults DISABLED so an
    # all-flags-off configuration stays baseline-equivalent (Req 12.9): when
    # off, ``provider_profiles`` resolves the ``deepgram`` profile — the task
    # 1.1 defaults — for every provider value, and gate evaluation is unchanged.
    # Task 2.12.3 wires the resolved profiles into gate evaluation; this task
    # only defines and resolves them.
    provider_profiles_enabled: bool = False

    # Per-provider overrides for the four gate parameters that differ by
    # provider, each read from its own SCREAMING_SNAKE_CASE env var
    # (PROVIDER_<PROVIDER>_<PARAM>). Absent values fall back to the ``deepgram``
    # (task 1.1) default for that parameter. Every value is fractional in
    # [0.0, 1.0]; ``clamp_ranges`` clamps out-of-range values once at startup.
    # deepgram — MUST equal the task 1.1 defaults (unchanged production path).
    provider_deepgram_high_confidence_threshold: float = 0.90
    provider_deepgram_lexicon_override_threshold: float = 0.60
    provider_deepgram_min_phonetic_similarity: float = 0.60
    provider_deepgram_max_relative_distance: float = 0.25
    # khaya
    provider_khaya_high_confidence_threshold: float = 0.90
    provider_khaya_lexicon_override_threshold: float = 0.60
    provider_khaya_min_phonetic_similarity: float = 0.60
    provider_khaya_max_relative_distance: float = 0.25
    # hybrid
    provider_hybrid_high_confidence_threshold: float = 0.90
    provider_hybrid_lexicon_override_threshold: float = 0.60
    provider_hybrid_min_phonetic_similarity: float = 0.60
    provider_hybrid_max_relative_distance: float = 0.25

    # --- AWS / Bedrock ---
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # Optional override for the RAG Q&A model. When set, the RAG pipeline uses
    # this model for grounded answering while the LLM refiner keeps using
    # bedrock_model_id. When unset, both paths share bedrock_model_id.
    rag_model_id: str | None = None

    # --- RAG agent ---
    # Wall-clock timeout for the full agent loop (seconds). The gateway aborts
    # at 60s, so this must be lower to allow a structured fallback.
    rag_agent_timeout_s: int = 50
    # Per-call boto3 read timeout for the RAG chat model (seconds).
    #
    # Deliberately separate from llm_chunk_timeout_ms, which the correction
    # refiner uses. That path sends a few hundred words and returns quickly;
    # the RAG chat model generates a long cited answer from up to 10 retrieved
    # chunks and routinely needs far longer. Reusing the 15s refiner value gave
    # the model 15s per attempt with one retry on top, so any answer needing
    # more than 15s failed at roughly 25s no matter how much wall-clock budget
    # the agent still had.
    rag_model_timeout_s: int = 45
    # Embedding batch size for ingestion (Titan supports up to 25 texts)
    rag_embedding_batch_size: int = 10
    # Retry attempts for failed embeddings during ingestion
    rag_embedding_max_retries: int = 2
    # Per-statement timeout for RAG retrieval queries (milliseconds).
    # The default db_statement_timeout_ms (15s) is tuned for simple correction
    # queries. Hybrid retrieval involves multiple JOINs, cosine distance,
    # tsvector ranking, and a latest-version subquery that grows with the
    # corpus. This higher limit applies only to retrieval sessions via
    # SET LOCAL so it cannot affect other query paths.
    rag_query_timeout_ms: int = 30000

    # --- LLM Refiner ---
    llm_enabled: bool = True
    llm_chunk_size: int = 300
    llm_max_parallel: int = 3
    llm_chunk_timeout_ms: int = 15000
    # Only 'dataset_store' is currently active; the refiner always uses this value
    llm_retrieval_mode: str = "dataset_store"
    llm_max_prompt_records: int = 50

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
            "RAG_AGENT_TIMEOUT_S": 50,
            "RAG_MODEL_TIMEOUT_S": 45,
            "RAG_EMBEDDING_BATCH_SIZE": 10,
            "RAG_EMBEDDING_MAX_RETRIES": 2,
            "RAG_QUERY_TIMEOUT_MS": 30000,
            "DB_POOL_SIZE": 5,
            "DB_MAX_OVERFLOW": 5,
            "DB_POOL_RECYCLE_SECONDS": 1800,
            "DB_POOL_TIMEOUT_SECONDS": 10,
            "DB_CONNECT_TIMEOUT_SECONDS": 10,
            "DB_STATEMENT_TIMEOUT_MS": 15000,
            "HISTORY_QUEUE_SIZE": 1000,
            "HISTORY_RETENTION_DAYS": 90,
            "HISTORY_RETENTION_INTERVAL_SECONDS": 86400,
            # Correction precision gating (Req 12)
            "MIN_PHONETIC_KEY_LENGTH": 4,
            "COMPONENT_MATCH_MIN_LENGTH": 6,
            "MAX_CANDIDATES_PER_SPAN": 200,
            "CONTEXT_WINDOW_WORDS": 40,
        }
        numeric_float_fields: dict[str, float] = {
            "MIN_CONFIDENCE": 0.75,
            "WORD_ACCEPT_THRESHOLD": 0.90,
            "FUZZY_SCORE_CUTOFF": 0.70,
            # Correction precision gating (Req 12)
            "HIGH_CONFIDENCE_THRESHOLD": 0.90,
            "LEXICON_OVERRIDE_THRESHOLD": 0.60,
            "MAX_RELATIVE_DISTANCE": 0.25,
            "MIN_PHONETIC_SIMILARITY": 0.60,
            "OUT_OF_SCOPE_PENALTY": 0.10,
            # Per-provider gate profiles (spec task 2.12.2). deepgram MUST match
            # the task 1.1 defaults above; khaya/hybrid share those defaults and
            # differ only in the (non-env) Unknown-confidence policy.
            "PROVIDER_DEEPGRAM_HIGH_CONFIDENCE_THRESHOLD": 0.90,
            "PROVIDER_DEEPGRAM_LEXICON_OVERRIDE_THRESHOLD": 0.60,
            "PROVIDER_DEEPGRAM_MIN_PHONETIC_SIMILARITY": 0.60,
            "PROVIDER_DEEPGRAM_MAX_RELATIVE_DISTANCE": 0.25,
            "PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD": 0.90,
            "PROVIDER_KHAYA_LEXICON_OVERRIDE_THRESHOLD": 0.60,
            "PROVIDER_KHAYA_MIN_PHONETIC_SIMILARITY": 0.60,
            "PROVIDER_KHAYA_MAX_RELATIVE_DISTANCE": 0.25,
            "PROVIDER_HYBRID_HIGH_CONFIDENCE_THRESHOLD": 0.90,
            "PROVIDER_HYBRID_LEXICON_OVERRIDE_THRESHOLD": 0.60,
            "PROVIDER_HYBRID_MIN_PHONETIC_SIMILARITY": 0.60,
            "PROVIDER_HYBRID_MAX_RELATIVE_DISTANCE": 0.25,
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


# Valid ranges for correction precision gating tunables (Req 12.11), keyed by
# the Settings field name and paired with the SCREAMING_SNAKE_CASE variable
# name used in the warning. Each entry is (env_name, lower_bound, upper_bound).
_GATING_RANGES: dict[str, tuple[str, float, float]] = {
    "high_confidence_threshold": ("HIGH_CONFIDENCE_THRESHOLD", 0.0, 1.0),
    "lexicon_override_threshold": ("LEXICON_OVERRIDE_THRESHOLD", 0.0, 1.0),
    "max_relative_distance": ("MAX_RELATIVE_DISTANCE", 0.0, 1.0),
    "min_phonetic_similarity": ("MIN_PHONETIC_SIMILARITY", 0.0, 1.0),
    "out_of_scope_penalty": ("OUT_OF_SCOPE_PENALTY", 0.0, 1.0),
    "min_phonetic_key_length": ("MIN_PHONETIC_KEY_LENGTH", 1, 12),
    "component_match_min_length": ("COMPONENT_MATCH_MIN_LENGTH", 1, 20),
    "max_candidates_per_span": ("MAX_CANDIDATES_PER_SPAN", 1, 5000),
    "context_window_words": ("CONTEXT_WINDOW_WORDS", 1, 500),
}


def _build_provider_profile_ranges() -> dict[str, tuple[str, float, float]]:
    """Ranges for the per-provider gate-profile env vars (spec task 2.12.2).

    Each per-provider fractional value shares the [0.0, 1.0] range already
    declared for the corresponding task 1.1 parameter (Req 12.11). Built from
    ``SUPPORTED_PROVIDERS`` × ``PROFILE_FLOAT_PARAMS`` so the set of clamped
    variables always matches the resolved profile fields, keyed by the Settings
    field name and paired with its SCREAMING_SNAKE_CASE env var.
    """
    lower, upper = PROFILE_FLOAT_RANGE
    ranges: dict[str, tuple[str, float, float]] = {}
    for provider in SUPPORTED_PROVIDERS:
        for param in PROFILE_FLOAT_PARAMS:
            field_name = f"provider_{provider}_{param}"
            ranges[field_name] = (provider_env_var(provider, param), lower, upper)
    return ranges


# Per-provider gate-profile ranges, merged into the clamping pass (Req 12.8,
# 12.11). Declared separately from ``_GATING_RANGES`` for clarity but clamped by
# the same ``clamp_ranges`` loop.
_PROVIDER_PROFILE_RANGES: dict[str, tuple[str, float, float]] = _build_provider_profile_ranges()


def clamp_ranges(settings: Settings) -> Settings:
    """Clamp out-of-range gating tunables to the nearest bound.

    Runs once at startup. For every correction precision gating tunable whose
    resolved value falls outside its documented range (Req 12.11), the value is
    clamped to the nearest bound and one ``config.invalid_value`` warning is
    logged naming the offending variable (Req 12.8). In-range values are left
    untouched and log nothing. Mutates and returns *settings*.

    The pass also covers the per-provider gate-profile values (spec task
    2.12.2): each is clamped to the nearest bound of the [0.0, 1.0] range
    declared for its parameter, with exactly one warning per offending
    variable, whether or not ``provider_profiles_enabled`` is set — resolution
    happens once at startup regardless of the flag.
    """
    all_ranges = {**_GATING_RANGES, **_PROVIDER_PROFILE_RANGES}
    for field_name, (env_name, lower, upper) in all_ranges.items():
        value = getattr(settings, field_name)
        if value < lower:
            clamped: float | int = lower
        elif value > upper:
            clamped = upper
        else:
            continue
        # Preserve int type for whole-number fields.
        if isinstance(value, int) and not isinstance(value, bool):
            clamped = int(clamped)
        logger.warning(
            "config.invalid_value",
            variable=env_name,
            raw=value,
            clamped=clamped,
        )
        setattr(settings, field_name, clamped)
    return settings


def provider_profiles(settings: Settings) -> dict[str, ProviderGateProfile]:
    """Resolve the per-provider gate profiles from *settings* (spec task 2.12.2).

    Returns a mapping from each supported provider (``deepgram``, ``khaya``,
    ``hybrid``) to its resolved :class:`ProviderGateProfile`. Called once at
    startup after :func:`clamp_ranges`, so the values it reads are already
    parsed and range-clamped and held unchanged for the process lifetime
    (Req 12.12).

    Baseline equivalence (Req 12.9): when ``provider_profiles_enabled`` is
    ``False`` (the default), every provider resolves to the ``deepgram``
    profile — the task 1.1 defaults with the Req 2.6 Unknown-confidence policy —
    so the flag-off path is byte-for-byte the current production behaviour and
    task 2.12.3's gate branch sees the same thresholds for every request.

    When the flag is enabled, each provider's four fractional thresholds are
    read from its own ``PROVIDER_<PROVIDER>_<PARAM>`` value on *settings*, and
    the Unknown-confidence Lexicon_Gate policy is the calibrated per-provider
    constant from task 2.12.1 (``deepgram`` rejects, ``khaya``/``hybrid`` do
    not). The policy value only takes effect once task 2.12.3 wires the gate
    branch.
    """
    deepgram = default_profile("deepgram")
    if not settings.provider_profiles_enabled:
        return {provider: deepgram for provider in SUPPORTED_PROVIDERS}

    profiles: dict[str, ProviderGateProfile] = {}
    for provider in SUPPORTED_PROVIDERS:
        base = default_profile(provider)
        overrides = {
            param: getattr(settings, f"provider_{provider}_{param}")
            for param in PROFILE_FLOAT_PARAMS
        }
        profiles[provider] = ProviderGateProfile(
            lexicon_gate_reject_unknown=base.lexicon_gate_reject_unknown,
            **overrides,
        )
    return profiles


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
    clamp_ranges(settings)
    validate_required(settings)
    return settings
