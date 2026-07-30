"""Unit tests for Word field fidelity: extra passthrough, confidence clamping,
structural validation, and CorrectedWord flag omission.

Requirements: 2.6, 1.6
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.request import CorrectionRequest, Word
from app.models.response import CorrectedWord


# ---------------------------------------------------------------------------
# 1. Extra field passthrough
# ---------------------------------------------------------------------------


class TestExtraFieldPassthrough:
    """Unknown provider fields survive serialization with original spelling."""

    def test_punctuated_word_survives(self):
        w = Word(word="hello", confidence=0.9, punctuated_word="Hello,")
        dumped = w.model_dump(by_alias=True)
        assert dumped["punctuated_word"] == "Hello,"

    def test_speaker_survives(self):
        w = Word(word="parliament", start=1.0, end=1.5, speaker=2)
        dumped = w.model_dump(by_alias=True)
        assert dumped["speaker"] == 2

    def test_arbitrary_unknown_field_survives(self):
        w = Word(word="test", myCustomField="value123")
        dumped = w.model_dump(by_alias=True)
        assert dumped["myCustomField"] == "value123"

    def test_multiple_extras_survive(self):
        w = Word(
            word="example",
            confidence=0.8,
            punctuated_word="Example.",
            speaker=1,
            language="en",
        )
        dumped = w.model_dump(by_alias=True)
        assert dumped["punctuated_word"] == "Example."
        assert dumped["speaker"] == 1
        assert dumped["language"] == "en"


# ---------------------------------------------------------------------------
# 2. Confidence clamping
# ---------------------------------------------------------------------------


class TestConfidenceClamping:
    """Out-of-range confidence is clamped, not rejected."""

    def test_above_one_clamped_to_one(self):
        w = Word(word="hi", confidence=1.5)
        assert w.confidence == 1.0

    def test_below_zero_clamped_to_zero(self):
        w = Word(word="hi", confidence=-0.5)
        assert w.confidence == 0.0

    def test_valid_confidence_unchanged(self):
        w = Word(word="hi", confidence=0.85)
        assert w.confidence == 0.85

    def test_none_confidence_stays_none(self):
        w = Word(word="hi", confidence=None)
        assert w.confidence is None

    def test_non_numeric_confidence_becomes_none(self):
        w = Word.model_validate({"word": "hi", "confidence": "not a number"})
        assert w.confidence is None


# ---------------------------------------------------------------------------
# 3. Structural validation failures
# ---------------------------------------------------------------------------


class TestStructuralValidation:
    """Structurally invalid bodies raise ValidationError."""

    def test_missing_word_field_raises(self):
        with pytest.raises(ValidationError):
            Word.model_validate({"start": 0.1, "end": 0.5, "confidence": 0.9})

    def test_word_as_list_raises(self):
        with pytest.raises(ValidationError):
            Word.model_validate({"word": ["not", "a", "string"]})

    def test_word_as_dict_raises(self):
        with pytest.raises(ValidationError):
            Word.model_validate({"word": {"text": "hello"}})

    def test_correction_request_missing_transcript_raises(self):
        with pytest.raises(ValidationError):
            CorrectionRequest.model_validate({"words": []})


# ---------------------------------------------------------------------------
# 4. CorrectedWord flag omission
# ---------------------------------------------------------------------------


class TestCorrectedWordFlagOmission:
    """Flags with None are omitted with exclude_none; True flags are present."""

    def test_none_location_corrected_omitted(self):
        cw = CorrectedWord(word="hello", location_corrected=None)
        dumped = cw.model_dump(exclude_none=True)
        assert "location_corrected" not in dumped
        assert "locationCorrected" not in dumped

    def test_none_location_corrected_omitted_with_alias(self):
        cw = CorrectedWord(word="hello", location_corrected=None)
        dumped = cw.model_dump(by_alias=True, exclude_none=True)
        assert "locationCorrected" not in dumped

    def test_true_location_corrected_present_with_alias(self):
        cw = CorrectedWord(word="Kumasi", location_corrected=True)
        dumped = cw.model_dump(by_alias=True, exclude_none=True)
        assert dumped["locationCorrected"] is True

    def test_true_bedrock_corrected_present(self):
        cw = CorrectedWord(word="Accra", bedrock_corrected=True)
        dumped = cw.model_dump(by_alias=True, exclude_none=True)
        assert dumped["bedrockCorrected"] is True

    def test_none_flags_all_omitted(self):
        cw = CorrectedWord(
            word="test",
            location_corrected=None,
            bedrock_corrected=None,
            year_corrected=None,
        )
        dumped = cw.model_dump(by_alias=True, exclude_none=True)
        assert "locationCorrected" not in dumped
        assert "bedrockCorrected" not in dumped
        assert "yearCorrected" not in dumped
