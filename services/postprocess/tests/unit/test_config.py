"""Unit tests for app/config.py — configuration parsing and validation."""

from __future__ import annotations

import pytest

from app.config import (
    Settings,
    clamp_ranges,
    provider_profiles,
    validate_required,
)
from app.correction.provider_profiles import SUPPORTED_PROVIDERS, default_profile


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove all config-related env vars before each test."""
    config_vars = [
        "PORT", "HOST", "UVICORN_WORKERS", "DRAIN_TIMEOUT_SECONDS",
        "SERVICE_TOKEN", "DATABASE_URL",
        "DATASET_REFRESH_SECONDS", "DATASET_LOAD_RETRY_SECONDS",
        "MIN_CONFIDENCE", "WORD_ACCEPT_THRESHOLD", "FUZZY_SCORE_CUTOFF",
        "MIN_CANDIDATE_LENGTH", "AWS_REGION", "BEDROCK_MODEL_ID", "RAG_MODEL_ID",
        "LLM_ENABLED", "LLM_CHUNK_SIZE", "LLM_MAX_PARALLEL",
        "LLM_CHUNK_TIMEOUT_MS", "LLM_RETRIEVAL_MODE",
        "LLM_MAX_PROMPT_RECORDS",
        "HISTORY_ENABLED", "LOG_LEVEL", "POSTPROCESS_MODE",
        # Per-provider gate profiles (spec task 2.12.2)
        "PROVIDER_PROFILES_ENABLED",
        "PROVIDER_DEEPGRAM_HIGH_CONFIDENCE_THRESHOLD",
        "PROVIDER_DEEPGRAM_LEXICON_OVERRIDE_THRESHOLD",
        "PROVIDER_DEEPGRAM_MIN_PHONETIC_SIMILARITY",
        "PROVIDER_DEEPGRAM_MAX_RELATIVE_DISTANCE",
        "PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD",
        "PROVIDER_KHAYA_LEXICON_OVERRIDE_THRESHOLD",
        "PROVIDER_KHAYA_MIN_PHONETIC_SIMILARITY",
        "PROVIDER_KHAYA_MAX_RELATIVE_DISTANCE",
        "PROVIDER_HYBRID_HIGH_CONFIDENCE_THRESHOLD",
        "PROVIDER_HYBRID_LEXICON_OVERRIDE_THRESHOLD",
        "PROVIDER_HYBRID_MIN_PHONETIC_SIMILARITY",
        "PROVIDER_HYBRID_MAX_RELATIVE_DISTANCE",
    ]
    for var in config_vars:
        monkeypatch.delenv(var, raising=False)


class TestDefaults:
    """Absent optional variables use the documented default."""

    def test_port_defaults_to_8082(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.port == 8082

    def test_dataset_refresh_defaults_to_300(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.dataset_refresh_seconds == 300

    def test_min_confidence_defaults_to_075(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.min_confidence == 0.75

    def test_word_accept_threshold_defaults_to_090(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.word_accept_threshold == 0.90

    def test_fuzzy_score_cutoff_defaults_to_070(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.fuzzy_score_cutoff == 0.70

    def test_llm_chunk_size_defaults_to_300(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.llm_chunk_size == 300

    def test_drain_timeout_defaults_to_15(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.drain_timeout_seconds == 15


class TestInvalidNumericFallback:
    """Non-numeric values fall back to default and warn."""

    def test_invalid_port_falls_back(self, monkeypatch):
        monkeypatch.setenv("PORT", "notanumber")
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.port == 8082

    def test_invalid_min_confidence_falls_back(self, monkeypatch):
        monkeypatch.setenv("MIN_CONFIDENCE", "abc")
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.min_confidence == 0.75

    def test_invalid_dataset_refresh_falls_back(self, monkeypatch):
        monkeypatch.setenv("DATASET_REFRESH_SECONDS", "xyz")
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.dataset_refresh_seconds == 300

    def test_invalid_llm_chunk_timeout_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_CHUNK_TIMEOUT_MS", "slow")
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.llm_chunk_timeout_ms == 15000

    def test_invalid_fuzzy_score_cutoff_falls_back(self, monkeypatch):
        monkeypatch.setenv("FUZZY_SCORE_CUTOFF", "high")
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.fuzzy_score_cutoff == 0.70


class TestMissingRequired:
    """Missing required variables exit non-zero."""

    def test_missing_service_token_exits(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        with pytest.raises(SystemExit) as exc_info:
            validate_required(s)
        assert exc_info.value.code == 1

    def test_missing_database_url_exits(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        s = Settings()
        with pytest.raises(SystemExit) as exc_info:
            validate_required(s)
        assert exc_info.value.code == 1

    def test_missing_both_exits(self):
        s = Settings()
        with pytest.raises(SystemExit) as exc_info:
            validate_required(s)
        assert exc_info.value.code == 1

    def test_both_present_does_not_exit(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        # Should not raise
        validate_required(s)


class TestSecretInjection:
    """Secrets are read from environment only, never from committed files."""

    def test_service_token_from_env(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "my-secret-token")
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        s = Settings()
        assert s.service_token == "my-secret-token"

    def test_database_url_from_env(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "tok")
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        s = Settings()
        assert s.database_url == "postgresql://user:pass@host/db"

    def test_no_env_file_loaded(self):
        """Settings.model_config env_file is None — no .env file is auto-read."""
        assert Settings.model_config.get("env_file") is None


def _secrets(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "tok")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")


class TestProviderProfileDefaults:
    """Per-provider gate profiles resolve to safe, baseline-equivalent defaults."""

    def test_flag_defaults_off(self, monkeypatch):
        _secrets(monkeypatch)
        s = Settings()
        assert s.provider_profiles_enabled is False

    def test_deepgram_profile_equals_task_1_1_defaults(self, monkeypatch):
        # The deepgram profile MUST reproduce the task 1.1 correction gating
        # defaults so the current production path is unchanged.
        _secrets(monkeypatch)
        s = Settings()
        profiles = provider_profiles(s)
        deepgram = profiles["deepgram"]
        assert deepgram.high_confidence_threshold == 0.90
        assert deepgram.lexicon_override_threshold == 0.60
        assert deepgram.min_phonetic_similarity == 0.60
        assert deepgram.max_relative_distance == 0.25
        # deepgram keeps the Req 2.6 unconditional Unknown-confidence rejection.
        assert deepgram.lexicon_gate_reject_unknown is True

    def test_flag_off_resolves_deepgram_for_every_provider(self, monkeypatch):
        # Baseline equivalence (Req 12.9): with the flag off, khaya/hybrid
        # overrides are ignored and every provider gets the deepgram profile.
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD", "0.10")
        monkeypatch.setenv("PROVIDER_HYBRID_MAX_RELATIVE_DISTANCE", "0.99")
        s = Settings()
        profiles = provider_profiles(s)
        deepgram = default_profile("deepgram")
        for provider in SUPPORTED_PROVIDERS:
            assert profiles[provider] == deepgram

    def test_default_profile_unknown_policy_per_provider(self):
        # Task 2.12.1 decision: deepgram rejects Unknown-confidence lexicon
        # spans; khaya/hybrid do not.
        assert default_profile("deepgram").lexicon_gate_reject_unknown is True
        assert default_profile("khaya").lexicon_gate_reject_unknown is False
        assert default_profile("hybrid").lexicon_gate_reject_unknown is False


class TestProviderProfileOverrides:
    """When enabled, each provider reads its own per-provider env vars."""

    def test_flag_on_reads_khaya_overrides(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_PROFILES_ENABLED", "true")
        monkeypatch.setenv("PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD", "0.80")
        monkeypatch.setenv("PROVIDER_KHAYA_MIN_PHONETIC_SIMILARITY", "0.50")
        s = Settings()
        profiles = provider_profiles(s)
        khaya = profiles["khaya"]
        assert khaya.high_confidence_threshold == 0.80
        assert khaya.min_phonetic_similarity == 0.50
        # Unspecified params fall back to the deepgram defaults.
        assert khaya.lexicon_override_threshold == 0.60
        assert khaya.max_relative_distance == 0.25
        # Policy stays the calibrated per-provider constant.
        assert khaya.lexicon_gate_reject_unknown is False

    def test_flag_on_deepgram_still_matches_defaults(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_PROFILES_ENABLED", "true")
        s = Settings()
        profiles = provider_profiles(s)
        assert profiles["deepgram"] == default_profile("deepgram")

    def test_hybrid_overrides_isolated_from_khaya(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_PROFILES_ENABLED", "true")
        monkeypatch.setenv("PROVIDER_HYBRID_MAX_RELATIVE_DISTANCE", "0.40")
        s = Settings()
        profiles = provider_profiles(s)
        assert profiles["hybrid"].max_relative_distance == 0.40
        assert profiles["khaya"].max_relative_distance == 0.25


class TestProviderProfileInvalidFallback:
    """Unparseable per-provider values fall back to the default."""

    def test_invalid_value_falls_back(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD", "notanumber")
        s = Settings()
        assert s.provider_khaya_high_confidence_threshold == 0.90


class TestProviderProfileClamping:
    """Out-of-range per-provider values clamp to the nearest [0,1] bound."""

    def test_above_one_clamps_to_one(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD", "1.5")
        s = Settings()
        clamp_ranges(s)
        assert s.provider_khaya_high_confidence_threshold == 1.0

    def test_below_zero_clamps_to_zero(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_HYBRID_MIN_PHONETIC_SIMILARITY", "-0.3")
        s = Settings()
        clamp_ranges(s)
        assert s.provider_hybrid_min_phonetic_similarity == 0.0

    def test_in_range_untouched(self, monkeypatch):
        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_KHAYA_MAX_RELATIVE_DISTANCE", "0.42")
        s = Settings()
        clamp_ranges(s)
        assert s.provider_khaya_max_relative_distance == 0.42


# ---------------------------------------------------------------------------
# Task 1.2: gating tunables (9) + feature flags (5) — defaults, absent/empty
# env handling, invalid-parse fallback, and range clamping.
# Requirements: 12.3, 12.4, 12.5, 12.7, 12.8, 12.11
# ---------------------------------------------------------------------------

#: The nine gating tunables introduced by Req 12.1/12.3, paired with their
#: SCREAMING_SNAKE_CASE env var name and documented default (Req 12.3).
_GATING_TUNABLE_DEFAULTS: list[tuple[str, str, float | int]] = [
    ("high_confidence_threshold", "HIGH_CONFIDENCE_THRESHOLD", 0.90),
    ("lexicon_override_threshold", "LEXICON_OVERRIDE_THRESHOLD", 0.60),
    ("max_relative_distance", "MAX_RELATIVE_DISTANCE", 0.25),
    ("min_phonetic_key_length", "MIN_PHONETIC_KEY_LENGTH", 4),
    ("min_phonetic_similarity", "MIN_PHONETIC_SIMILARITY", 0.60),
    ("component_match_min_length", "COMPONENT_MATCH_MIN_LENGTH", 6),
    ("max_candidates_per_span", "MAX_CANDIDATES_PER_SPAN", 200),
    ("context_window_words", "CONTEXT_WINDOW_WORDS", 40),
    ("out_of_scope_penalty", "OUT_OF_SCOPE_PENALTY", 0.10),
]

#: The five boolean feature flags introduced by Req 12.2, paired with their env
#: var name and documented default (Req 12.4 enabled-by-default; Req 12.5
#: disabled-by-default).
_FEATURE_FLAG_DEFAULTS: list[tuple[str, str, bool]] = [
    ("lexicon_gate_enabled", "LEXICON_GATE_ENABLED", True),
    ("evidence_confidence_enabled", "EVIDENCE_CONFIDENCE_ENABLED", True),
    ("context_gate_enabled", "CONTEXT_GATE_ENABLED", True),
    ("sitting_scope_enabled", "SITTING_SCOPE_ENABLED", False),
    ("llm_veto_enabled", "LLM_VETO_ENABLED", False),
]

#: Valid range for each fractional/whole-number tunable (Req 12.11), used to
#: build an out-of-range value (below the lower bound and above the upper
#: bound) for the clamping tests.
_GATING_TUNABLE_RANGES: dict[str, tuple[float, float]] = {
    "high_confidence_threshold": (0.0, 1.0),
    "lexicon_override_threshold": (0.0, 1.0),
    "max_relative_distance": (0.0, 1.0),
    "min_phonetic_key_length": (1, 12),
    "min_phonetic_similarity": (0.0, 1.0),
    "component_match_min_length": (1, 20),
    "max_candidates_per_span": (1, 5000),
    "context_window_words": (1, 500),
    "out_of_scope_penalty": (0.0, 1.0),
}


class TestGatingTunableDefaults:
    """Absent env vars resolve every gating tunable to its documented default
    (Req 12.3)."""

    @pytest.mark.parametrize(("field_name", "env_name", "default"), _GATING_TUNABLE_DEFAULTS)
    def test_absent_env_var_resolves_default(self, monkeypatch, field_name, env_name, default):
        _secrets(monkeypatch)
        monkeypatch.delenv(env_name, raising=False)
        s = Settings()
        assert getattr(s, field_name) == default

    @pytest.mark.parametrize(("field_name", "env_name", "default"), _GATING_TUNABLE_DEFAULTS)
    def test_empty_env_var_resolves_default(self, monkeypatch, field_name, env_name, default):
        # Empty string is treated the same as absent (Req 12.3).
        _secrets(monkeypatch)
        monkeypatch.setenv(env_name, "")
        s = Settings()
        assert getattr(s, field_name) == default


class TestFeatureFlagDefaults:
    """Absent/empty env vars resolve every feature flag to its documented
    default (Req 12.4, 12.5), each flag resolved independently."""

    @pytest.mark.parametrize(("field_name", "env_name", "default"), _FEATURE_FLAG_DEFAULTS)
    def test_absent_env_var_resolves_default(self, monkeypatch, field_name, env_name, default):
        _secrets(monkeypatch)
        monkeypatch.delenv(env_name, raising=False)
        s = Settings()
        assert getattr(s, field_name) is default

    @pytest.mark.parametrize(("field_name", "env_name", "default"), _FEATURE_FLAG_DEFAULTS)
    def test_empty_env_var_resolves_default(self, monkeypatch, field_name, env_name, default):
        _secrets(monkeypatch)
        monkeypatch.setenv(env_name, "")
        s = Settings()
        assert getattr(s, field_name) is default

    def test_flags_resolved_independently(self, monkeypatch):
        # Enabling one flag must not change the resolution of the others.
        _secrets(monkeypatch)
        monkeypatch.setenv("SITTING_SCOPE_ENABLED", "true")
        s = Settings()
        assert s.sitting_scope_enabled is True
        assert s.llm_veto_enabled is False
        assert s.lexicon_gate_enabled is True
        assert s.evidence_confidence_enabled is True
        assert s.context_gate_enabled is True


class TestGatingTunableInvalidParseFallback:
    """An unparseable value retains the default and logs exactly one
    ``config.invalid_value`` warning (Req 12.7)."""

    @pytest.mark.parametrize(("field_name", "env_name", "default"), _GATING_TUNABLE_DEFAULTS)
    def test_unparseable_value_retains_default_and_warns_once(
        self, monkeypatch, field_name, env_name, default
    ):
        import structlog

        _secrets(monkeypatch)
        monkeypatch.setenv(env_name, "not-a-number")

        with structlog.testing.capture_logs() as logs:
            s = Settings()

        assert getattr(s, field_name) == default

        invalid_events = [
            e
            for e in logs
            if e.get("event") == "config.invalid_value" and e.get("variable") == env_name
        ]
        assert len(invalid_events) == 1, f"expected exactly one warning; got {logs}"
        assert invalid_events[0]["log_level"] == "warning"


class TestGatingTunableRangeClamping:
    """An out-of-range value clamps to the nearest bound and logs exactly one
    ``config.invalid_value`` warning (Req 12.8, 12.11)."""

    @pytest.mark.parametrize(("field_name", "env_name", "_default"), _GATING_TUNABLE_DEFAULTS)
    def test_below_lower_bound_clamps_and_warns_once(
        self, monkeypatch, field_name, env_name, _default
    ):
        import structlog

        _secrets(monkeypatch)
        lower, _upper = _GATING_TUNABLE_RANGES[field_name]
        # A value clearly below the lower bound for every tunable in this set.
        below = lower - 1
        monkeypatch.setenv(env_name, str(below))

        s = Settings()
        with structlog.testing.capture_logs() as logs:
            clamp_ranges(s)

        assert getattr(s, field_name) == lower

        invalid_events = [
            e
            for e in logs
            if e.get("event") == "config.invalid_value" and e.get("variable") == env_name
        ]
        assert len(invalid_events) == 1, f"expected exactly one warning; got {logs}"
        assert invalid_events[0]["log_level"] == "warning"

    @pytest.mark.parametrize(("field_name", "env_name", "_default"), _GATING_TUNABLE_DEFAULTS)
    def test_above_upper_bound_clamps_and_warns_once(
        self, monkeypatch, field_name, env_name, _default
    ):
        import structlog

        _secrets(monkeypatch)
        _lower, upper = _GATING_TUNABLE_RANGES[field_name]
        # A value clearly above the upper bound for every tunable in this set.
        above = upper + 1
        monkeypatch.setenv(env_name, str(above))

        s = Settings()
        with structlog.testing.capture_logs() as logs:
            clamp_ranges(s)

        assert getattr(s, field_name) == upper

        invalid_events = [
            e
            for e in logs
            if e.get("event") == "config.invalid_value" and e.get("variable") == env_name
        ]
        assert len(invalid_events) == 1, f"expected exactly one warning; got {logs}"
        assert invalid_events[0]["log_level"] == "warning"

    def test_in_range_value_is_untouched_and_warns_never(self, monkeypatch):
        import structlog

        _secrets(monkeypatch)
        monkeypatch.setenv("HIGH_CONFIDENCE_THRESHOLD", "0.85")

        s = Settings()
        with structlog.testing.capture_logs() as logs:
            clamp_ranges(s)

        assert s.high_confidence_threshold == 0.85
        invalid_events = [e for e in logs if e.get("event") == "config.invalid_value"]
        assert invalid_events == []


class TestProviderProfileConfigLoggingAndDefaults:
    """Same invalid-parse / clamping pattern extended to the per-provider gate
    profile settings (spec task 2.12.2), since ``clamp_ranges`` covers those
    values too (Req 12.8, 12.11)."""

    def test_unparseable_provider_value_retains_default_and_warns_once(self, monkeypatch):
        import structlog

        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD", "not-a-number")

        with structlog.testing.capture_logs() as logs:
            s = Settings()

        assert s.provider_khaya_high_confidence_threshold == 0.90

        invalid_events = [
            e
            for e in logs
            if e.get("event") == "config.invalid_value"
            and e.get("variable") == "PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD"
        ]
        assert len(invalid_events) == 1, f"expected exactly one warning; got {logs}"
        assert invalid_events[0]["log_level"] == "warning"

    def test_out_of_range_provider_value_clamps_and_warns_once(self, monkeypatch):
        import structlog

        _secrets(monkeypatch)
        monkeypatch.setenv("PROVIDER_HYBRID_MAX_RELATIVE_DISTANCE", "3.5")

        s = Settings()
        with structlog.testing.capture_logs() as logs:
            clamp_ranges(s)

        assert s.provider_hybrid_max_relative_distance == 1.0

        invalid_events = [
            e
            for e in logs
            if e.get("event") == "config.invalid_value"
            and e.get("variable") == "PROVIDER_HYBRID_MAX_RELATIVE_DISTANCE"
        ]
        assert len(invalid_events) == 1, f"expected exactly one warning; got {logs}"
        assert invalid_events[0]["log_level"] == "warning"
