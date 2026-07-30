"""Property tests for request and response serialization round-trip equality.

Validates: Requirements 2.7, 2.8, 15.4

These tests use Hypothesis to generate arbitrary valid model instances and
verify that serializing to JSON and deserializing back produces an equal
model instance. This proves the wire-format contract is lossless for both
the request (including extra="allow" passthrough on Word) and the response
(including exclude_none parity for flags and counters).
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.entities import (
    CorrectionRecord,
    EntityKind,
    EntityType,
    MatchStrategy,
)
from app.models.request import CorrectionOptions, CorrectionRequest, Word
from app.models.response import (
    CorrectedWord,
    CorrectionResponse,
    EntitySummary,
    Metadata,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Reusable alphabet for extra field keys (letters only, valid identifiers)
_identifier_alphabet = st.characters(categories=("L",))

# Reserved field names and aliases that the models use — extras must not collide
_RESERVED_KEYS = frozenset({
    "word", "start", "end", "confidence",
    "locationCorrected", "location_corrected",
    "bedrockCorrected", "bedrock_corrected",
    "yearCorrected", "year_corrected",
    "entityKind", "entity_kind",
    "entityType", "entity_type",
    "llmRefine", "llm_refine",
    "minConfidence", "min_confidence",
    "wordAcceptThreshold", "word_accept_threshold",
    "provider", "transcript", "words", "options",
    "correlationId", "correlation_id",
})

# Extra fields strategy — simulates unknown provider fields riding through.
# Keys must not collide with any model field name or alias to avoid Pydantic
# interpreting them as typed fields (which causes validation errors).
_extra_fields = st.dictionaries(
    keys=st.text(min_size=1, max_size=20, alphabet=_identifier_alphabet).filter(
        lambda k: k not in _RESERVED_KEYS
    ),
    values=st.one_of(
        st.text(min_size=1, max_size=50),
        st.integers(min_value=-1000, max_value=1000),
        st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
        st.booleans(),
    ),
    max_size=5,
)

# Confidence values within [0.0, 1.0] or None
_confidence = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)


# StrEnum strategies
_entity_kind = st.sampled_from(list(EntityKind))
_entity_type = st.sampled_from(list(EntityType))
_match_strategy = st.sampled_from(list(MatchStrategy))
_provider = st.sampled_from(["deepgram", "khaya", "hybrid"])

# Timing values
_timing = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=7200.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def word_strategy(draw: st.DrawFn) -> Word:
    """Generate a valid Word instance with optional extra fields."""
    extras = draw(_extra_fields)
    w = Word(
        word=draw(st.text(min_size=1, max_size=50)),
        start=draw(_timing),
        end=draw(_timing),
        confidence=draw(_confidence),
        **extras,
    )
    return w


@st.composite
def correction_options_strategy(draw: st.DrawFn) -> CorrectionOptions:
    """Generate a valid CorrectionOptions instance."""
    return CorrectionOptions(
        llm_refine=draw(st.booleans()),
        min_confidence=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        word_accept_threshold=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        provider=draw(_provider),
    )


@st.composite
def correction_request_strategy(draw: st.DrawFn) -> CorrectionRequest:
    """Generate a valid CorrectionRequest instance."""
    return CorrectionRequest(
        transcript=draw(st.text(min_size=0, max_size=200)),
        words=draw(st.lists(word_strategy(), min_size=0, max_size=10)),
        options=draw(correction_options_strategy()),
        correlation_id=draw(st.one_of(st.none(), st.uuids().map(str))),
    )



@st.composite
def corrected_word_strategy(draw: st.DrawFn) -> CorrectedWord:
    """Generate a valid CorrectedWord instance with optional extra fields."""
    extras = draw(_extra_fields)
    return CorrectedWord(
        word=draw(st.text(min_size=1, max_size=50)),
        start=draw(_timing),
        end=draw(_timing),
        confidence=draw(_confidence),
        location_corrected=draw(st.one_of(st.none(), st.just(True))),
        bedrock_corrected=draw(st.one_of(st.none(), st.just(True))),
        year_corrected=draw(st.one_of(st.none(), st.just(True))),
        entity_kind=draw(st.one_of(st.none(), _entity_kind)),
        entity_type=draw(st.one_of(st.none(), _entity_type)),
        **extras,
    )


@st.composite
def entity_summary_strategy(draw: st.DrawFn) -> EntitySummary:
    """Generate a valid EntitySummary instance."""
    return EntitySummary(
        name=draw(st.text(min_size=1, max_size=50)),
        kind=draw(_entity_kind),
        type=draw(_entity_type),
        mentions=draw(st.integers(min_value=1, max_value=100)),
    )


@st.composite
def metadata_strategy(draw: st.DrawFn) -> Metadata:
    """Generate a valid Metadata instance."""
    return Metadata(
        location_corrections=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=50))),
        year_corrections=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=50))),
        bedrock_corrections=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=50))),
        llm_status=draw(st.one_of(st.none(), st.sampled_from(["ok", "partial", "failed", "skipped", "unconfigured"]))),
        postprocessing_status=draw(st.one_of(st.none(), st.sampled_from(["applied", "skipped", "disabled"]))),
        rule_latency_ms=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=5000))),
        llm_latency_ms=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=30000))),
        dataset_version=draw(st.one_of(st.none(), st.text(min_size=1, max_size=30))),
        correlation_id=draw(st.one_of(st.none(), st.uuids().map(str))),
    )


@st.composite
def correction_record_strategy(draw: st.DrawFn) -> CorrectionRecord:
    """Generate a valid CorrectionRecord instance."""
    return CorrectionRecord(
        original=draw(st.text(min_size=1, max_size=50)),
        corrected=draw(st.text(min_size=1, max_size=50)),
        strategy=draw(_match_strategy),
        confidence=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        entity_kind=draw(_entity_kind),
        entity_type=draw(_entity_type),
    )


@st.composite
def correction_response_strategy(draw: st.DrawFn) -> CorrectionResponse:
    """Generate a valid CorrectionResponse instance."""
    return CorrectionResponse(
        transcript=draw(st.text(min_size=0, max_size=200)),
        words=draw(st.lists(corrected_word_strategy(), min_size=0, max_size=10)),
        entities=draw(st.lists(entity_summary_strategy(), min_size=0, max_size=5)),
        metadata=draw(metadata_strategy()),
        corrections=draw(st.lists(correction_record_strategy(), min_size=0, max_size=5)),
    )



# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(request=correction_request_strategy())
@settings(max_examples=200)
def test_request_roundtrip_preserves_equality(request: CorrectionRequest) -> None:
    """Correction_Request serialization round-trip preserves model equality.

    Serialize with by_alias=True (matching the wire format), convert to a JSON
    string and back, then deserialize into a new CorrectionRequest. The result
    must equal the original.

    This covers:
    - Extra fields on Word survive the round-trip (extra="allow" passthrough)
    - Confidence clamping is idempotent (already-clamped values don't change)
    - Empty words list works
    - Aliases resolve correctly (correlationId, llmRefine, etc.)

    **Validates: Requirements 2.7, 15.4**
    """
    # Serialize to dict with aliases (wire format)
    serialized = request.model_dump(by_alias=True)

    # Convert to JSON string and back — proves actual wire-format round-trip
    json_str = json.dumps(serialized)
    deserialized_dict = json.loads(json_str)

    # Reconstruct model from the deserialized dict
    reconstructed = CorrectionRequest.model_validate(deserialized_dict)

    assert reconstructed == request


@given(response=correction_response_strategy())
@settings(max_examples=200)
def test_response_roundtrip_preserves_equality(response: CorrectionResponse) -> None:
    """Correction_Response serialization round-trip preserves model equality.

    Serialize with by_alias=True, exclude_none=True (matching the wire format
    where false flags and zero counters are omitted), convert to a JSON string
    and back, then deserialize into a new CorrectionResponse. The result must
    equal the original.

    This covers:
    - None flags are omitted and stay None on deserialization
    - Extra fields on CorrectedWord survive the round-trip
    - corrections list with various strategies round-trips correctly
    - EntitySummary entries are preserved

    **Validates: Requirements 2.8, 15.4**
    """
    # Serialize to dict with aliases and excluding None values (wire format)
    serialized = response.model_dump(by_alias=True, exclude_none=True)

    # Convert to JSON string and back — proves actual wire-format round-trip
    json_str = json.dumps(serialized)
    deserialized_dict = json.loads(json_str)

    # Reconstruct model from the deserialized dict
    reconstructed = CorrectionResponse.model_validate(deserialized_dict)

    assert reconstructed == response
