"""Unit tests for the correction evidence models.

Covers the closed-set enumerations, the additive omit-when-unsupplied
serialization contract (``by_alias=True, exclude_none=True``), camelCase alias
round-tripping, and structural validation.

Requirements: 1.1, 1.4, 1.5, 8.2, 10.3
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.evidence import (
    CorrectionEntry,
    CorrectionEvidence,
    CorrectionOutcome,
    CorrectionStage,
    MappingConfidence,
)

# ---------------------------------------------------------------------------
# 1. Closed-set enumerations
# ---------------------------------------------------------------------------


class TestClosedSetEnums:
    """Stage, outcome, and mapping confidence are closed StrEnum sets."""

    def test_correction_stage_values(self):
        assert {s.value for s in CorrectionStage} == {"rule", "year", "llm"}

    def test_correction_outcome_values(self):
        assert {o.value for o in CorrectionOutcome} == {"applied", "vetoed"}

    def test_mapping_confidence_values(self):
        assert {m.value for m in MappingConfidence} == {"exact", "lost"}

    def test_stage_is_str(self):
        assert CorrectionStage.rule == "rule"

    def test_invalid_stage_rejected(self):
        with pytest.raises(ValidationError):
            CorrectionEntry(
                source_span_id="s:w0-w0",
                source_word_ids=["w0"],
                original="foo",
                corrected="bar",
                correction_stage="unknown",
                correction_outcome="applied",
                confidence=0.9,
            )

    def test_invalid_outcome_rejected(self):
        with pytest.raises(ValidationError):
            CorrectionEntry(
                source_span_id="s:w0-w0",
                source_word_ids=["w0"],
                original="foo",
                corrected="bar",
                correction_stage="rule",
                correction_outcome="rejected",
                confidence=0.9,
            )


# ---------------------------------------------------------------------------
# 2. Omit-when-unsupplied serialization (additive / Baseline-compatible)
# ---------------------------------------------------------------------------


class TestOmitWhenUnsupplied:
    """Optional fields absent from output under exclude_none (Req 1.5, 10.3)."""

    def _minimal(self) -> CorrectionEntry:
        return CorrectionEntry(
            source_span_id="s:w0-w1",
            source_word_ids=["w0", "w1"],
            original="new york",
            corrected="New York",
            correction_stage=CorrectionStage.rule,
            correction_outcome=CorrectionOutcome.applied,
            confidence=0.95,
        )

    def test_unsupplied_provenance_omitted(self):
        dumped = self._minimal().model_dump(by_alias=True, exclude_none=True)
        assert "correlationId" not in dumped
        assert "datasetVersion" not in dumped
        assert "modelId" not in dumped

    def test_unsupplied_entity_classification_omitted(self):
        dumped = self._minimal().model_dump(by_alias=True, exclude_none=True)
        assert "entityKind" not in dumped
        assert "entityType" not in dumped

    def test_unsupplied_mapping_confidence_omitted(self):
        dumped = self._minimal().model_dump(by_alias=True, exclude_none=True)
        assert "mappingConfidence" not in dumped

    def test_unsupplied_threshold_omitted(self):
        dumped = self._minimal().model_dump(by_alias=True, exclude_none=True)
        assert "threshold" not in dumped

    def test_required_fields_present(self):
        dumped = self._minimal().model_dump(by_alias=True, exclude_none=True)
        assert dumped["sourceSpanId"] == "s:w0-w1"
        assert dumped["sourceWordIds"] == ["w0", "w1"]
        assert dumped["original"] == "new york"
        assert dumped["corrected"] == "New York"
        assert dumped["correctionStage"] == "rule"
        assert dumped["correctionOutcome"] == "applied"
        assert dumped["confidence"] == 0.95

    def test_supplied_optional_fields_present(self):
        entry = CorrectionEntry(
            source_span_id="s:w2-w2",
            source_word_ids=["w2"],
            original="2020",
            corrected="2020",
            source_start=1.0,
            source_end=1.5,
            correction_stage=CorrectionStage.llm,
            correction_outcome=CorrectionOutcome.applied,
            confidence=0.8,
            threshold=0.7,
            entity_kind="location",
            entity_type="region",
            correlation_id="corr-1",
            dataset_version="v3",
            model_id="claude-x",
            mapping_confidence=MappingConfidence.lost,
        )
        dumped = entry.model_dump(by_alias=True, exclude_none=True)
        assert dumped["sourceStart"] == 1.0
        assert dumped["sourceEnd"] == 1.5
        assert dumped["threshold"] == 0.7
        assert dumped["entityKind"] == "location"
        assert dumped["entityType"] == "region"
        assert dumped["correlationId"] == "corr-1"
        assert dumped["datasetVersion"] == "v3"
        assert dumped["modelId"] == "claude-x"
        assert dumped["mappingConfidence"] == "lost"


# ---------------------------------------------------------------------------
# 3. populate_by_name round-tripping
# ---------------------------------------------------------------------------


class TestPopulateByName:
    """Fields accept both snake_case names and camelCase aliases."""

    def test_construct_by_field_name(self):
        entry = CorrectionEntry(
            source_span_id="s:w0-w0",
            source_word_ids=["w0"],
            original="foo",
            corrected="bar",
            correction_stage="rule",
            correction_outcome="applied",
            confidence=0.9,
        )
        assert entry.source_span_id == "s:w0-w0"

    def test_construct_by_alias(self):
        entry = CorrectionEntry.model_validate(
            {
                "sourceSpanId": "s:w0-w0",
                "sourceWordIds": ["w0"],
                "original": "foo",
                "corrected": "bar",
                "correctionStage": "year",
                "correctionOutcome": "vetoed",
                "confidence": 0.6,
            }
        )
        assert entry.source_span_id == "s:w0-w0"
        assert entry.correction_stage is CorrectionStage.year
        assert entry.correction_outcome is CorrectionOutcome.vetoed


# ---------------------------------------------------------------------------
# 4. Confidence bounds and container
# ---------------------------------------------------------------------------


class TestConfidenceBounds:
    """Confidence is constrained to [0, 1] like CorrectionRecord."""

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            CorrectionEntry(
                source_span_id="s",
                source_word_ids=["w0"],
                original="a",
                corrected="b",
                correction_stage="rule",
                correction_outcome="applied",
                confidence=1.5,
            )

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            CorrectionEntry(
                source_span_id="s",
                source_word_ids=["w0"],
                original="a",
                corrected="b",
                correction_stage="rule",
                correction_outcome="applied",
                confidence=-0.1,
            )


class TestCorrectionEvidenceContainer:
    """The per-version container keys entries to transcript + version."""

    def test_empty_evidence_serialization(self):
        ev = CorrectionEvidence(transcript_id=42, version=3)
        dumped = ev.model_dump(by_alias=True, exclude_none=True)
        assert dumped == {"transcriptId": 42, "version": 3, "entries": []}

    def test_evidence_with_entries(self):
        ev = CorrectionEvidence(
            transcript_id=7,
            version=1,
            entries=[
                CorrectionEntry(
                    source_span_id="s:w0-w0",
                    source_word_ids=["w0"],
                    original="foo",
                    corrected="Foo",
                    correction_stage="rule",
                    correction_outcome="applied",
                    confidence=0.99,
                )
            ],
        )
        dumped = ev.model_dump(by_alias=True, exclude_none=True)
        assert dumped["transcriptId"] == 7
        assert len(dumped["entries"]) == 1
        assert dumped["entries"][0]["sourceSpanId"] == "s:w0-w0"
