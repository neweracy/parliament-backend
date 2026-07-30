"""Unit tests for request logging middleware.

Verifies that the RequestLoggingMiddleware calls log_request at the end of
every request with the correlation id, path, method, HTTP status, and latency.

Requirements: 13.2, 17.1
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex
from app.deps import _get_settings
from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_TOKEN = "test-secret-token-middleware"


def _test_settings() -> Settings:
    return Settings(
        service_token=TEST_TOKEN,
        database_url="postgresql://test:test@localhost:5432/test",
        llm_enabled=False,
    )


def _make_empty_index() -> MatchIndex:
    return MatchIndex(
        canonical_map={},
        fused_map={},
        phonetic_map={},
        surname_map={},
        initial_surname_map={},
        entity_kind_map={},
        entity_type_map={},
        party_abbr_map={},
        alias_ordinal={},
        length_buckets={},
        bk_tree=None,
    )


def _make_snapshot() -> DatasetSnapshot:
    return DatasetSnapshot(
        version="2026-07-09T10:00:00+00:00",
        records=(),
        record_count=42,
        loaded_at=datetime(2026, 7, 9, 10, 0, 0, tzinfo=timezone.utc),
        index=_make_empty_index(),
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )


@pytest.fixture()
def client() -> TestClient:
    """TestClient with a loaded dataset cache."""
    app.dependency_overrides[_get_settings] = _test_settings

    mock_cache = MagicMock(spec=DatasetCache)
    mock_cache.get_snapshot.return_value = _make_snapshot()
    app.state.dataset_cache = mock_cache
    app.state.settings = _test_settings()
    app.state.start_time = 1000000.0
    app.state.bedrock_client = None

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture()
def client_no_cache() -> TestClient:
    """TestClient with no dataset loaded (snapshot=None)."""
    app.dependency_overrides[_get_settings] = _test_settings

    mock_cache = MagicMock(spec=DatasetCache)
    mock_cache.get_snapshot.return_value = None
    app.state.dataset_cache = mock_cache
    app.state.settings = _test_settings()
    app.state.start_time = 1000000.0
    app.state.bedrock_client = None

    yield TestClient(app)

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRequestLoggingMiddleware:
    """Middleware calls log_request at the end of every request."""

    @patch("app.middleware.log_request")
    def test_health_endpoint_logs_request(self, mock_log: MagicMock, client: TestClient):
        """GET /health logs with correct path, method, and 200 status."""
        resp = client.get("/health")

        assert resp.status_code == 200
        mock_log.assert_called_once()

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["path"] == "/health"
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["status"] == 200
        assert call_kwargs["latency_ms"] >= 0
        # correlation_id should be a non-empty string
        assert isinstance(call_kwargs["correlation_id"], str)
        assert len(call_kwargs["correlation_id"]) > 0

    @patch("app.middleware.log_request")
    def test_503_before_cache_loaded(self, mock_log: MagicMock, client_no_cache: TestClient):
        """A 503 response still triggers log_request with status 503."""
        resp = client_no_cache.get("/health")

        assert resp.status_code == 503
        mock_log.assert_called_once()

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["status"] == 503
        assert call_kwargs["path"] == "/health"
        assert call_kwargs["method"] == "GET"

    @patch("app.middleware.log_request")
    def test_correlation_id_from_request_header(self, mock_log: MagicMock, client: TestClient):
        """When X-Correlation-Id is provided, it appears in the log."""
        cid = "my-trace-id-123"
        resp = client.get("/health", headers={"X-Correlation-Id": cid})

        assert resp.status_code == 200
        mock_log.assert_called_once()

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["correlation_id"] == cid

    @patch("app.middleware.log_request")
    def test_postprocess_endpoint_logs_request(self, mock_log: MagicMock, client: TestClient):
        """POST /v1/postprocess logs with correct path, method, and status."""
        body = {"transcript": "hello world", "words": []}
        cid = "corr-abc-123"
        resp = client.post(
            "/v1/postprocess",
            json=body,
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": cid,
            },
        )

        assert resp.status_code == 200
        mock_log.assert_called_once()

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["path"] == "/v1/postprocess"
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["status"] == 200
        assert call_kwargs["correlation_id"] == cid

    @patch("app.middleware.log_request")
    def test_auth_failure_logs_request(self, mock_log: MagicMock, client: TestClient):
        """A 401 auth failure still triggers log_request with status 401."""
        body = {"transcript": "hello", "words": []}
        resp = client.post("/v1/postprocess", json=body)

        assert resp.status_code == 401
        mock_log.assert_called_once()

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["status"] == 401
        assert call_kwargs["path"] == "/v1/postprocess"
        assert call_kwargs["method"] == "POST"

    @patch("app.middleware.log_request")
    def test_latency_is_positive(self, mock_log: MagicMock, client: TestClient):
        """Latency measurement is a positive number in milliseconds."""
        client.get("/health")

        call_kwargs = mock_log.call_args.kwargs
        assert isinstance(call_kwargs["latency_ms"], float)
        assert call_kwargs["latency_ms"] > 0

    @patch("app.middleware.log_request")
    def test_generated_correlation_id_is_uuid(self, mock_log: MagicMock, client: TestClient):
        """When no X-Correlation-Id header is sent, a UUID is generated."""
        # Health endpoint doesn't use the get_correlation_id dependency,
        # so no header is set on the response — middleware generates one.
        client.get("/health")

        call_kwargs = mock_log.call_args.kwargs
        cid = call_kwargs["correlation_id"]
        # Should look like a UUID (8-4-4-4-12)
        parts = cid.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
