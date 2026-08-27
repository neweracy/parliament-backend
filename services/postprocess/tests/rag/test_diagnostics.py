"""Tests for the RAG diagnostics endpoint.

Verifies the diagnostic health check reports correct status for each
pipeline component under various failure conditions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rag.diagnostics import router


def _make_app(
    *,
    session_factory=None,
    chat_model=None,
    embeddings=None,
    settings=None,
) -> FastAPI:
    """Build a minimal FastAPI app with the diagnostics router."""
    app = FastAPI()
    app.include_router(router)

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


def _mock_breaker_closed() -> MagicMock:
    """Create a mock circuit breaker in the closed (healthy) state."""
    breaker = MagicMock()
    breaker.state.value = "closed"
    breaker._failure_count = 0
    breaker._failure_threshold = 5
    breaker._recovery_timeout_s = 60.0
    return breaker


def _mock_breaker_open() -> MagicMock:
    """Create a mock circuit breaker in the open (tripped) state."""
    breaker = MagicMock()
    breaker.state.value = "open"
    breaker._failure_count = 5
    breaker._failure_threshold = 5
    breaker._recovery_timeout_s = 60.0
    return breaker


class TestDiagnosticsAuth:
    """Authentication checks."""

    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_rejects_unauthenticated(self, _breaker: MagicMock) -> None:
        """Endpoint returns 401 without auth header."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics")
        assert resp.status_code == 401

    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_rejects_wrong_token(self, _breaker: MagicMock) -> None:
        """Endpoint returns 401 with wrong token."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get(
                "/rag/diagnostics",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401


class TestDiagnosticsComponents:
    """Component-level checks."""

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_reports_degraded_without_session_factory(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """Database check fails when session_factory is None."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"]["status"] == "error"

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_reports_chat_model_unavailable(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """Chat model check reports unavailable when None."""
        app = _make_app(chat_model=None)
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["chat_model"]["status"] == "unavailable"
        assert body["checks"]["chat_model"]["configured"] is False

    @patch("app.rag.diagnostics.probe_credentials", return_value=True)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_reports_aws_credentials_ok(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """AWS credentials check reports ok when found."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["aws_credentials"]["status"] == "ok"
        assert body["checks"]["aws_credentials"]["resolved"] is True

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_reports_aws_credentials_missing(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """AWS credentials check reports error when absent."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["aws_credentials"]["status"] == "error"

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_reports_embeddings_unavailable(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """Embeddings client check reports unavailable when None."""
        app = _make_app(embeddings=None)
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["embeddings_client"]["status"] == "unavailable"
        assert body["checks"]["embedding_api"]["status"] == "skipped"

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_reports_timeout_config(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """Timeout configuration is always present."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert "timeouts" in body["checks"]
        assert body["checks"]["timeouts"]["rag_agent_timeout_s"] == 50

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_includes_diagnostics_latency(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """Response includes latency measurement."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert "diagnostics_latency_ms" in body
        assert body["diagnostics_latency_ms"] >= 0

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_open())
    def test_circuit_breaker_open_reports_degraded(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """When circuit breaker is open, check reports degraded."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["circuit_breaker"]["status"] == "degraded"
        assert "error" in body["checks"]["circuit_breaker"]

    @patch("app.rag.diagnostics.probe_credentials", return_value=False)
    @patch("app.rag.diagnostics._bedrock_breaker", new_callable=lambda: lambda: _mock_breaker_closed())
    def test_circuit_breaker_closed_reports_ok(
        self, _breaker: MagicMock, _mock: MagicMock
    ) -> None:
        """When circuit breaker is closed, check reports ok."""
        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/rag/diagnostics", headers=_auth_headers())
        body = resp.json()
        assert body["checks"]["circuit_breaker"]["status"] == "ok"
        assert body["checks"]["circuit_breaker"]["state"] == "closed"
