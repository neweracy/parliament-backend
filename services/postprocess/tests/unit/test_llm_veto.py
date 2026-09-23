"""Unit tests for the LLM_Refiner veto (Req 9) — pure helpers + pipeline wiring.

Two layers:

* Pure-function tests over ``app.llm.veto`` and ``app.llm.prompt`` veto helpers:
  chunk assignment (9.1), decision resolution and ambiguity discard (9.11),
  the restore of text / Words / corrections order (9.2, 9.3, 9.4, 9.10), and
  the prompt review block + parser (9.1). No Bedrock, no I/O.

* Pipeline tests (``app.pipeline.run_pipeline``) with Bedrock mocked, driving a
  real rule correction through ``correct_words`` so ``veto_spans`` is populated,
  then injecting veto results to assert the end-to-end restore, the vetoed
  history record (9.5), the metadata veto count (9.7), the person-entity restore
  (9.13), the chunk-failure retention (9.8), the history-failure isolation
  (9.12), and baseline equivalence when the flag is off (Req 12.9 / 14.4).

Every test that reaches the LLM stage mocks Bedrock; a real network or AWS call
would fail (Req 16.12).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.correction.engine import VetoSpan
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import build_index
from app.llm.bedrock import BedrockClient
from app.llm.prompt import (
    VETO_OUTPUT_MARKER,
    build_system_prompt,
    parse_veto_decisions,
)
from app.llm.refiner import VetoResult
from app.llm.veto import (
    ResolvedVeto,
    RuleCorrection,
    apply_vetoes,
    assign_corrections_to_chunks,
    resolve_vetoes,
)
from app.models.entities import (
    CorrectionRecord,
    EntityKind,
    EntityRecord,
    EntityType,
    MatchStrategy,
)
from app.models.request import CorrectionOptions, CorrectionRequest, Word
from app.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    defaults = {
        "service_token": "test-token",
        "database_url": "postgresql+psycopg://u:p@localhost/test",
        "llm_enabled": True,
        "bedrock_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "llm_chunk_size": 50,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_bedrock_client(configured: bool = True) -> MagicMock:
    client = MagicMock(spec=BedrockClient)
    client.is_configured = configured
    client.model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    return client


def _build_snapshot(records) -> DatasetSnapshot:
    return DatasetSnapshot(
        version="veto-test-v1",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime(2026, 7, 9, 10, 0, 0, tzinfo=UTC),
        index=build_index(records),
        block_list=frozenset(),
        stopwords=frozenset(["the", "a", "an", "is", "of", "and", "in", "to"]),
        word_stopwords=frozenset(["the", "a", "an", "in", "of", "and", "to"]),
        title_prefixes=frozenset(["honorable", "honourable", "hon", "mr", "dr"]),
    )


def _make_cache(snapshot: DatasetSnapshot) -> DatasetCache:
    cache = MagicMock(spec=DatasetCache)
    cache.get_snapshot.return_value = snapshot
    return cache


def _record(canonical, kind, etype, aliases):
    return EntityRecord(
        canonical=canonical,
        entity_kind=kind,
        entity_type=etype,
        aliases=aliases,
        source="test",
        source_rank=0,
    )


def _correction_record(original, corrected, kind=EntityKind.location):
    return CorrectionRecord(
        original=original,
        corrected=corrected,
        strategy=MatchStrategy.phonetic,
        confidence=0.80,
        entity_kind=kind,
        entity_type=EntityType.city if kind == EntityKind.location else EntityType.mp,
    )


# ===========================================================================
# Pure helpers — assign / resolve / apply
# ===========================================================================


class TestAssignCorrectionsToChunks:
    """Rule corrections are grouped by the chunk containing their Span (Req 9.1)."""

    def test_assigns_to_containing_chunk(self):
        corrections = [
            RuleCorrection(0, "kumase", "Kumasi", "location"),
            RuleCorrection(1, "accra", "Accra", "location"),
        ]
        chunks = ["hello Kumasi world", "the Accra region"]
        per_chunk = assign_corrections_to_chunks(corrections, chunks)
        assert [c.record_index for c in per_chunk[0]] == [0]
        assert [c.record_index for c in per_chunk[1]] == [1]

    def test_unmatched_correction_is_dropped(self):
        corrections = [RuleCorrection(0, "kumase", "Kumasi", "location")]
        chunks = ["nothing here"]
        per_chunk = assign_corrections_to_chunks(corrections, chunks)
        assert per_chunk == [[]]


class TestResolveVetoes:
    """Each veto decision resolves to exactly one supplied correction (Req 9.11)."""

    def test_resolves_single_match(self):
        chunk = [RuleCorrection(3, "kumase", "Kumasi", "location")]
        resolved = resolve_vetoes([("kumase", "Kumasi")], chunk)
        assert len(resolved) == 1
        assert resolved[0].correction.record_index == 3

    def test_case_insensitive_match(self):
        chunk = [RuleCorrection(0, "Kumase", "Kumasi", "location")]
        resolved = resolve_vetoes([("kumase", "kumasi")], chunk)
        assert len(resolved) == 1

    def test_unresolvable_zero_match_discarded(self):
        chunk = [RuleCorrection(0, "kumase", "Kumasi", "location")]
        resolved = resolve_vetoes([("accra", "Accra")], chunk)
        assert resolved == []

    def test_ambiguous_two_matches_discarded_others_apply(self):
        # Two identical corrections in the chunk → ambiguous, discard.
        chunk = [
            RuleCorrection(0, "sege", "Sege", "location"),
            RuleCorrection(1, "sege", "Sege", "location"),
            RuleCorrection(2, "accra", "Accra", "location"),
        ]
        resolved = resolve_vetoes([("sege", "Sege"), ("accra", "Accra")], chunk)
        # The ambiguous "sege" veto is discarded; "accra" still resolves (Req 9.11).
        assert [r.correction.record_index for r in resolved] == [2]


class TestApplyVetoes:
    """apply_vetoes restores text / Words and prunes corrections (Req 9.2-9.4, 9.10)."""

    def test_restores_words_text_and_removes_one_entry(self):
        span = VetoSpan(
            original="kumase",
            corrected="Kumasi",
            original_words=[
                {"word": "kumase", "start": 1.0, "end": 1.4, "confidence": 0.5}
            ],
            entity_kind="location",
            output_index=1,
        )
        final_words = [
            {"word": "in", "start": 0.0, "end": 0.5},
            {"word": "Kumasi", "start": 1.0, "end": 1.4, "locationCorrected": True},
            {"word": "today", "start": 1.5, "end": 2.0},
        ]
        corrections = [
            _correction_record("world", "World"),
            _correction_record("kumase", "Kumasi"),
        ]
        rc = RuleCorrection(1, "kumase", "Kumasi", "location", veto_span=span)

        outcome = apply_vetoes(
            "in Kumasi today", final_words, corrections, [ResolvedVeto(rc)]
        )
        # Text restored, no occurrence of corrected text (Req 9.2, 9.10).
        assert outcome.final_text == "in kumase today"
        assert "Kumasi" not in outcome.final_text
        # Words restored to original count / timing (Req 9.4, 9.10).
        assert outcome.final_words[1]["word"] == "kumase"
        assert outcome.final_words[1]["start"] == 1.0
        assert outcome.final_words[1]["end"] == 1.4
        assert not any(w.get("word") == "Kumasi" for w in outcome.final_words)
        # Exactly the one entry removed, order preserved (Req 9.3).
        assert len(outcome.corrections) == 1
        assert outcome.corrections[0].original == "world"
        assert outcome.veto_count == 1

    def test_restores_merged_span_to_original_word_count(self):
        # A two-word merge "b b" -> "B.B. Carboo" restores two Words (Req 9.4).
        span = VetoSpan(
            original="b b",
            corrected="B.B. Carboo",
            original_words=[
                {"word": "b", "start": 1.0, "end": 1.2},
                {"word": "b", "start": 1.2, "end": 1.4},
            ],
            entity_kind="person",
            output_index=0,
        )
        final_words = [
            {"word": "B.B. Carboo", "start": 1.0, "end": 1.4, "locationCorrected": True},
        ]
        corrections = [_correction_record("b b", "B.B. Carboo", EntityKind.person)]
        rc = RuleCorrection(0, "b b", "B.B. Carboo", "person", veto_span=span)

        outcome = apply_vetoes(
            "B.B. Carboo", final_words, corrections, [ResolvedVeto(rc)]
        )
        assert [w["word"] for w in outcome.final_words] == ["b", "b"]
        assert outcome.corrections == []

    def test_missing_word_skips_veto(self):
        # Corrected Word not present → veto inapplicable, nothing changes.
        span = VetoSpan(
            original="kumase",
            corrected="Kumasi",
            original_words=[{"word": "kumase", "start": 1.0, "end": 1.4}],
            entity_kind="location",
            output_index=0,
        )
        final_words = [{"word": "elsewhere", "start": 0.0, "end": 0.5}]
        corrections = [_correction_record("kumase", "Kumasi")]
        rc = RuleCorrection(0, "kumase", "Kumasi", "location", veto_span=span)

        outcome = apply_vetoes(
            "elsewhere", final_words, corrections, [ResolvedVeto(rc)]
        )
        assert outcome.veto_count == 0
        assert len(outcome.corrections) == 1
        assert final_words[0]["word"] == "elsewhere"


# ===========================================================================
# Prompt review block + parser (Req 9.1)
# ===========================================================================


class TestVetoPrompt:
    def test_prompt_baseline_unchanged_when_no_corrections(self):
        # No veto corrections → prompt is byte-for-byte the Baseline prompt.
        base = build_system_prompt([])
        with_none = build_system_prompt([], None)
        with_empty = build_system_prompt([], [])
        assert base == with_none == with_empty
        assert VETO_OUTPUT_MARKER not in base

    def test_prompt_gains_review_block_with_corrections(self):
        prompt = build_system_prompt([], [("kumase", "Kumasi")])
        assert "RULE-BASED CORRECTIONS TO REVIEW" in prompt
        assert "kumase -> Kumasi" in prompt
        assert VETO_OUTPUT_MARKER in prompt

    def test_parse_none_marker_returns_empty(self):
        assert parse_veto_decisions(
            f"[Segment 1]: text\n{VETO_OUTPUT_MARKER} none"
        ) == []

    def test_parse_no_marker_returns_none(self):
        # No veto section at all → unparseable for veto (Req 9.8).
        assert parse_veto_decisions("[Segment 1]: just corrected text") is None

    def test_parse_decisions(self):
        raw = (
            "[Segment 1]: some text here\n"
            f"{VETO_OUTPUT_MARKER} kumase -> Kumasi; sage -> Sege"
        )
        assert parse_veto_decisions(raw) == [
            ("kumase", "Kumasi"),
            ("sage", "Sege"),
        ]


# ===========================================================================
# Pipeline integration — Bedrock mocked
# ===========================================================================


def _snapshot_with_alias():
    """Snapshot where 'Kumasi' has alias 'kumase' so a low-confidence word corrects."""
    records = [
        _record("Kumasi", EntityKind.location, EntityType.city, ["kumase", "kumasi"]),
    ]
    return _build_snapshot(records)


def _snapshot_with_person():
    records = [
        _record(
            "Ablakwa",
            EntityKind.person,
            EntityType.mp,
            ["ablakwa", "ablakua"],
        ),
    ]
    return _build_snapshot(records)


async def _run_veto_pipeline(request, snapshot, veto_results, *, settings=None, history=None):
    """Run the pipeline with refine_chunks mocked to return the given veto_results.

    ``refine_chunks`` is patched so the model's proposal path is a no-op and only
    the veto decisions we inject drive the restore. The patched function returns
    the words list unchanged plus our veto results, mirroring the real 4-tuple.
    The pipeline is awaited *inside* the patch context so the mock is active for
    the whole run.
    """
    settings = settings or _make_settings(llm_veto_enabled=True)
    cache = _make_cache(snapshot)
    client = _make_bedrock_client()

    async def fake_refine(words, snap, bc, session, st, veto_corrections_by_chunk=None):
        return words, "ok", 0, veto_results

    with patch("app.pipeline.refine_chunks", side_effect=fake_refine):
        return await run_pipeline(
            request,
            cache,
            bedrock_client=client,
            settings=settings,
            history_writer=history,
        )


class TestPipelineVetoRestore:
    """End-to-end veto restore through run_pipeline (Bedrock mocked)."""

    @pytest.mark.asyncio
    async def test_veto_restores_span_in_text_and_words(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        veto_results = [VetoResult(chunk_index=0, decisions=[("kumase", "Kumasi")])]
        response = await _run_veto_pipeline(request, snapshot, veto_results)

        # Corrected text no longer present anywhere (Req 9.2, 9.10).
        assert "Kumasi" not in response.transcript
        assert "kumase" in response.transcript
        assert not any(w.word == "Kumasi" for w in response.words)
        # Restored Word carries its original timing (Req 9.4).
        restored = next(w for w in response.words if w.word == "kumase")
        assert restored.start == 1.0
        assert restored.end == 1.4
        # Exactly one corrections entry removed (Req 9.3) and count reported (9.7).
        assert response.corrections == []
        assert response.metadata.vetoes == 1

    @pytest.mark.asyncio
    async def test_veto_nothing_leaves_correction(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        veto_results = [VetoResult(chunk_index=0, decisions=[])]
        response = await _run_veto_pipeline(request, snapshot, veto_results)

        assert any(w.word == "Kumasi" for w in response.words)
        # Both the text and word corrections for the span remain (Req 12.9).
        assert len(response.corrections) == 2
        # Veto counter omitted when 0 (Req 9.7, 14.4).
        assert response.metadata.vetoes is None

    @pytest.mark.asyncio
    async def test_unresolvable_veto_discarded(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        # Decision that resolves to no supplied correction → discarded (Req 9.11).
        veto_results = [VetoResult(chunk_index=0, decisions=[("banana", "Apple")])]
        response = await _run_veto_pipeline(request, snapshot, veto_results)

        assert any(w.word == "Kumasi" for w in response.words)
        assert len(response.corrections) == 2
        assert response.metadata.vetoes is None

    @pytest.mark.asyncio
    async def test_person_entity_veto_restores(self):
        snapshot = _snapshot_with_person()
        request = CorrectionRequest(
            transcript="hon ablakua spoke",
            words=[
                Word(word="hon", start=0.0, end=0.3, confidence=0.99),
                Word(word="ablakua", start=0.3, end=0.9, confidence=0.30),
                Word(word="spoke", start=0.9, end=1.2, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        # The rule stage corrects the mis-spelled surname to "Ablakwa"; veto it.
        veto_results = [VetoResult(chunk_index=0, decisions=[("ablakua", "Ablakwa")])]
        response = await _run_veto_pipeline(request, snapshot, veto_results)

        # Person correction restored rather than retained (Req 9.13).
        assert not any(w.word == "Ablakwa" for w in response.words)
        assert response.metadata.vetoes == 1

    @pytest.mark.asyncio
    async def test_chunk_failure_retains_corrections(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        # A failed/unparseable chunk contributes no veto result (Req 9.8):
        # empty veto_results list → nothing restored, correction retained.
        veto_results: list[VetoResult] = []
        response = await _run_veto_pipeline(request, snapshot, veto_results)

        assert any(w.word == "Kumasi" for w in response.words)
        assert len(response.corrections) == 2
        assert response.metadata.vetoes is None

    @pytest.mark.asyncio
    async def test_vetoed_history_record_enqueued(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        history = MagicMock()
        history.enqueue = MagicMock()
        veto_results = [VetoResult(chunk_index=0, decisions=[("kumase", "Kumasi")])]
        await _run_veto_pipeline(request, snapshot, veto_results, history=history)

        outcomes = [call.args[0].outcome for call in history.enqueue.call_args_list]
        # Exactly one vetoed record enqueued (Req 9.5).
        assert outcomes.count("vetoed") == 1

    @pytest.mark.asyncio
    async def test_history_failure_does_not_affect_restore(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        history = MagicMock()
        # enqueue raises — a dropped/failed history write must not surface and
        # must not affect the restored Span or veto count (Req 9.12).
        history.enqueue = MagicMock(side_effect=RuntimeError("queue exploded"))
        veto_results = [VetoResult(chunk_index=0, decisions=[("kumase", "Kumasi")])]

        try:
            response = await _run_veto_pipeline(
                request, snapshot, veto_results, history=history
            )
        except RuntimeError:
            pytest.fail("history enqueue failure must not surface (Req 9.12)")
        assert response.metadata.vetoes == 1
        assert "Kumasi" not in response.transcript


class TestPipelineVetoBaseline:
    """Flag-off behaviour is byte-for-byte Baseline (Req 12.9 / 14.4)."""

    @pytest.mark.asyncio
    async def test_flag_off_no_veto_metadata_and_correction_kept(self):
        snapshot = _snapshot_with_alias()
        request = CorrectionRequest(
            transcript="in kumase today",
            words=[
                Word(word="in", start=0.0, end=0.5, confidence=0.99),
                Word(word="kumase", start=1.0, end=1.4, confidence=0.30),
                Word(word="today", start=1.5, end=2.0, confidence=0.99),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        settings = _make_settings(llm_veto_enabled=False)
        cache = _make_cache(snapshot)
        client = _make_bedrock_client()

        captured = {}

        async def fake_refine(words, snap, bc, session, st, veto_corrections_by_chunk=None):
            captured["veto_arg"] = veto_corrections_by_chunk
            return words, "ok", 0, []

        with patch("app.pipeline.refine_chunks", side_effect=fake_refine):
            response = await run_pipeline(
                request, cache, bedrock_client=client, settings=settings
            )

        # With the flag off the refiner is never handed veto corrections (Req 12.9).
        assert captured["veto_arg"] is None
        # No veto metadata field emitted (Req 14.4).
        assert response.metadata.vetoes is None
        dumped = response.metadata.model_dump(by_alias=True, exclude_none=True)
        assert "vetoes" not in dumped
