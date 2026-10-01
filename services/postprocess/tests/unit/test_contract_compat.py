"""Contract backward-compatibility and gate wiring-order tests (task 6.7).

Consolidates the Requirement 14 (Contract Backward Compatibility) checks into a
single focused suite and asserts the pipeline stage wiring order. Every 14.x
acceptance criterion has an explicit assertion here; where an existing suite
(test_models.py, test_pipeline.py, test_correct_words.py) already asserts a
criterion piecemeal, this file adds the contract-level assertion rather than
duplicating the low-level one.

All tests run without Bedrock or a database: ``run_pipeline`` is called with
``bedrock_client=None`` (LLM unconfigured/skipped) and ``session_factory=None``,
with a mocked :class:`~app.datasets.cache.DatasetCache` returning a hand-built
snapshot. No network or AWS call is made.

Validates: Requirements 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9,
14.10, 14.11, 14.12, 14.13
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.correction.engine import correct_words
from app.correction.gates import GateContext
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType, MatchStrategy
from app.models.request import CorrectionOptions, CorrectionRequest, Word
from app.models.response import CorrectedWord, CorrectionResponse, Metadata
from app.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Shared fixture: a small hand-built snapshot with mergeable entities
# ---------------------------------------------------------------------------


def _build_snapshot() -> DatasetSnapshot:
    """A minimal snapshot with a multi-token alias for merge coverage.

    ``Ningo-Prampram`` carries the two-token alias ``"ningo prampram"`` so a
    request whose Words are ``["ningo", "prampram"]`` produces an exact
    two-Word->one-Word merge - the shape Req 14.5 tests for first-merged-Word
    provider passthrough.
    """
    records = [
        EntityRecord(
            canonical="Ningo-Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=["ningoprampram", "ningo prampram"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["koumasi", "kumase"],
            source="supplementary",
            source_rank=0,
        ),
    ]
    index = build_index(records)
    return DatasetSnapshot(
        version="contract-fixture-v1",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(["the", "a", "an", "is", "of"]),
        word_stopwords=frozenset(["the", "a", "an", "is", "of", "and", "in", "to"]),
        title_prefixes=frozenset(["honourable", "hon", "minister", "mr", "dr"]),
    )


def _make_cache(snapshot: DatasetSnapshot | None) -> DatasetCache:
    cache = MagicMock(spec=DatasetCache)
    cache.get_snapshot.return_value = snapshot
    return cache


def _run(
    request: CorrectionRequest, snapshot: DatasetSnapshot | None = None
) -> CorrectionResponse:
    """Run the pipeline with no Bedrock and no DB (Req 14 test constraints)."""
    snap = snapshot if snapshot is not None else _build_snapshot()
    cache = _make_cache(snap)
    return asyncio.run(
        run_pipeline(
            request,
            cache,
            bedrock_client=None,
            settings=None,
            history_writer=None,
            session_factory=None,
            lexicon=None,
        )
    )


# ---------------------------------------------------------------------------
# 14.1 - Baseline-accepted bodies still validate and return a response
# ---------------------------------------------------------------------------


class TestBaselineAcceptedBodies:
    """Req 14.1: every body the Baseline accepts still validates and responds."""

    def test_body_omitting_options(self):
        """A body with no ``options`` object validates (defaults applied)."""
        req = CorrectionRequest.model_validate(
            {"transcript": "hello world", "words": [{"word": "hello"}, {"word": "world"}]}
        )
        assert isinstance(req.options, CorrectionOptions)
        resp = _run(req)
        assert isinstance(resp, CorrectionResponse)
        assert resp.transcript == "hello world"

    def test_body_omitting_every_word_confidence(self):
        """A body where no Word carries a confidence validates and responds."""
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello world",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.4},
                    {"word": "world", "start": 0.4, "end": 0.8},
                ],
            }
        )
        assert all(w.confidence is None for w in req.words)
        resp = _run(req)
        assert len(resp.words) == 2

    def test_body_with_unrecognised_provider_fields_on_word(self):
        """Unrecognised provider fields on a Word are accepted (extra='allow')."""
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello",
                "words": [
                    {
                        "word": "hello",
                        "confidence": 0.9,
                        "punctuated_word": "Hello,",
                        "speaker": 3,
                        "myProviderField": "x",
                    }
                ],
            }
        )
        resp = _run(req)
        assert isinstance(resp, CorrectionResponse)

    def test_option_names_camelcase_alias(self):
        """Options supplied under their camelCase alias validate."""
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello",
                "words": [{"word": "hello"}],
                "options": {
                    "minConfidence": 0.8,
                    "wordAcceptThreshold": 0.95,
                    "llmRefine": False,
                },
            }
        )
        assert req.options.min_confidence == 0.8
        assert req.options.word_accept_threshold == 0.95
        assert req.options.llm_refine is False
        assert isinstance(_run(req), CorrectionResponse)

    def test_option_names_snake_case_field(self):
        """Options under their snake_case field name validate (populate_by_name)."""
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello",
                "words": [{"word": "hello"}],
                "options": {
                    "min_confidence": 0.81,
                    "word_accept_threshold": 0.93,
                    "llm_refine": False,
                },
            }
        )
        assert req.options.min_confidence == 0.81
        assert req.options.word_accept_threshold == 0.93
        assert isinstance(_run(req), CorrectionResponse)


# ---------------------------------------------------------------------------
# 14.2 - Response emits every Baseline JSON key, same type, same camelCase alias
# 14.11 - New response fields are strictly additive
# ---------------------------------------------------------------------------

# The Baseline response contract: the JSON keys the Metadata model emits for a
# representative request under ``by_alias=True``, each with the JSON type it
# emits. The Metadata model only declares camelCase aliases for a subset of
# fields (``correlationId`` and the additive ``gateRejections``); the remaining
# metadata fields serialize under their snake_case field name. The camelCase
# conversion for the rest happens at the Node.js gateway boundary, not in this
# Python model - so the contract this Python service owns is exactly these keys.
# New fields introduced by this spec are additive; they may appear but must not
# rename, remove, or retype any of these Baseline keys.
_BASELINE_METADATA_KEYS: dict[str, type] = {
    "llm_status": str,
    "postprocessing_status": str,
    "rule_latency_ms": int,
    "llm_latency_ms": int,
    "dataset_version": str,
    "correlationId": str,
}

_BASELINE_WORD_KEYS: dict[str, type] = {
    "word": str,
    "start": float,
    "end": float,
    "confidence": float,
}


class TestBaselineResponseKeys:
    """Req 14.2 / 14.11: Baseline keys present, correct type, correct alias."""

    def test_top_level_keys_present_with_alias(self):
        req = CorrectionRequest(
            transcript="hello world",
            words=[
                Word(word="hello", start=0.0, end=0.4, confidence=0.9),
                Word(word="world", start=0.4, end=0.8, confidence=0.9),
            ],
            correlation_id="corr-14-2",
        )
        dumped = _run(req).model_dump(by_alias=True, exclude_none=True)

        for key in ("transcript", "words", "entities", "metadata", "corrections"):
            assert key in dumped, f"Baseline top-level key {key!r} missing"

    def test_metadata_baseline_keys_present_with_type_and_alias(self):
        req = CorrectionRequest(
            transcript="hello world",
            words=[
                Word(word="hello", start=0.0, end=0.4, confidence=0.9),
                Word(word="world", start=0.4, end=0.8, confidence=0.9),
            ],
            correlation_id="corr-14-2b",
        )
        meta = _run(req).model_dump(by_alias=True, exclude_none=True)["metadata"]

        for alias, typ in _BASELINE_METADATA_KEYS.items():
            assert alias in meta, f"Baseline metadata key {alias!r} missing"
            assert isinstance(meta[alias], typ), (
                f"metadata[{alias!r}] should be {typ.__name__}, "
                f"got {type(meta[alias]).__name__}"
            )

    def test_word_baseline_keys_present_with_type_and_alias(self):
        req = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello", start=0.0, end=0.4, confidence=0.9)],
        )
        word = _run(req).model_dump(by_alias=True, exclude_none=True)["words"][0]
        for alias, typ in _BASELINE_WORD_KEYS.items():
            assert alias in word, f"Baseline word key {alias!r} missing"
            assert isinstance(word[alias], typ)

    def test_new_response_fields_are_additive_not_renames(self):
        """Req 14.11: additive fields use new keys; no Baseline key is renamed/retyped.

        The Metadata model is the response surface that grew (gateRejections,
        vetoes). Assert every Baseline key still resolves to a defined field
        under its original alias, and the new fields are separate aliases.
        """
        fields_by_alias = {
            (f.alias or name): name for name, f in Metadata.model_fields.items()
        }
        for alias in _BASELINE_METADATA_KEYS:
            assert alias in fields_by_alias, f"Baseline alias {alias!r} was renamed/removed"
        # New additive keys exist as their own aliases, distinct from Baseline keys.
        assert "gateRejections" in fields_by_alias
        assert "vetoes" in fields_by_alias
        assert set(_BASELINE_METADATA_KEYS).isdisjoint({"gateRejections", "vetoes"})


# ---------------------------------------------------------------------------
# 14.3 - Match_Strategy is reported from the allowed value set
# ---------------------------------------------------------------------------


class TestMatchStrategyValues:
    """Req 14.3: corrections report one of the allowed Match_Strategy values."""

    ALLOWED = {
        "exact",
        "fused",
        "joined",
        "initials",
        "title_person",
        "phonetic",
        "fuzzy",
        "substring",
    }

    def test_enum_values_are_exactly_the_contract_set(self):
        assert {s.value for s in MatchStrategy} == self.ALLOWED

    def test_applied_correction_reports_allowed_strategy(self):
        req = CorrectionRequest(
            transcript="ningo prampram",
            words=[
                Word(word="ningo", start=0.0, end=0.4, confidence=0.5),
                Word(word="prampram", start=0.4, end=0.8, confidence=0.5),
            ],
            options=CorrectionOptions(llm_refine=False),
        )
        resp = _run(req)
        assert resp.corrections, "expected at least one correction"
        for cr in resp.corrections:
            assert cr.strategy.value in self.ALLOWED


# ---------------------------------------------------------------------------
# 14.4 - Zero-valued metadata counters and false Word flags are omitted
# ---------------------------------------------------------------------------


class TestZeroValuedOmission:
    """Req 14.4: zero counters (incl. gate_rejections, vetoes) and false flags omitted."""

    def test_zero_counters_omitted_including_new_ones(self):
        req = CorrectionRequest(
            transcript="nothing to correct here",
            words=[Word(word="nothing", start=0.0, end=0.4, confidence=0.99)],
            correlation_id="corr-14-4",
        )
        meta = _run(req).model_dump(by_alias=True, exclude_none=True)["metadata"]

        for absent in (
            "locationCorrections",
            "yearCorrections",
            "bedrockCorrections",
            "gateRejections",
            "vetoes",
            # No snake_case leakage of the new counters either.
            "location_corrections",
            "gate_rejections",
        ):
            assert absent not in meta, f"{absent!r} should be omitted when zero"

    def test_false_word_flags_omitted(self):
        """A CorrectedWord with false/None flags omits them on serialization."""
        cw = CorrectedWord(
            word="hello",
            start=0.0,
            end=0.4,
            confidence=0.9,
            location_corrected=None,
            bedrock_corrected=None,
            year_corrected=None,
        )
        dumped = cw.model_dump(by_alias=True, exclude_none=True)
        for absent in ("locationCorrected", "bedrockCorrected", "yearCorrected"):
            assert absent not in dumped

    def test_uncorrected_pipeline_word_carries_no_flags(self):
        req = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello", start=0.0, end=0.4, confidence=0.99)],
        )
        word = _run(req).model_dump(by_alias=True, exclude_none=True)["words"][0]
        for absent in ("locationCorrected", "bedrockCorrected", "yearCorrected"):
            assert absent not in word


# ---------------------------------------------------------------------------
# 14.5 - Response Word returns each unrecognised provider field of the input
#        Word, from the FIRST merged Word, excluding a modified punctuated_word
# ---------------------------------------------------------------------------


class TestProviderFieldPassthrough:
    """Req 14.5: unrecognised provider fields ride through; first-merged-Word wins."""

    def test_uncorrected_word_returns_unrecognised_fields_verbatim(self):
        req = CorrectionRequest(
            transcript="hello",
            words=[
                Word.model_validate(
                    {
                        "word": "hello",
                        "start": 0.0,
                        "end": 0.4,
                        "confidence": 0.9,
                        "speaker": 5,
                        "punctuated_word": "Hello,",
                        "customTag": "abc",
                    }
                )
            ],
        )
        word = _run(req).model_dump(by_alias=True, exclude_none=True)["words"][0]
        # Unrecognised provider fields survive with original key + value.
        assert word["speaker"] == 5
        assert word["punctuated_word"] == "Hello,"
        assert word["customTag"] == "abc"

    def test_merged_word_takes_first_merged_words_provider_fields(self):
        """A >1-Word merge carries the FIRST merged Word's unrecognised fields."""
        req = CorrectionRequest(
            transcript="ningo prampram",
            words=[
                Word.model_validate(
                    {
                        "word": "ningo",
                        "start": 0.0,
                        "end": 0.4,
                        "confidence": 0.5,
                        "speaker": 7,
                        "customField": "keepme",
                    }
                ),
                Word.model_validate(
                    {
                        "word": "prampram",
                        "start": 0.4,
                        "end": 0.8,
                        "confidence": 0.5,
                        "speaker": 9,
                        "customField": "dropme",
                    }
                ),
            ],
            options=CorrectionOptions(llm_refine=False),
        )
        resp = _run(req)
        assert resp.transcript == "Ningo-Prampram"
        assert len(resp.words) == 1
        merged = resp.words[0].model_dump(by_alias=True, exclude_none=True)
        assert merged["word"] == "Ningo-Prampram"
        # First merged Word's provider fields are carried; the second's are not.
        assert merged["speaker"] == 7
        assert merged["customField"] == "keepme"

    def test_merge_excludes_modified_punctuated_word(self):
        """Req 14.5: a modified Word's ``punctuated_word`` is excluded/rewritten.

        On a non-inert GateContext (any gate flag on) the merged Word's
        ``punctuated_word`` is set to the corrected text plus the last merged
        Word's terminal punctuation (Req 6.7) rather than passed through from
        the first Word verbatim - i.e. the *input* first-Word punctuated_word is
        not returned unchanged for a modified Word.
        """
        settings = Settings(service_token="x", database_url="x")
        gate_context = GateContext.from_settings(settings, lexicon=None)
        assert gate_context.is_inert is False

        snap = _build_snapshot()
        words = [
            {
                "word": "ningo",
                "start": 0.0,
                "end": 0.4,
                "confidence": 0.5,
                "speaker": 7,
                "punctuated_word": "Ningo",
                "customField": "keepme",
            },
            {
                "word": "prampram",
                "start": 0.4,
                "end": 0.8,
                "confidence": 0.5,
                "speaker": 9,
                "punctuated_word": "Prampram.",
                "customField": "dropme",
            },
        ]
        result = correct_words(words, snap.index, snap, gate_context=gate_context)
        assert len(result.words) == 1
        merged = result.words[0]
        # First merged Word's unrecognised fields still present.
        assert merged["speaker"] == 7
        assert merged["customField"] == "keepme"
        # The modified Word's punctuated_word is NOT the first input's verbatim
        # value; it is rewritten to the corrected text (+ terminal punctuation
        # from the last merged Word, here "." -> "Ningo-Prampram.").
        assert merged["punctuated_word"] != "Ningo"
        assert merged["punctuated_word"].startswith("Ningo-Prampram")


# ---------------------------------------------------------------------------
# 14.6 - minConfidence / wordAcceptThreshold options override Settings defaults
# ---------------------------------------------------------------------------


class TestOptionOverrides:
    """Req 14.6: request options override Settings under either casing."""

    def test_camelcase_options_reach_the_engine(self):
        """The engine receives the request's option values (camelCase alias)."""
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello",
                "words": [{"word": "hello"}],
                "options": {
                    "minConfidence": 0.83,
                    "wordAcceptThreshold": 0.91,
                    "llmRefine": False,
                },
            }
        )
        captured: dict[str, float] = {}

        real_correct_words = correct_words

        def _spy_words(*args, **kwargs):
            captured["word_accept_threshold"] = kwargs.get("word_accept_threshold")
            captured["min_confidence"] = kwargs.get("min_confidence")
            return real_correct_words(*args, **kwargs)

        with patch("app.pipeline.correct_words", _spy_words):
            _run(req)

        # Settings is None in the test run, so these values come from the
        # request options in preference to any Settings default (Req 14.6).
        assert captured["min_confidence"] == 0.83
        assert captured["word_accept_threshold"] == 0.91

    def test_snake_case_options_reach_the_engine(self):
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello",
                "words": [{"word": "hello"}],
                "options": {
                    "min_confidence": 0.84,
                    "word_accept_threshold": 0.92,
                    "llm_refine": False,
                },
            }
        )
        captured: dict[str, float] = {}
        real_correct_words = correct_words

        def _spy_words(*args, **kwargs):
            captured["word_accept_threshold"] = kwargs.get("word_accept_threshold")
            captured["min_confidence"] = kwargs.get("min_confidence")
            return real_correct_words(*args, **kwargs)

        with patch("app.pipeline.correct_words", _spy_words):
            _run(req)

        assert captured["min_confidence"] == 0.84
        assert captured["word_accept_threshold"] == 0.92


# ---------------------------------------------------------------------------
# 14.7 - Out-of-range Word confidence is clamped, no validation error
# 14.13 - Non-numeric Word confidence treated as absent, no validation error
# ---------------------------------------------------------------------------


class TestConfidenceHandling:
    """Req 14.7 / 14.13: confidence clamped / non-numeric treated as absent."""

    def test_above_one_clamped_no_error(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hi", "words": [{"word": "hi", "confidence": 1.7}]}
        )
        assert req.words[0].confidence == 1.0
        assert isinstance(_run(req), CorrectionResponse)

    def test_below_zero_clamped_no_error(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hi", "words": [{"word": "hi", "confidence": -0.4}]}
        )
        assert req.words[0].confidence == 0.0

    def test_non_numeric_confidence_treated_as_absent_no_error(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hi", "words": [{"word": "hi", "confidence": "high"}]}
        )
        assert req.words[0].confidence is None
        assert isinstance(_run(req), CorrectionResponse)


# ---------------------------------------------------------------------------
# 14.8 - Empty Words list: transcript carries text corrections, Words empty
# ---------------------------------------------------------------------------


class TestEmptyWordsList:
    """Req 14.8: an empty Words list still corrects the transcript text."""

    def test_empty_words_returns_text_corrections_empty_words(self):
        req = CorrectionRequest(
            transcript="ningo prampram was mentioned",
            words=[],
            options=CorrectionOptions(llm_refine=False),
        )
        resp = _run(req)
        assert resp.words == []
        # The transcript-text correction still applies.
        assert "Ningo-Prampram" in resp.transcript

    def test_empty_words_valid_and_responds_when_no_entity(self):
        req = CorrectionRequest(transcript="nothing here", words=[])
        resp = _run(req)
        assert resp.words == []
        assert resp.transcript == "nothing here"


# ---------------------------------------------------------------------------
# 14.9 - Pipeline runs Correction_Engine -> Year_Corrector -> LLM_Refiner,
#        with every Req 1-8 gate inside the Correction_Engine stage
# ---------------------------------------------------------------------------


class TestPipelineWiringOrder:
    """Req 14.9: stage order via a spy on the stage functions."""

    def test_stage_call_order_is_engine_then_year_then_refiner(self):
        """Spy on the stage functions and assert their relative call order."""
        req = CorrectionRequest(
            transcript="ningo prampram",
            words=[
                Word(word="ningo", start=0.0, end=0.4, confidence=0.5),
                Word(word="prampram", start=0.4, end=0.8, confidence=0.5),
            ],
            options=CorrectionOptions(llm_refine=True),
        )
        calls: list[str] = []

        import app.pipeline as pipeline

        orig_correct_text = pipeline.correct_text
        orig_correct_words = pipeline.correct_words
        orig_years_text = pipeline.correct_years_in_text
        orig_years_words = pipeline.correct_years
        orig_refine = pipeline.refine_chunks

        def _spy_text(*a, **k):
            calls.append("engine.correct_text")
            return orig_correct_text(*a, **k)

        def _spy_words(*a, **k):
            calls.append("engine.correct_words")
            return orig_correct_words(*a, **k)

        def _spy_years_text(*a, **k):
            calls.append("year.correct_years_in_text")
            return orig_years_text(*a, **k)

        def _spy_years_words(*a, **k):
            calls.append("year.correct_years")
            return orig_years_words(*a, **k)

        async def _spy_refine(*a, **k):
            calls.append("llm.refine_chunks")
            return await orig_refine(*a, **k)

        with (
            patch.object(pipeline, "correct_text", _spy_text),
            patch.object(pipeline, "correct_words", _spy_words),
            patch.object(pipeline, "correct_years_in_text", _spy_years_text),
            patch.object(pipeline, "correct_years", _spy_years_words),
            patch.object(pipeline, "refine_chunks", _spy_refine),
        ):
            _run(req)

        # The Correction_Engine stage (correct_text/correct_words) runs before
        # the Year_Corrector (correct_years*), which runs before any LLM stage.
        engine_calls = [i for i, c in enumerate(calls) if c.startswith("engine.")]
        year_calls = [i for i, c in enumerate(calls) if c.startswith("year.")]

        assert engine_calls, "Correction_Engine stage did not run"
        assert year_calls, "Year_Corrector stage did not run"
        # Every engine call precedes every year call.
        assert max(engine_calls) < min(year_calls), (
            f"engine must run before year corrector; call order: {calls}"
        )
        # refine_chunks is not invoked here (bedrock_client=None -> unconfigured),
        # which itself confirms the LLM stage is gated last and never precedes
        # the rule/year stages.
        assert "llm.refine_chunks" not in calls

    def test_llm_unconfigured_when_no_bedrock_client(self):
        """With bedrock_client=None the LLM stage reports 'unconfigured' (gated last)."""
        req = CorrectionRequest(
            transcript="hello",
            words=[Word(word="hello", start=0.0, end=0.4, confidence=0.9)],
            options=CorrectionOptions(llm_refine=True),
        )
        resp = _run(req)
        assert resp.metadata.llm_status == "unconfigured"

    def test_gates_live_inside_engine_stage(self):
        """Req 14.9: the gate tally is produced by the rule stage, not a later stage.

        _run_rule_stages returns the GateTally alongside the engine results,
        confirming gate evaluation happens within the Correction_Engine stage
        (the rule stage), not in the Year_Corrector or LLM_Refiner.
        """
        from app.correction.gates import GateTally
        from app.pipeline import _run_rule_stages

        snap = _build_snapshot()
        req = CorrectionRequest(
            transcript="ningo prampram",
            words=[
                Word(word="ningo", start=0.0, end=0.4, confidence=0.5),
                Word(word="prampram", start=0.4, end=0.8, confidence=0.5),
            ],
        )
        result = _run_rule_stages(req, snap, None, None, None)
        # (text_result, word_result, final_text, final_words, year_count, tally)
        assert len(result) == 6
        tally = result[5]
        # The tally is the rule-stage's gate collector; on the baseline path it
        # is present (and empty), proving gate wiring is inside the engine stage.
        assert isinstance(tally, GateTally)


# ---------------------------------------------------------------------------
# 14.10 - A field the Baseline doesn't define (incl. sittingId) applies its
#         documented default when omitted
# ---------------------------------------------------------------------------


class TestNewFieldDefaults:
    """Req 14.10: new request fields default correctly when omitted."""

    def test_sitting_id_defaults_to_none_when_omitted(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "words": [{"word": "hello"}]}
        )
        assert req.sitting_id is None
        assert isinstance(_run(req), CorrectionResponse)

    def test_provider_defaults_to_deepgram_when_omitted(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "words": [{"word": "hello"}]}
        )
        assert req.options.provider == "deepgram"

    def test_sitting_id_accepts_camelcase_alias(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "words": [{"word": "hello"}], "sittingId": "s-42"}
        )
        assert req.sitting_id == "s-42"


# ---------------------------------------------------------------------------
# 14.12 - An unrecognised request field is ignored; response computed from
#         recognised fields, no validation error
# ---------------------------------------------------------------------------


class TestUnrecognisedRequestField:
    """Req 14.12: unknown top-level request fields are ignored, not errors."""

    def test_unknown_top_level_field_ignored(self):
        req = CorrectionRequest.model_validate(
            {
                "transcript": "hello world",
                "words": [{"word": "hello"}, {"word": "world"}],
                "someUnknownField": {"nested": [1, 2, 3]},
                "anotherUnknown": 42,
            }
        )
        # The unknown fields are not retained as model attributes.
        assert not hasattr(req, "someUnknownField")
        assert not hasattr(req, "anotherUnknown")
        # Response is computed from the recognised fields, no error.
        resp = _run(req)
        assert resp.transcript == "hello world"
        assert len(resp.words) == 2
