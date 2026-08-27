"""Tests for the RetrievalStatus dataclass.

Verifies status tracking correctly classifies retrieval arm outcomes.
"""

from __future__ import annotations

import pytest

from app.rag.retrieval_status import ArmStatus, RetrievalStatus


class TestArmStatus:
    """ArmStatus enum values."""

    def test_values(self) -> None:
        assert ArmStatus.OK.value == "ok"
        assert ArmStatus.FAILED.value == "failed"
        assert ArmStatus.SKIPPED.value == "skipped"


class TestRetrievalStatusBothFailed:
    """The both_failed property."""

    def test_both_failed(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.FAILED,
            vector=ArmStatus.FAILED,
        )
        assert status.both_failed is True

    def test_one_ok_one_failed(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.OK,
            vector=ArmStatus.FAILED,
        )
        assert status.both_failed is False

    def test_both_ok(self) -> None:
        status = RetrievalStatus()
        assert status.both_failed is False

    def test_one_skipped_one_failed(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.SKIPPED,
            vector=ArmStatus.FAILED,
        )
        assert status.both_failed is False


class TestRetrievalStatusAnyFailed:
    """The any_failed property."""

    def test_both_ok(self) -> None:
        assert RetrievalStatus().any_failed is False

    def test_fulltext_failed(self) -> None:
        status = RetrievalStatus(fulltext=ArmStatus.FAILED)
        assert status.any_failed is True

    def test_vector_failed(self) -> None:
        status = RetrievalStatus(vector=ArmStatus.FAILED)
        assert status.any_failed is True

    def test_both_failed(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.FAILED,
            vector=ArmStatus.FAILED,
        )
        assert status.any_failed is True


class TestRetrievalStatusDegraded:
    """The degraded property."""

    def test_both_ok(self) -> None:
        assert RetrievalStatus().degraded is False

    def test_one_skipped(self) -> None:
        status = RetrievalStatus(vector=ArmStatus.SKIPPED)
        assert status.degraded is True

    def test_one_failed(self) -> None:
        status = RetrievalStatus(fulltext=ArmStatus.FAILED)
        assert status.degraded is True

    def test_both_skipped(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.SKIPPED,
            vector=ArmStatus.SKIPPED,
        )
        assert status.degraded is True


class TestRetrievalStatusToDict:
    """The to_dict serialization."""

    def test_all_ok_no_errors(self) -> None:
        status = RetrievalStatus(
            fulltext_count=5,
            vector_count=8,
            fused_count=10,
        )
        d = status.to_dict()
        assert d["fulltext"] == "ok"
        assert d["vector"] == "ok"
        assert d["fulltext_count"] == 5
        assert d["vector_count"] == 8
        assert d["fused_count"] == 10
        assert "fulltext_error" not in d
        assert "vector_error" not in d

    def test_with_errors(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.OK,
            vector=ArmStatus.FAILED,
            vector_error="ConnectionRefused",
        )
        d = status.to_dict()
        assert d["vector"] == "failed"
        assert d["vector_error"] == "ConnectionRefused"
        assert "fulltext_error" not in d

    def test_both_errors(self) -> None:
        status = RetrievalStatus(
            fulltext=ArmStatus.FAILED,
            vector=ArmStatus.FAILED,
            fulltext_error="DBError",
            vector_error="EmbeddingTimeout",
        )
        d = status.to_dict()
        assert d["fulltext_error"] == "DBError"
        assert d["vector_error"] == "EmbeddingTimeout"

    def test_default_values(self) -> None:
        """Default status has zero counts and OK arms."""
        d = RetrievalStatus().to_dict()
        assert d["fulltext"] == "ok"
        assert d["vector"] == "ok"
        assert d["fulltext_count"] == 0
        assert d["vector_count"] == 0
        assert d["fused_count"] == 0
