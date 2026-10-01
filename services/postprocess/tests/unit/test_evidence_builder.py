"""Unit tests for app/correction/evidence_builder.py — evidence mapping.

Feature: transcript-evidence-navigation (task 2.2).

Covers the one-CorrectionRecord-to-one-CorrectionEntry mapping (Property 2) and
every correction shape in the design's shape table (Req 2.1-2.6, 2.10):
merge (many-to-one), split (one-to-many), punctuation/capitalization-only, year,
LLM, vetoed, and unresolvable. Also covers immutable ASR timing carry-across
(Req 2.7), provenance omit-when-unsupplied (Req 1.4, 1.5), threshold recording
(Req 1.1), stage/outcome by origin (Req 1.7, 1.8), and repeated identical Spans
resolving to successive raw runs.

No Bedrock/AWS/network: the builder is pure and free of pipeline state.
"""

from __future__ import annotations

from app.correction.evidence_builder import (
    build_correction_entries,
    build_correction_evidence,
)
from app.correction.source_ids import assign_source_word_ids
from app.models.entities import CorrectionRecord, EntityKind, EntityType, MatchStrategy
from app.models.evidence import CorrectionOutcome, CorrectionStage, MappingConfidence


def _word(text: str, start: float, end: float, confidence: float = 0.9) -> dict:
    return {"word": text, "start": start, "end": end, "confidence": confidence}


def _record(
    original: str,
    corrected: str,
    *,
    strategy: MatchStrategy = MatchStrategy.exact,
    confidence: float = 0.95,
    kind: EntityKind = EntityKind.location,
    etype: EntityType = EntityType.region,
) -> CorrectionRecord:
    return CorrectionRecord(
        original=original,
        corrected=corrected,
        strategy=strategy,
        confidence=confidence,
        entity_kind=kind,
        entity_type=etype,
    )


# ---------------------------------------------------------------------------
# Merge (many-to-one) — Req 2.1
# ---------------------------------------------------------------------------


class TestMergeShape:
    """Two or more consecutive Source_Words -> one corrected token (Req 2.1)."""

    def test_merge_covers_all_words_and_bounding_timing(self):
        raw = [
            _word("new", 1.0, 1.4),
            _word("juaben", 1.4, 2.0),
        ]
        ids = assign_source_word_ids(raw)
        records = [_record("new juaben", "New Juaben")]

        entries = build_correction_entries(records, raw, ids)

        assert len(entries) == 1
        e = entries[0]
        assert e.source_word_ids == ["w0", "w1"]
        assert e.source_span_id == "s:w0-w1"
        assert e.source_start == 1.0  # first covered word start
        assert e.source_end == 2.0  # last covered word end
        assert e.original == "new juaben"
        assert e.corrected == "New Juaben"
        assert e.mapping_confidence is None


# ---------------------------------------------------------------------------
# Split (one-to-many) — Req 2.2
# ---------------------------------------------------------------------------


class TestSplitShape:
    """One Source_Word -> two or more corrected tokens (Req 2.2)."""

    def test_split_addresses_single_word_timing(self):
        raw = [
            _word("cannot", 3.0, 3.5),
            _word("wait", 3.5, 3.9),
        ]
        ids = assign_source_word_ids(raw)
        # A split: the single raw word "cannot" becomes "can not".
        records = [_record("cannot", "can not")]

        entries = build_correction_entries(records, raw, ids)

        assert len(entries) == 1
        e = entries[0]
        assert e.source_word_ids == ["w0"]
        assert e.source_span_id == "s:w0-w0"
        assert e.source_start == 3.0
        assert e.source_end == 3.5


# ---------------------------------------------------------------------------
# Punctuation / capitalization only — Req 2.3
# ---------------------------------------------------------------------------


class TestPunctuationCapitalizationShape:
    """Only punctuation/capitalization changes; timing stays covered (Req 2.3)."""

    def test_capitalization_only_records_texts_and_timing(self):
        raw = [_word("ndc", 5.0, 5.4)]
        ids = assign_source_word_ids(raw)
        records = [
            _record("ndc", "NDC", kind=EntityKind.party, etype=EntityType.party)
        ]

        entries = build_correction_entries(records, raw, ids)

        e = entries[0]
        assert e.original == "ndc"
        assert e.corrected == "NDC"
        assert e.source_start == 5.0
        assert e.source_end == 5.4
        assert e.source_word_ids == ["w0"]


# ---------------------------------------------------------------------------
# Year normalization — Req 2.4
# ---------------------------------------------------------------------------


class TestYearShape:
    """Year normalization carries stage=year and covered-word timing (Req 2.4)."""

    def test_year_stage_and_timing(self):
        raw = [
            _word("two", 7.0, 7.3),
            _word("thousand", 7.3, 7.8),
            _word("twenty", 7.8, 8.2),
        ]
        ids = assign_source_word_ids(raw)
        records = [_record("two thousand twenty", "2020")]

        entries = build_correction_entries(
            records, raw, ids, stage=CorrectionStage.year, threshold=None
        )

        e = entries[0]
        assert e.correction_stage is CorrectionStage.year
        assert e.source_start == 7.0
        assert e.source_end == 8.2
        assert e.source_word_ids == ["w0", "w1", "w2"]
        assert e.threshold is None


# ---------------------------------------------------------------------------
# LLM change — Req 2.5
# ---------------------------------------------------------------------------


class TestLlmShape:
    """LLM change carries stage=llm and records model_id when supplied (Req 2.5)."""

    def test_llm_stage_and_model_id(self):
        raw = [_word("accra", 9.0, 9.5)]
        ids = assign_source_word_ids(raw)
        records = [_record("accra", "Accra")]

        entries = build_correction_entries(
            records,
            raw,
            ids,
            stage=CorrectionStage.llm,
            threshold=0.6,
            model_id="claude-sonnet",
        )

        e = entries[0]
        assert e.correction_stage is CorrectionStage.llm
        assert e.model_id == "claude-sonnet"
        assert e.threshold == 0.6

    def test_llm_model_id_omitted_when_absent(self):
        raw = [_word("accra", 9.0, 9.5)]
        ids = assign_source_word_ids(raw)
        records = [_record("accra", "Accra")]

        entries = build_correction_entries(
            records, raw, ids, stage=CorrectionStage.llm
        )

        dumped = entries[0].model_dump(by_alias=True, exclude_none=True)
        assert "modelId" not in dumped


# ---------------------------------------------------------------------------
# Vetoed — Req 1.9, 2.6
# ---------------------------------------------------------------------------


class TestVetoedShape:
    """A vetoed entry retains original text + timing and is outcome=vetoed."""

    def test_vetoed_outcome_and_retained_original(self):
        raw = [_word("kumasi", 11.0, 11.6)]
        ids = assign_source_word_ids(raw)
        records = [_record("kumasi", "Kumawu")]

        entries = build_correction_entries(
            records,
            raw,
            ids,
            stage=CorrectionStage.llm,
            outcome=CorrectionOutcome.vetoed,
        )

        e = entries[0]
        assert e.correction_outcome is CorrectionOutcome.vetoed
        assert e.original == "kumasi"  # original retained unchanged (Req 1.9)
        assert e.source_start == 11.0
        assert e.source_end == 11.6


# ---------------------------------------------------------------------------
# Unresolvable alignment — Req 2.10
# ---------------------------------------------------------------------------


class TestUnresolvableShape:
    """No contiguous raw run matches -> mapping_confidence=lost, no range."""

    def test_lost_when_original_absent_from_raw(self):
        raw = [_word("hello", 1.0, 1.5), _word("world", 1.5, 2.0)]
        ids = assign_source_word_ids(raw)
        records = [_record("nonexistent phrase", "Corrected")]

        entries = build_correction_entries(records, raw, ids)

        e = entries[0]
        assert e.mapping_confidence is MappingConfidence.lost
        assert e.source_word_ids == []
        assert e.source_start is None
        assert e.source_end is None
        # Still carries the before/after text so it is reviewable in list form.
        assert e.original == "nonexistent phrase"
        assert e.corrected == "Corrected"


# ---------------------------------------------------------------------------
# Property 2: one entry per change, and each addresses >=1 id or reports lost
# ---------------------------------------------------------------------------


class TestEvidenceCompleteness:
    """Every change maps to exactly one entry (Property 2)."""

    def test_one_entry_per_record(self):
        raw = [
            _word("new", 1.0, 1.4),
            _word("juaben", 1.4, 2.0),
            _word("ndc", 2.0, 2.4),
        ]
        ids = assign_source_word_ids(raw)
        records = [
            _record("new juaben", "New Juaben"),
            _record("ndc", "NDC", kind=EntityKind.party, etype=EntityType.party),
        ]

        entries = build_correction_entries(records, raw, ids)

        assert len(entries) == len(records)
        for e in entries:
            assert e.source_word_ids or e.mapping_confidence is MappingConfidence.lost

    def test_repeated_identical_spans_map_to_successive_runs(self):
        raw = [
            _word("accra", 1.0, 1.4),
            _word("and", 1.4, 1.6),
            _word("accra", 1.6, 2.0),
        ]
        ids = assign_source_word_ids(raw)
        records = [_record("accra", "Accra"), _record("accra", "Accra")]

        entries = build_correction_entries(records, raw, ids)

        assert entries[0].source_word_ids == ["w0"]
        assert entries[1].source_word_ids == ["w2"]
        assert entries[0].source_start == 1.0
        assert entries[1].source_start == 1.6


# ---------------------------------------------------------------------------
# Immutable timing (Req 2.7) — the builder never mutates raw word timing
# ---------------------------------------------------------------------------


class TestImmutableTiming:
    """The builder reads timing only; it never rewrites raw ASR start/end."""

    def test_raw_words_unchanged_after_build(self):
        raw = [_word("new", 1.0, 1.4), _word("juaben", 1.4, 2.0)]
        before = [dict(w) for w in raw]
        ids = assign_source_word_ids(raw)

        build_correction_entries([_record("new juaben", "New Juaben")], raw, ids)

        assert raw == before


# ---------------------------------------------------------------------------
# Provenance and threshold (Req 1.1, 1.4, 1.5)
# ---------------------------------------------------------------------------


class TestProvenanceAndThreshold:
    """Provenance recorded where supplied, omitted otherwise; threshold kept."""

    def test_supplied_provenance_recorded(self):
        raw = [_word("accra", 1.0, 1.5)]
        ids = assign_source_word_ids(raw)
        records = [_record("accra", "Accra")]

        entries = build_correction_entries(
            records,
            raw,
            ids,
            threshold=0.75,
            correlation_id="corr-9",
            dataset_version="v7",
        )

        e = entries[0]
        assert e.threshold == 0.75
        assert e.correlation_id == "corr-9"
        assert e.dataset_version == "v7"

    def test_unsupplied_provenance_omitted_from_dump(self):
        raw = [_word("accra", 1.0, 1.5)]
        ids = assign_source_word_ids(raw)
        entries = build_correction_entries([_record("accra", "Accra")], raw, ids)

        dumped = entries[0].model_dump(by_alias=True, exclude_none=True)
        assert "correlationId" not in dumped
        assert "datasetVersion" not in dumped
        assert "modelId" not in dumped
        assert "threshold" not in dumped

    def test_entity_classification_carried_across(self):
        raw = [_word("accra", 1.0, 1.5)]
        ids = assign_source_word_ids(raw)
        entries = build_correction_entries([_record("accra", "Accra")], raw, ids)

        e = entries[0]
        assert e.entity_kind == "location"
        assert e.entity_type == "region"


# ---------------------------------------------------------------------------
# build_correction_evidence orchestration
# ---------------------------------------------------------------------------


class TestBuildCorrectionEvidence:
    """The per-version container groups changes by origin with right stage/outcome."""

    def test_batches_carry_stage_and_outcome(self):
        raw = [
            _word("new", 1.0, 1.4),
            _word("juaben", 1.4, 2.0),
            _word("two", 2.0, 2.3),
            _word("thousand", 2.3, 2.8),
            _word("kumasi", 2.8, 3.4),
            _word("accra", 3.4, 3.9),
        ]
        ids = assign_source_word_ids(raw)

        evidence = build_correction_evidence(
            transcript_id=42,
            version=3,
            raw_words=raw,
            source_word_ids=ids,
            applied_records=[_record("new juaben", "New Juaben")],
            year_records=[_record("two thousand", "2020")],
            llm_records=[_record("accra", "Accra")],
            vetoed_records=[_record("kumasi", "Kumawu")],
            rule_threshold=0.9,
            llm_threshold=0.6,
            correlation_id="corr-1",
            dataset_version="v3",
            model_id="claude-x",
        )

        assert evidence.transcript_id == 42
        assert evidence.version == 3
        assert len(evidence.entries) == 4

        by_stage_outcome = {
            (e.correction_stage, e.correction_outcome): e for e in evidence.entries
        }
        assert (CorrectionStage.rule, CorrectionOutcome.applied) in by_stage_outcome
        assert (CorrectionStage.year, CorrectionOutcome.applied) in by_stage_outcome
        assert (CorrectionStage.llm, CorrectionOutcome.applied) in by_stage_outcome
        assert (CorrectionStage.llm, CorrectionOutcome.vetoed) in by_stage_outcome

        # Year batch carries no threshold; llm entries carry model_id.
        year_entry = by_stage_outcome[(CorrectionStage.year, CorrectionOutcome.applied)]
        assert year_entry.threshold is None
        llm_applied = by_stage_outcome[(CorrectionStage.llm, CorrectionOutcome.applied)]
        assert llm_applied.model_id == "claude-x"
        assert llm_applied.threshold == 0.6

    def test_empty_evidence(self):
        evidence = build_correction_evidence(
            transcript_id=1,
            version=1,
            raw_words=[],
            source_word_ids=[],
            applied_records=[],
        )
        assert evidence.entries == []
        dumped = evidence.model_dump(by_alias=True, exclude_none=True)
        assert dumped == {"transcriptId": 1, "version": 1, "entries": []}
