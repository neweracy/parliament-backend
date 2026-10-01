"""Unit and property tests for app/correction/scoring.py evidence scoring.

Covers the evidence-scaled scoring functions added for Req 3:
- ``phonetic_evidence_score`` — bounded, monotone in similarity / key length /
  fanout, and forced below 0.75 when Key_Fanout >= 5.
- ``fuzzy_evidence_score`` — bounded and non-increasing in Relative_Distance.

The retained deterministic constants and legacy ``fuzzy_confidence`` are
untouched by this task; the strict-below-0.93 invariant that ties the two
together is asserted here.

Requirements: 3.2, 3.3, 3.4, 3.7, 3.8, 3.12, 3.13, 3.14
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.correction.scoring import (
    EVIDENCE_MAX,
    EVIDENCE_MIN,
    INITIALS_MULTI_CONFIDENCE,
    fuzzy_evidence_score,
    phonetic_evidence_score,
)

_TOL = 1e-6

# Hypothesis strategies constrained to the evidence-scoring input domain.
_similarity = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_key_len = st.integers(min_value=0, max_value=40)
_fanout = st.integers(min_value=1, max_value=200)
_rel_dist = st.floats(min_value=0.0, max_value=5.0, allow_nan=False)


# ---------------------------------------------------------------------------
# phonetic_evidence_score
# ---------------------------------------------------------------------------


class TestPhoneticEvidenceScoreBounds:
    def test_within_evidence_interval_for_extremes(self):
        # Best possible evidence tops out at EVIDENCE_MAX.
        assert phonetic_evidence_score(1.0, 12, 1) == EVIDENCE_MAX
        # Worst evidence never drops below EVIDENCE_MIN.
        assert phonetic_evidence_score(0.0, 0, 200) == EVIDENCE_MIN

    @given(_similarity, _key_len, _fanout)
    def test_always_within_interval(self, sim, key_len, fanout):
        score = phonetic_evidence_score(sim, key_len, fanout)
        assert EVIDENCE_MIN - _TOL <= score <= EVIDENCE_MAX + _TOL

    @given(_similarity, _key_len, _fanout)
    def test_strictly_below_smallest_deterministic_constant(self, sim, key_len, fanout):
        # Req 3.14: every Approximate score is strictly below 0.93.
        assert phonetic_evidence_score(sim, key_len, fanout) < INITIALS_MULTI_CONFIDENCE


class TestPhoneticEvidenceScoreMonotonicity:
    @given(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        _key_len,
        _fanout,
    )
    def test_non_decreasing_in_similarity(self, sim_a, sim_b, key_len, fanout):
        # Req 3.2: higher similarity never scores lower (equal key_len/fanout).
        lo, hi = sorted((sim_a, sim_b))
        assert (
            phonetic_evidence_score(hi, key_len, fanout)
            >= phonetic_evidence_score(lo, key_len, fanout) - _TOL
        )

    @given(_similarity, _key_len, _key_len, _fanout)
    def test_non_decreasing_in_key_len(self, sim, key_a, key_b, fanout):
        # Req 3.12: longer key never scores lower (equal similarity/fanout).
        lo, hi = sorted((key_a, key_b))
        assert (
            phonetic_evidence_score(sim, hi, fanout)
            >= phonetic_evidence_score(sim, lo, fanout) - _TOL
        )

    @given(_similarity, _key_len, _fanout, _fanout)
    def test_non_increasing_in_fanout(self, sim, key_len, fan_a, fan_b):
        # Req 3.3: lower fanout never scores lower (equal similarity/key_len).
        lo, hi = sorted((fan_a, fan_b))
        assert (
            phonetic_evidence_score(sim, key_len, lo)
            >= phonetic_evidence_score(sim, key_len, hi) - _TOL
        )


class TestPhoneticEvidenceScoreFanoutCeiling:
    @given(
        _similarity,
        _key_len,
        st.integers(min_value=5, max_value=200),
    )
    def test_high_fanout_forces_below_075(self, sim, key_len, fanout):
        # Req 3.7: Key_Fanout >= 5 => Evidence_Score < 0.75, across the domain.
        assert phonetic_evidence_score(sim, key_len, fanout) < 0.75

    def test_fanout_four_may_exceed_075(self):
        # The ceiling applies at 5, not 4 — a strong key of fanout 4 can exceed
        # 0.75, confirming the ceiling is not applied too eagerly.
        assert phonetic_evidence_score(1.0, 12, 1) >= 0.75


# ---------------------------------------------------------------------------
# fuzzy_evidence_score
# ---------------------------------------------------------------------------


class TestFuzzyEvidenceScore:
    def test_zero_distance_is_ceiling(self):
        assert fuzzy_evidence_score(0.0) == EVIDENCE_MAX

    def test_large_distance_clamps_to_floor(self):
        assert fuzzy_evidence_score(5.0) == EVIDENCE_MIN

    @given(_rel_dist)
    def test_always_within_interval(self, rel):
        score = fuzzy_evidence_score(rel)
        assert EVIDENCE_MIN - _TOL <= score <= EVIDENCE_MAX + _TOL

    @given(_rel_dist)
    def test_strictly_below_smallest_deterministic_constant(self, rel):
        # Req 3.14.
        assert fuzzy_evidence_score(rel) < INITIALS_MULTI_CONFIDENCE

    @given(_rel_dist, _rel_dist)
    def test_non_increasing_in_relative_distance(self, rel_a, rel_b):
        # Req 3.13: lower Relative_Distance never scores lower.
        lo, hi = sorted((rel_a, rel_b))
        assert fuzzy_evidence_score(lo) >= fuzzy_evidence_score(hi) - _TOL
