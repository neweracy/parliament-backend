"""Retrieval status tracking — distinguishes empty results from failures.

When both retriever arms return empty lists, the answerer cannot tell whether
the query genuinely has no matches or the infrastructure is broken. This module
provides a lightweight status tracker that travels alongside the retrieval
results so downstream code can respond appropriately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ArmStatus(Enum):
    """Status of a single retrieval arm."""

    OK = "ok"  # Ran successfully, may have returned 0+ results
    FAILED = "failed"  # Threw an exception (swallowed, returned [])
    SKIPPED = "skipped"  # Not configured (e.g., no embeddings client)


@dataclass
class RetrievalStatus:
    """Tracks the outcome of both retrieval arms for one query.

    Attributes:
        fulltext: Status of the fulltext (tsvector) search arm.
        vector: Status of the vector (pgvector cosine) search arm.
        fulltext_count: Number of results from fulltext arm.
        vector_count: Number of results from vector arm.
        fused_count: Number of results after RRF fusion.
        fulltext_error: Error message if fulltext arm failed.
        vector_error: Error message if vector arm failed.
    """

    fulltext: ArmStatus = ArmStatus.OK
    vector: ArmStatus = ArmStatus.OK
    fulltext_count: int = 0
    vector_count: int = 0
    fused_count: int = 0
    fulltext_error: str | None = None
    vector_error: str | None = None

    @property
    def both_failed(self) -> bool:
        """True when both arms failed — results are empty due to errors, not data."""
        return self.fulltext == ArmStatus.FAILED and self.vector == ArmStatus.FAILED

    @property
    def any_failed(self) -> bool:
        """True when at least one arm failed — results may be incomplete."""
        return self.fulltext == ArmStatus.FAILED or self.vector == ArmStatus.FAILED

    @property
    def degraded(self) -> bool:
        """True when operating on partial data (one arm failed or skipped)."""
        return (
            self.fulltext in (ArmStatus.FAILED, ArmStatus.SKIPPED)
            or self.vector in (ArmStatus.FAILED, ArmStatus.SKIPPED)
        )

    def to_dict(self) -> dict:
        """Serialize for logging or API responses."""
        result: dict = {
            "fulltext": self.fulltext.value,
            "vector": self.vector.value,
            "fulltext_count": self.fulltext_count,
            "vector_count": self.vector_count,
            "fused_count": self.fused_count,
        }
        if self.fulltext_error:
            result["fulltext_error"] = self.fulltext_error
        if self.vector_error:
            result["vector_error"] = self.vector_error
        return result
