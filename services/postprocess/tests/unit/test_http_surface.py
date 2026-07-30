"""Unit tests for the HTTP surface — routes, auth, error envelopes, and headers.

Uses FastAPI's TestClient to exercise the full route layer with dependency
overrides for settings and a mock DatasetCache on app.state.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 7.13, 15.1
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

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

TEST_TOKEN = "test-secret-token-123"


def _test_settings() -> Settings:
    """Return a Settings instance suitable for testing."""
    return Settings(
        service_token=TEST_TOKEN,
        database_url="postgresql://test:test@localhost:5432/test",
        llm_enabled=False,
    )


def _make_empty_index() -> MatchIndex:
    """Create a minimal empty MatchIndex."""
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
    """Create a minimal loaded DatasetSnapshot."""
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
def client_no_cache() -> TestClient:
    """TestClient with no dataset loaded (snapshot=None)."""
    app.dependency_overrides[_get_settings] = _test_settings

    # Mock dataset_cache with no snapshot
    mock_cache = MagicMock(spec=DatasetCache)
    mock_cache.get_snapshot.return_value = None
    app.state.dataset_cache = mock_cache
    app.state.settings = _test_settings()
    app.state.start_time = time.time()

    client = TestClient(app, raise_server_exceptions=False)
    yield client

    app.dependency_overrides.clear()


@pytest.fixture()
def client_with_cache() -> TestClient:
    """TestClient with a loaded dataset (valid snapshot)."""
    app.dependency_overrides[_get_settings] = _test_settings

    # Mock dataset_cache with a real snapshot
    mock_cache = MagicMock(spec=DatasetCache)
    mock_cache.get_snapshot.return_value = _make_snapshot()
    app.state.dataset_cache = mock_cache
    app.state.settings = _test_settings()
    app.state.start_time = time.time()

    client = TestClient(app, raise_server_exceptions=False)
    yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. 401 without a token
# ---------------------------------------------------------------------------


class TestAuth401:
    """POST /v1/postprocess returns 401 without Authorization header."""

    def test_missing_authorization_header(self, client_with_cache: TestClient):
        """Missing Authorization header returns 401 with MISSING_TOKEN."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        assert body["error"]["type"] == "AuthenticationError"
        assert body["error"]["code"] == "MISSING_TOKEN"
        assert "message" in body["error"]

    def test_invalid_token_format(self, client_with_cache: TestClient):
        """Malformed Authorization header returns 401."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": "BadFormat"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "INVALID_TOKEN"

    def test_wrong_token(self, client_with_cache: TestClient):
        """Wrong bearer token returns 401 INVALID_TOKEN."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# 2. 422 on structurally invalid body
# ---------------------------------------------------------------------------


class TestValidation422:
    """POST /v1/postprocess returns 422 on invalid request bodies."""

    def test_missing_transcript_field(self, client_with_cache: TestClient):
        """Missing required 'transcript' field returns 422 INVALID_REQUEST."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"words": []},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body
        assert body["error"]["type"] == "ValidationError"
        assert body["error"]["code"] == "INVALID_REQUEST"
        assert "message" in body["error"]

    def test_invalid_json_body(self, client_with_cache: TestClient):
        """Non-JSON body returns 422."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            content=b"not json at all",
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 422

    def test_wrong_type_for_words(self, client_with_cache: TestClient):
        """Words field with wrong type returns 422."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": "not a list"},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["type"] == "ValidationError"
        assert body["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 3. 503 from /health before the first load
# ---------------------------------------------------------------------------


class TestHealth503:
    """GET /health returns 503 when no snapshot is loaded."""

    def test_health_returns_503_before_load(self, client_no_cache: TestClient):
        """Health endpoint returns 503 when Dataset_Cache has no snapshot."""
        resp = client_no_cache.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert "error" in body
        assert body["error"]["type"] == "ServiceUnavailable"
        assert body["error"]["code"] == "DATASET_NOT_LOADED"
        assert "message" in body["error"]


# ---------------------------------------------------------------------------
# 4. 200 with an empty words list
# ---------------------------------------------------------------------------


class TestEmptyWordsList200:
    """POST /v1/postprocess with empty words returns 200."""

    def test_empty_words_returns_200(self, client_with_cache: TestClient):
        """An empty words list is valid and returns a CorrectionResponse."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "transcript" in body
        assert body["transcript"] == "hello"
        assert "words" in body
        assert isinstance(body["words"], list)
        assert "entities" in body
        assert "metadata" in body

    def test_transcript_only_returns_200(self, client_with_cache: TestClient):
        """Transcript-only request (words defaults to []) returns 200."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "some text"},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript"] == "some text"
        assert body["words"] == []


# ---------------------------------------------------------------------------
# 5. /openapi.json describes schemas
# ---------------------------------------------------------------------------


class TestOpenAPISchema:
    """/openapi.json describes the request, response, and error schemas."""

    def test_openapi_returns_200(self, client_with_cache: TestClient):
        """OpenAPI document is accessible."""
        resp = client_with_cache.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "openapi" in schema
        assert "paths" in schema

    def test_openapi_describes_postprocess_endpoint(self, client_with_cache: TestClient):
        """OpenAPI doc describes the /v1/postprocess path."""
        resp = client_with_cache.get("/openapi.json")
        schema = resp.json()
        assert "/v1/postprocess" in schema["paths"]
        post_op = schema["paths"]["/v1/postprocess"]["post"]
        assert "requestBody" in post_op

    def test_openapi_has_correction_request_schema(self, client_with_cache: TestClient):
        """OpenAPI doc defines the CorrectionRequest schema."""
        resp = client_with_cache.get("/openapi.json")
        schema = resp.json()
        schemas = schema.get("components", {}).get("schemas", {})
        assert "CorrectionRequest" in schemas

    def test_openapi_has_error_response(self, client_with_cache: TestClient):
        """OpenAPI doc describes 422 error responses."""
        resp = client_with_cache.get("/openapi.json")
        schema = resp.json()
        post_op = schema["paths"]["/v1/postprocess"]["post"]
        assert "responses" in post_op
        assert "422" in post_op["responses"]


# ---------------------------------------------------------------------------
# 6. 200 from /health when loaded
# ---------------------------------------------------------------------------


class TestHealthLoaded200:
    """GET /health returns 200 with metadata when cache is loaded."""

    def test_health_returns_200_when_loaded(self, client_with_cache: TestClient):
        """Health endpoint returns 200 with correct metadata."""
        resp = client_with_cache.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "record_count" in body
        assert body["record_count"] == 42
        assert "loaded_at" in body
        assert "version" in body
        assert "uptime_seconds" in body
        assert body["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# 7. 503 from /v1/postprocess when cache not loaded
# ---------------------------------------------------------------------------


class TestPostprocess503:
    """POST /v1/postprocess returns 503 when cache is not loaded."""

    def test_returns_503_when_no_snapshot(self, client_no_cache: TestClient):
        """Valid auth but no snapshot yields 503."""
        resp = client_no_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["type"] == "ServiceUnavailable"
        assert body["error"]["code"] == "DATASET_NOT_LOADED"


# ---------------------------------------------------------------------------
# 8. X-Correlation-Id echoed
# ---------------------------------------------------------------------------


class TestCorrelationIdHeader:
    """Response echoes the X-Correlation-Id header."""

    def test_echoes_provided_correlation_id(self, client_with_cache: TestClient):
        """When client sends X-Correlation-Id, it's echoed in response."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": "test-corr-id-abc",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-correlation-id") == "test-corr-id-abc"

    def test_generates_correlation_id_when_absent(self, client_with_cache: TestClient):
        """When no X-Correlation-Id sent, one is generated in response."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        corr_id = resp.headers.get("x-correlation-id")
        assert corr_id is not None
        assert len(corr_id) > 0


# ---------------------------------------------------------------------------
# 9. X-Postprocess-Stage set
# ---------------------------------------------------------------------------


class TestPostprocessStageHeader:
    """Response includes the X-Postprocess-Stage header."""

    def test_stage_complete_on_success(self, client_with_cache: TestClient):
        """Successful processing sets X-Postprocess-Stage to 'complete'."""
        resp = client_with_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-postprocess-stage") == "complete"

    def test_stage_none_on_503(self, client_no_cache: TestClient):
        """503 (not loaded) sets X-Postprocess-Stage to 'none'."""
        resp = client_no_cache.post(
            "/v1/postprocess",
            json={"transcript": "hello", "words": []},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 503
        assert resp.headers.get("x-postprocess-stage") == "none"
