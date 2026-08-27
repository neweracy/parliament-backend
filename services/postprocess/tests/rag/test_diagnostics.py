"""Tests for the RAG diagnostics endpoint.

Verifies the diagnostic health check reports correct status for each
pipeline component under various failure conditions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.deps import verify_service_token
from app.rag.diagnostics import router


def _verify_token_ok() -> None:
    """No-op override that always passes auth."""


def _verify_token_reject() -> None:
    """Override that always rejects auth (simulates missing/wrong token)."""
    raise HTTPException(
        status_code=401,
        detail={"error": {"type": "AuthenticationError", "code": "INVALID_TOKEN"}},
    )


def _make_app(
    *,
    session_factory=None,
    chat_model=None,
    embeddings=None,
    settings=None,
    auth_override=None,
) -> FastAPI:
    """Build a minimal FastAPI app with the diagnostics router.

    By default, overrides verify_service_token to skip real config lookup.
    Pass ``auth_override=_verify_token_reject`` to simulate auth failure.
    """
    app = FastAPI()
    app.include_router(router)

    # Override the auth dependency to avoid needing real env vars
    if auth_override is None:
        auth_override = _verify_token_ok
    app.dependency_overrides[verify_service_token] = auth_override

    state = app.state
    state.session_factory = session_factory
    state.chat_model = chat_model
    state.embeddings = embeddings

    if settings is None:
        settings = MagicMock()
        settings.service_token = "test-token"
        settings.bedrock_model_id = "test-model"
        settings.rag_agent_timeout_s = 50
        settings.rag_model_timeout_s = 45
    state.settings = settings

    return app


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _make_breaker_mock(*, state: str = "closed", failures: int = 0) -> MagicMock:
    """Build a mock circuit breaker with the given state."""
    breaker = MagicMock()
    breaker.state.value = state
    breaker._failure_count = failures
    breaker._failure_threshold = 5
    breaker._recovery_timeout_s = 60.0
    return breaker


class TestDiagnosticsAuth:
    """Authentication checks."""

    def test_rejects_unauthenticated(self) -> None:
        """Endpoint returns 401 without auth header."""
        app = _make_app(auth_override=_verify_token_reject)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics")
        assert resp.status_code == 401

    def test_rejects_wrong_token(self) -> None:
        """Endpoint returns 401 with wrong token."""
        app = _make_app(auth_override=_verify_token_reject)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/rag/diagnostics",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401


class TestDiagnosticsComponents:
    """Component-level checks."""

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_reports_degraded_without_session_factory(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """Database check fails when session_factory is None."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"]["status"] == "error"

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_reports_chat_model_unavailable(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """Chat model check reports unavailable when None."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app(chat_model=None)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["chat_model"]["status"] == "unavailable"
        assert body["checks"]["chat_model"]["configured"] is False

    @patch("app.rag.diagnostics.probe_credentials", return_value=True)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_reports_aws_credentials_ok(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """AWS credentials check reports ok when found."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["aws_credentials"]["status"] == "ok"
        assert body["checks"]["aws_credentials"]["resolved"] is True

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_reports_aws_credentials_missing(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """AWS credentials check reports error when absent."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["aws_credentials"]["status"] == "error"

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_reports_embeddings_unavailable(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """Embeddings client check reports unavailable when None."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app(embeddings=None)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["embeddings_client"]["status"] == "unavailable"
        assert body["checks"]["embedding_api"]["status"] == "skipped"

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_reports_timeout_config(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """Timeout configuration is always present."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert "timeouts" in body["checks"]
        assert body["checks"]["timeouts"]["rag_agent_timeout_s"] == 50

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_includes_diagnostics_latency(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """Response includes latency measurement."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert "diagnostics_latency_ms" in body
        assert body["diagnostics_latency_ms"] >= 0

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_circuit_breaker_open_reports_degraded(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """When circuit breaker is open, check reports degraded."""
        mock_breaker.state.value = "open"
        mock_breaker._failure_count = 5
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["circuit_breaker"]["status"] == "degraded"
        assert "error" in body["checks"]["circuit_breaker"]

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker")
    def test_circuit_breaker_closed_reports_ok(
        self, mock_breaker: MagicMock, _mock_creds: MagicMock
    ) -> None:
        """When circuit breaker is closed, check reports ok."""
        mock_breaker.state.value = "closed"
        mock_breaker._failure_count = 0
        mock_breaker._failure_threshold = 5
        mock_breaker._recovery_timeout_s = 60.0
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["circuit_breaker"]["status"] == "ok"
        assert body["checks"]["circuit_breaker"]["state"] == "closed"
