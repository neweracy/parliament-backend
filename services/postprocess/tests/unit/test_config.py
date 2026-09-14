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
