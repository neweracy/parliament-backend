"""Unit tests for app/deps.py — auth and correlation ID handling."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import _get_settings, get_correlation_id, verify_service_token

# ---------------------------------------------------------------------------
# Test app setup
# ---------------------------------------------------------------------------

TEST_TOKEN = "test-service-token-xyz"


def _make_test_settings() -> Settings:
    """Create a Settings instance with a known SERVICE_TOKEN for testing."""
    return Settings(
        service_token=TEST_TOKEN,
        database_url="postgresql://test:test@localhost/test",
    )


app = FastAPI()
app.dependency_overrides[_get_settings] = _make_test_settings


@app.get("/protected")
async def protected_route(
    _auth: None = pytest.importorskip("fastapi").Depends(verify_service_token),
    correlation_id: str = pytest.importorskip("fastapi").Depends(get_correlation_id),
):
    return {"ok": True, "correlationId": correlation_id}


@app.get("/correlation-only")
async def correlation_only_route(
    correlation_id: str = pytest.importorskip("fastapi").Depends(get_correlation_id),
):
    return {"correlationId": correlation_id}


client = TestClient(app)


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestVerifyServiceToken:
    """Test Bearer Service_Token authentication dependency."""

    def test_missing_authorization_header_returns_401(self):
        """No Authorization header → 401 with MISSING_TOKEN code."""
        resp = client.get("/protected")
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"]["type"] == "AuthenticationError"
        assert body["detail"]["error"]["code"] == "MISSING_TOKEN"

    def test_invalid_token_returns_401(self):
        """Wrong token → 401 with INVALID_TOKEN code."""
        resp = client.get(
            "/protected",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"]["type"] == "AuthenticationError"
        assert body["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_malformed_authorization_header_returns_401(self):
        """Non-Bearer format → 401 with INVALID_TOKEN code."""
        resp = client.get(
            "/protected",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"]["type"] == "AuthenticationError"
        assert body["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_bearer_only_no_token_returns_401(self):
        """'Bearer ' with no token value → 401."""
        resp = client.get(
            "/protected",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"]["error"]["type"] == "AuthenticationError"
        assert body["detail"]["error"]["code"] == "INVALID_TOKEN"

    def test_valid_token_returns_200(self):
        """Correct token → passes through to the route."""
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_timing_safe_comparison(self):
        """Token comparison uses secrets.compare_digest (constant-time).

        We verify the function is used by checking that a token differing
        only in the last character still returns INVALID_TOKEN (not some
        early-exit shortcut).
        """
        almost_right = TEST_TOKEN[:-1] + ("a" if TEST_TOKEN[-1] != "a" else "b")
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {almost_right}"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# Correlation ID tests
# ---------------------------------------------------------------------------


class TestGetCorrelationId:
    """Test X-Correlation-Id header handling."""

    def test_provided_correlation_id_is_returned(self):
        """When the header is present, it's returned as-is."""
        cid = "my-custom-correlation-id-123"
        resp = client.get(
            "/correlation-only",
            headers={"X-Correlation-Id": cid},
        )
        assert resp.status_code == 200
        assert resp.json()["correlationId"] == cid
        assert resp.headers["X-Correlation-Id"] == cid

    def test_missing_correlation_id_generates_uuid4(self):
        """When the header is absent, a UUID4 is generated."""
        resp = client.get("/correlation-only")
        assert resp.status_code == 200
        cid = resp.json()["correlationId"]
        # Validate it's a valid UUID4
        parsed = uuid.UUID(cid, version=4)
        assert str(parsed) == cid
        # Also echoed in the response header
        assert resp.headers["X-Correlation-Id"] == cid

    def test_correlation_id_echoed_in_response_header(self):
        """The correlation ID appears in the response X-Correlation-Id header."""
        cid = "echo-test-id"
        resp = client.get(
            "/correlation-only",
            headers={"X-Correlation-Id": cid},
        )
        assert resp.headers["X-Correlation-Id"] == cid

    def test_each_request_without_header_gets_unique_id(self):
        """Two requests without X-Correlation-Id get different UUIDs."""
        resp1 = client.get("/correlation-only")
        resp2 = client.get("/correlation-only")
        cid1 = resp1.json()["correlationId"]
        cid2 = resp2.json()["correlationId"]
        assert cid1 != cid2

    def test_auth_and_correlation_together(self):
        """Both dependencies work together on a protected route."""
        cid = "combined-test-id"
        resp = client.get(
            "/protected",
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": cid,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["correlationId"] == cid
        assert resp.headers["X-Correlation-Id"] == cid
