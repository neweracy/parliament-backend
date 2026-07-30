"""Unit tests for app/config.py — configuration parsing and validation."""

from __future__ import annotations

import pytest

from app.config import Settings, validate_required


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove all config-related env vars before each test."""
    config_vars = [
        "PORT", "HOST", "UVICORN_WORKERS", "DRAIN_TIMEOUT_SECONDS",
        "SERVICE_TOKEN", "DATABASE_URL",
        "DATASET_REFRESH_SECONDS", "DATASET_LOAD_RETRY_SECONDS",
        "MIN_CONFIDENCE", "WORD_ACCEPT_THRESHOLD", "FUZZY_SCORE_CUTOFF",
        "MIN_CANDIDATE_LENGTH", "AWS_REGION", "BEDROCK_MODEL_ID",
        "LLM_ENABLED", "LLM_CHUNK_SIZE", "LLM_MAX_PARALLEL",
        "LLM_CHUNK_TIMEOUT_MS", "LLM_RETRIEVAL_MODE",
        "LLM_MAX_PROMPT_RECORDS", "KNOWLEDGE_BASE_ID",
        "HISTORY_ENABLED", "LOG_LEVEL", "POSTPROCESS_MODE",
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
