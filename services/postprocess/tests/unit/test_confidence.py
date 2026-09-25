"""Unit tests for app/correction/confidence.py — Span_Confidence and Confidence_Gate.

Covers Requirement 1:
- span_confidence: min clamped Word_Confidence over covered Words carrying one;
  Unknown (None) when none do (Req 1.3, 1.4, 1.5).
- align_span_to_words: positional case-insensitive alignment ignoring edge
  punctuation; None when any token has no aligned Word (Req 1.7, 1.8).
- confidence_gate_blocks_approximate: True iff known and >= threshold
  (Req 1.2, 1.6, 1.11).
"""

from datetime import UTC, datetime

from app.correction.confidence import (
    UNKNOWN,
    align_span_to_words,
    confidence_gate_blocks_approximate,
    span_confidence,
)
from app.correction.engine import correct_single
from app.correction.gates import GateConfig, GateContext
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


def _w(word: str, confidence=None) -> dict:
    d: dict = {"word": word, "start": 0.0, "end": 0.1}
    if confidence is not None:
        d["confidence"] = confidence
    return d


class TestSpanConfidence:
    """span_confidence — Req 1.3, 1.4, 1.5."""

    def test_min_over_covered_words(self):
        words = [_w("a", 0.9), _w("b", 0.6), _w("c", 0.8)]
        assert span_confidence(words) == 0.6

    def test_single_word(self):
        assert span_confidence([_w("a", 0.42)]) == 0.42

    def test_mixed_present_and_absent_uses_only_present(self):
        # Req 1.4: min over only the Words that carry a confidence.
        words = [_w("a", 0.7), _w("b"), _w("c", 0.5)]
        assert span_confidence(words) == 0.5

    def test_all_absent_is_unknown(self):
        # Req 1.5
        assert span_confidence([_w("a"), _w("b")]) is UNKNOWN

    def test_empty_is_unknown(self):
        # Req 1.5 — empty covered set.
        assert span_confidence([]) is UNKNOWN

    def test_none_confidence_is_absent(self):
        words = [_w("a"), _w("b", 0.8)]
        words[0]["confidence"] = None
        assert span_confidence(words) == 0.8

    def test_non_numeric_confidence_is_absent(self):
        # Req 14.13: non-numeric confidence treated as absent.
        words = [{"word": "a", "confidence": "high"}, _w("b", 0.7)]
        assert span_confidence(words) == 0.7

    def test_non_numeric_only_is_unknown(self):
        words = [{"word": "a", "confidence": "high"}]
        assert span_confidence(words) is UNKNOWN

    def test_bool_confidence_excluded(self):
        # bool is a subclass of int but is never a valid confidence.
        words = [{"word": "a", "confidence": True}, _w("b", 0.3)]
        assert span_confidence(words) == 0.3

    def test_out_of_range_high_clamped(self):
        # Req 14.7: clamp above 1.0.
        assert span_confidence([_w("a", 1.5)]) == 1.0

    def test_out_of_range_low_clamped(self):
        assert span_confidence([_w("a", -0.5)]) == 0.0

    def test_clamp_then_min(self):
        words = [_w("a", 1.4), _w("b", 0.2)]
        assert span_confidence(words) == 0.2


class TestAlignSpanToWords:
    """align_span_to_words — Req 1.7, 1.8."""

    def test_simple_alignment(self):
        words = [_w("hello", 0.9), _w("world", 0.8)]
        aligned = align_span_to_words(["hello", "world"], words, 0)
        assert aligned == words

    def test_alignment_from_offset(self):
        words = [_w("the", 0.9), _w("hon", 0.8), _w("member", 0.7)]
        aligned = align_span_to_words(["hon", "member"], words, 1)
        assert aligned == [words[1], words[2]]

    def test_case_insensitive(self):
        words = [_w("Ghana", 0.9)]
        aligned = align_span_to_words(["ghana"], words, 0)
        assert aligned == words

    def test_ignores_leading_trailing_punctuation(self):
        # Req 1.7: strip leading/trailing punctuation on both sides.
        words = [_w("Ghana,", 0.9)]
        aligned = align_span_to_words(["Ghana"], words, 0)
        assert aligned == words

    def test_internal_separators_preserved(self):
        words = [_w("Ofori-Atta", 0.9)]
        aligned = align_span_to_words(["ofori-atta"], words, 0)
        assert aligned == words

    def test_mismatched_text_is_unknown(self):
        # Req 1.8: matched text diverges.
        words = [_w("hello", 0.9), _w("earth", 0.8)]
        assert align_span_to_words(["hello", "world"], words, 0) is None

    def test_span_runs_past_end_is_unknown(self):
        # Req 1.8: count diverges (Span longer than remaining Words).
        words = [_w("hello", 0.9)]
        assert align_span_to_words(["hello", "world"], words, 0) is None

    def test_out_of_range_start_index_is_unknown(self):
        words = [_w("hello", 0.9)]
        assert align_span_to_words(["hello"], words, 5) is None

    def test_negative_start_index_is_unknown(self):
        words = [_w("hello", 0.9)]
        assert align_span_to_words(["hello"], words, -1) is None

    def test_empty_span_tokens_is_unknown(self):
        words = [_w("hello", 0.9)]
        assert align_span_to_words([], words, 0) is None

    def test_non_string_word_text_is_unknown(self):
        words = [{"word": 123, "confidence": 0.9}]
        assert align_span_to_words(["123"], words, 0) is None

    def test_alignment_enables_confidence(self):
        # Integration of the two helpers: aligned Words feed span_confidence.
        words = [_w("member", 0.95), _w("for", 0.4), _w("tema", 0.9)]
        aligned = align_span_to_words(["member", "for", "tema"], words, 0)
        assert aligned is not None
        assert span_confidence(aligned) == 0.4


class TestConfidenceGateBlocksApproximate:
    """confidence_gate_blocks_approximate — Req 1.2, 1.6, 1.11."""

    def test_known_above_threshold_blocks(self):
        assert confidence_gate_blocks_approximate(0.95, 0.90) is True

    def test_known_at_threshold_blocks(self):
        # Req 1.2: >= threshold.
        assert confidence_gate_blocks_approximate(0.90, 0.90) is True

    def test_known_below_threshold_permits(self):
        assert confidence_gate_blocks_approximate(0.85, 0.90) is False

    def test_unknown_permits(self):
        # Req 1.6: Unknown never blocks approximate evaluation.
        assert confidence_gate_blocks_approximate(UNKNOWN, 0.90) is False

    def test_unknown_permits_even_with_zero_threshold(self):
        assert confidence_gate_blocks_approximate(None, 0.0) is False

    def test_zero_threshold_blocks_any_known(self):
        assert confidence_gate_blocks_approximate(0.0, 0.0) is True


# ---------------------------------------------------------------------------
# Engine integration: correct_single Confidence_Gate wiring (Req 1.1, 1.2, 1.6, 12.9)
# ---------------------------------------------------------------------------


def _engine_env():
    """A minimal index + snapshot with an entity reachable only approximately."""
    records = [
        EntityRecord(
            canonical="Sege",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["seged"],
            source="supplementary",
            source_rank=0,
        ),
    ]
    index = build_index(records)
    snapshot = DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )
    return index, snapshot


def _active_context() -> GateContext:
    """A non-inert context (at least one flag on) so the gate is active."""
    return GateContext(config=GateConfig(evidence_confidence_enabled=True))


class TestCorrectSingleConfidenceGate:
    """The Confidence_Gate is wired into correct_single (Req 1.1, 1.2, 1.6, 12.9)."""

    def test_high_confidence_blocks_approximate(self):
        # "sage" fuzzy/phonetic-matches "sege"; a high-confidence Span with the
        # gate active is restricted to Deterministic strategies (Req 1.2).
        index, snapshot = _engine_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_active_context(), span_conf=0.99,
        )
        assert result is None

    def test_low_confidence_permits_approximate(self):
        # Below High_Confidence_Threshold the approximate chain runs (Req 1.6-adjacent).
        index, snapshot = _engine_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_active_context(), span_conf=0.30,
        )
        assert result is not None
        assert result.canonical == "Sege"

    def test_unknown_confidence_permits_approximate(self):
        # Req 1.6: Unknown never blocks the approximate strategies.
        index, snapshot = _engine_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_active_context(), span_conf=None,
        )
        assert result is not None
        assert result.canonical == "Sege"

    def test_inert_context_ignores_high_confidence(self):
        # Req 12.9: with every flag off the gate is inert — the full Baseline
        # chain runs regardless of Span_Confidence.
        index, snapshot = _engine_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=GateContext.inert(), span_conf=0.99,
        )
        assert result is not None
        assert result.canonical == "Sege"

    def test_no_context_is_baseline(self):
        # gate_context=None normalises to inert — Baseline path (Req 12.9).
        index, snapshot = _engine_env()
        result = correct_single("sage", 1, index, snapshot, span_conf=0.99)
        assert result is not None
        assert result.canonical == "Sege"

    def test_deterministic_evaluated_at_high_confidence(self):
        # Req 1.1: exact match applies even when the gate blocks approximate.
        index, snapshot = _engine_env()
        result = correct_single(
            "sege", 1, index, snapshot,
            gate_context=_active_context(), span_conf=0.99,
        )
        assert result is not None
        assert result.canonical == "Sege"
        assert result.strategy == "exact"
