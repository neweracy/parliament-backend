"""Unit tests for app/correction/source_ids.py — stable source addressing.

Feature: transcript-evidence-navigation (task 1.1).

Covers the deterministic Source_Word_Id / Source_Span_Id scheme assigned before
corrections run (Req 1.2, 1.3, 2.7):
- source_word_id / assign_source_word_ids: ``w{index}`` per raw ASR word, in
  order, distinct, stable within a version, positional only (never touches ASR
  timing).
- source_span_id / source_span_id_from_indices: ``s:{first}-{last}`` from the
  bounding covered word ids.
"""

import pytest

from app.correction.source_ids import (
    assign_source_word_ids,
    source_span_id,
    source_span_id_from_indices,
    source_word_id,
)


class TestSourceWordId:
    """source_word_id — Req 1.2."""

    def test_zero_based_index_scheme(self):
        assert source_word_id(0) == "w0"
        assert source_word_id(1) == "w1"
        assert source_word_id(42) == "w42"

    def test_negative_index_rejected(self):
        with pytest.raises(ValueError):
            source_word_id(-1)


class TestAssignSourceWordIds:
    """assign_source_word_ids — Req 1.2, 2.7."""

    def test_one_id_per_word_in_order(self):
        words = [{"word": "a"}, {"word": "b"}, {"word": "c"}]
        assert assign_source_word_ids(words) == ["w0", "w1", "w2"]

    def test_ids_are_distinct(self):
        ids = assign_source_word_ids([{"word": "x"}] * 5)
        assert len(ids) == len(set(ids)) == 5

    def test_empty_word_list_yields_no_ids(self):
        assert assign_source_word_ids([]) == []

    def test_stable_across_repeated_assignment(self):
        # Same raw list to same ids on every call: stable within a version.
        words = [{"word": "one"}, {"word": "two"}]
        assert assign_source_word_ids(words) == assign_source_word_ids(words)

    def test_element_type_is_irrelevant(self):
        # Positional only — dicts, strings, or any element work identically.
        assert assign_source_word_ids(["a", "b"]) == ["w0", "w1"]

    def test_does_not_mutate_input_timing(self):
        # Immutable_Source_Timing (Req 2.7): assignment never touches start/end.
        words = [{"word": "a", "start": 0.5, "end": 0.9}]
        before = [dict(w) for w in words]
        assign_source_word_ids(words)
        assert words == before


class TestSourceSpanId:
    """source_span_id / source_span_id_from_indices — Req 1.3, 2.1, 2.2."""

    def test_single_word_span_repeats_id(self):
        assert source_span_id(["w0"]) == "s:w0-w0"

    def test_multi_word_span_bounds_first_and_last(self):
        assert source_span_id(["w3", "w4", "w5"]) == "s:w3-w5"

    def test_empty_span_rejected(self):
        with pytest.raises(ValueError):
            source_span_id([])

    def test_from_indices_single(self):
        assert source_span_id_from_indices(2, 2) == "s:w2-w2"

    def test_from_indices_range(self):
        assert source_span_id_from_indices(0, 3) == "s:w0-w3"

    def test_from_indices_rejects_inverted_range(self):
        with pytest.raises(ValueError):
            source_span_id_from_indices(3, 1)

    def test_from_indices_rejects_negative(self):
        with pytest.raises(ValueError):
            source_span_id_from_indices(-1, 2)
