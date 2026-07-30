"""End-to-end integration test — full HTTP surface with real correction pipeline.

Runs representative examples through the FastAPI app via TestClient, with
a real fixture DatasetCache (no DB) and LLM disabled, asserting:
  - Corrected transcript matches expected
  - Response shape matches CorrectionResponse contract (by_alias, exclude_none)
  - X-Correlation-Id is echoed
  - X-Postprocess-Stage is set to 'complete'
  - Entities and metadata are present in the response

Bedrock is NOT called — LLM is disabled via options.

Requirements: 15.9, 2.5
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.deps import _get_settings
from app.main import app
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_TOKEN = "e2e-test-token-secret"


def _test_settings() -> Settings:
    """Return a Settings instance suitable for e2e testing."""
    return Settings(
        service_token=TEST_TOKEN,
        database_url="postgresql://test:test@localhost:5432/test",
        llm_enabled=False,
    )


def _build_e2e_snapshot() -> DatasetSnapshot:
    """Build a realistic DatasetSnapshot with entities for the test cases.

    Includes:
      - Ningo-Prampram (constituency) with fused alias 'ningoprampram'
      - Kumasi (city) with fuzzy-matchable alias 'kumase'
      - B.B. Carboo (MP) with alias 'bb kabo'
      - National Democratic Congress (party, NDC)
    """
    records = [
        EntityRecord(
            canonical="Ningo-Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=["ningoprampram", "ningo prampram", "ningo-prampram"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.supplementary,
            aliases=["pram pram", "prampram"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["kumase", "kumsi", "kumasi"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="B.B. Carboo",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["bb carboo", "bb kabo", "b.b. kabo", "b.b. carboo"],
            source="all_persons",
            source_rank=3,
        ),
        EntityRecord(
            canonical="Ken Ofori-Atta",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["ken ofori atta", "k. ofori-atta", "ken ofori-atta"],
            source="all_persons",
            source_rank=3,
        ),
        EntityRecord(
            canonical="National Democratic Congress",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["ndc", "national democratic congress"],
            party="NDC",
            source="all_parties",
            source_rank=5,
        ),
    ]

    index = build_index(records)

    return DatasetSnapshot(
        version="2026-07-09T10:00:00+00:00",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime(2026, 7, 9, 10, 0, 0, tzinfo=timezone.utc),
        index=index,
        block_list=frozenset(["general", "page", "nation", "national"]),
        stopwords=frozenset(["the", "a", "an", "is", "of", "and", "in", "to"]),
        word_stopwords=frozenset([
            "a", "an", "the", "in", "on", "at", "to", "of", "is", "are",
            "was", "were", "be", "and", "or", "but", "for", "by", "with",
        ]),
        title_prefixes=frozenset([
            "honorable", "honourable", "hon", "minister", "mr", "mrs", "dr",
        ]),
    )


@pytest.fixture()
def e2e_client() -> TestClient:
    """TestClient with a fully built DatasetSnapshot for e2e testing."""
    app.dependency_overrides[_get_settings] = _test_settings

    snapshot = _build_e2e_snapshot()

    mock_cache = MagicMock(spec=DatasetCache)
    mock_cache.get_snapshot.return_value = snapshot
    app.state.dataset_cache = mock_cache
    app.state.settings = _test_settings()
    app.state.start_time = time.time()

    client = TestClient(app, raise_server_exceptions=False)
    yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


class TestFusedCorrection:
    """E2E: fused entity correction — 'ningoprampram' → 'Ningo-Prampram'."""

    def test_fused_correction_in_transcript(self, e2e_client: TestClient):
        """A transcript with 'ningoprampram' is corrected to 'Ningo-Prampram'."""
        resp = e2e_client.post(
            "/v1/postprocess",
            json={
                "transcript": "the member for ningoprampram spoke",
                "words": [
                    {"word": "the", "start": 0.0, "end": 0.2, "confidence": 0.99},
                    {"word": "member", "start": 0.2, "end": 0.5, "confidence": 0.98},
                    {"word": "for", "start": 0.5, "end": 0.7, "confidence": 0.99},
                    {"word": "ningoprampram", "start": 0.7, "end": 1.3, "confidence": 0.55},
                    {"word": "spoke", "start": 1.3, "end": 1.6, "confidence": 0.97},
                ],
                "options": {"llmRefine": False},
            },
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": "e2e-fused-test-001",
            },
        )

        assert resp.status_code == 200
        body = resp.json()

        # Transcript is corrected
        assert "Ningo-Prampram" in body["transcript"]

        # Response shape — required top-level fields
        assert "transcript" in body
        assert "words" in body
        assert "entities" in body
        assert "metadata" in body

        # Entities include the corrected entity
        entity_names = [e["name"] for e in body["entities"]]
        assert "Ningo-Prampram" in entity_names

        # Metadata has correction counters and status
        assert body["metadata"]["postprocessing_status"] == "applied"
        assert body["metadata"].get("location_corrections", 0) >= 1

        # Headers
        assert resp.headers.get("x-correlation-id") == "e2e-fused-test-001"
        assert resp.headers.get("x-postprocess-stage") == "complete"


class TestFuzzyCorrection:
    """E2E: fuzzy entity correction — 'Kumase' → 'Kumasi'."""

    def test_fuzzy_correction_in_transcript(self, e2e_client: TestClient):
        """A transcript with 'Kumase' is corrected to 'Kumasi' via fuzzy match."""
        resp = e2e_client.post(
            "/v1/postprocess",
            json={
                "transcript": "we traveled to Kumase yesterday",
                "words": [
                    {"word": "we", "start": 0.0, "end": 0.2, "confidence": 0.99},
                    {"word": "traveled", "start": 0.2, "end": 0.6, "confidence": 0.95},
                    {"word": "to", "start": 0.6, "end": 0.8, "confidence": 0.99},
                    {"word": "Kumase", "start": 0.8, "end": 1.2, "confidence": 0.60},
                    {"word": "yesterday", "start": 1.2, "end": 1.7, "confidence": 0.98},
                ],
                "options": {"llmRefine": False},
            },
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": "e2e-fuzzy-test-002",
            },
        )

        assert resp.status_code == 200
        body = resp.json()

        # Transcript is corrected
        assert "Kumasi" in body["transcript"]
        assert "Kumase" not in body["transcript"]

        # The corrected word has locationCorrected flag
        corrected_words = [w for w in body["words"] if w.get("locationCorrected")]
        assert len(corrected_words) >= 1
        assert any(w["word"] == "Kumasi" for w in corrected_words)

        # Entity summary includes Kumasi
        entity_names = [e["name"] for e in body["entities"]]
        assert "Kumasi" in entity_names

        # Check entity structure
        kumasi_entity = next(e for e in body["entities"] if e["name"] == "Kumasi")
        assert kumasi_entity["kind"] == "location"
        assert kumasi_entity["type"] == "city"
        assert kumasi_entity["mentions"] >= 1

        # Headers
        assert resp.headers.get("x-correlation-id") == "e2e-fuzzy-test-002"
        assert resp.headers.get("x-postprocess-stage") == "complete"


class TestEmptyWordsPassthrough:
    """E2E: empty words list is a valid passthrough case."""

    def test_empty_words_passthrough(self, e2e_client: TestClient):
        """An empty words list returns a corrected transcript with empty words."""
        resp = e2e_client.post(
            "/v1/postprocess",
            json={
                "transcript": "hello world",
                "words": [],
                "options": {"llmRefine": False},
            },
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": "e2e-empty-test-003",
            },
        )

        assert resp.status_code == 200
        body = resp.json()

        # Transcript passes through (no entity matches expected)
        assert body["transcript"] == "hello world"
        assert body["words"] == []
        assert isinstance(body["entities"], list)
        assert "metadata" in body

        # Metadata is present
        assert body["metadata"]["postprocessing_status"] == "applied"

        # Headers
        assert resp.headers.get("x-correlation-id") == "e2e-empty-test-003"
        assert resp.headers.get("x-postprocess-stage") == "complete"


class TestResponseContract:
    """E2E: response shape matches CorrectionResponse contract."""

    def test_response_uses_camel_case_aliases(self, e2e_client: TestClient):
        """Response uses camelCase field names per the contract (by_alias=True)."""
        resp = e2e_client.post(
            "/v1/postprocess",
            json={
                "transcript": "the member for ningoprampram spoke",
                "words": [
                    {"word": "the", "start": 0.0, "end": 0.2, "confidence": 0.99},
                    {"word": "member", "start": 0.2, "end": 0.5, "confidence": 0.98},
                    {"word": "for", "start": 0.5, "end": 0.7, "confidence": 0.99},
                    {"word": "ningoprampram", "start": 0.7, "end": 1.3, "confidence": 0.55},
                    {"word": "spoke", "start": 1.3, "end": 1.6, "confidence": 0.97},
                ],
                "options": {"llmRefine": False},
            },
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )

        assert resp.status_code == 200
        body = resp.json()

        # CorrectedWord flags use camelCase
        corrected_words = [w for w in body["words"] if w.get("locationCorrected")]
        assert len(corrected_words) >= 1

        for w in corrected_words:
            # camelCase fields present (not snake_case)
            assert "locationCorrected" in w
            assert "entityKind" in w
            assert "entityType" in w
            # snake_case versions should NOT be present
            assert "location_corrected" not in w
            assert "entity_kind" not in w
            assert "entity_type" not in w

    def test_none_values_excluded_from_response(self, e2e_client: TestClient):
        """None/false values are excluded from response (exclude_none=True)."""
        resp = e2e_client.post(
            "/v1/postprocess",
            json={
                "transcript": "hello world",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.3, "confidence": 0.99},
                    {"word": "world", "start": 0.3, "end": 0.6, "confidence": 0.98},
                ],
                "options": {"llmRefine": False},
            },
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )

        assert resp.status_code == 200
        body = resp.json()

        # Uncorrected words should NOT have locationCorrected or entityKind
        for w in body["words"]:
            assert "locationCorrected" not in w
            assert "bedrockCorrected" not in w
            assert "entityKind" not in w
            assert "entityType" not in w

        # Zero counters should be absent from metadata
        metadata = body["metadata"]
        if metadata.get("location_corrections") is not None:
            assert metadata["location_corrections"] > 0
        # bedrock_corrections and year_corrections should be absent when 0
        assert "bedrock_corrections" not in metadata

    def test_metadata_correlation_id_uses_alias(self, e2e_client: TestClient):
        """metadata.correlationId uses the camelCase alias."""
        resp = e2e_client.post(
            "/v1/postprocess",
            json={
                "transcript": "test",
                "words": [],
                "options": {"llmRefine": False},
                "correlationId": "my-custom-corr-id",
            },
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "X-Correlation-Id": "my-custom-corr-id",
            },
        )

        assert resp.status_code == 200
        body = resp.json()

        # correlationId in metadata uses camelCase alias
        metadata = body["metadata"]
        assert "correlationId" in metadata
        assert metadata["correlationId"] == "my-custom-corr-id"
        # snake_case version should NOT be present
        assert "correlation_id" not in metadata
