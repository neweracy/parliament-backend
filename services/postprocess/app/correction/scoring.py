"""Confidence scoring constants, fuzzy confidence, and tie-break precedence.

Defines the confidence values returned by each match strategy, the
length-adaptive max_dist table for fuzzy matching, the strategy precedence
ranks used for deterministic tie-breaking, and the evidence-scaled scoring
functions (Req 3) that replace the flat per-strategy Approximate_Strategy
constants when ``evidence_confidence_enabled`` is on.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 3.12, 3.13, 3.14, 4.7, 10.4
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Confidence constants — one per match strategy
# ---------------------------------------------------------------------------

EXACT_CONFIDENCE: float = 1.00
FUSED_CONFIDENCE: float = 0.98
JOINED_CONFIDENCE: float = 0.97
INITIALS_SINGLE_CONFIDENCE: float = 0.95
INITIALS_MULTI_CONFIDENCE: float = 0.93
# title_person is a Deterministic surname lookup (see the strategies.py module
# docstring, task 1.3). It carries a fixed per-strategy constant rather than an
# Evidence_Score, matching the value historically emitted by the
# title-prefixed surname_map fallback in engine.py.
TITLE_PERSON_CONFIDENCE: float = 0.90
PHONETIC_CONFIDENCE: float = 0.90
SUBSTRING_CONFIDENCE: float = 0.80
FUZZY_MIN_CONFIDENCE: float = 0.70


def fuzzy_confidence(distance: int) -> float:
    """Compute fuzzy match confidence from Levenshtein distance.

    Formula: max(0.70, 1.0 - 0.12 * distance)

    Examples::

        fuzzy_confidence(0) → 1.00
        fuzzy_confidence(1) → 0.88
        fuzzy_confidence(2) → 0.76
        fuzzy_confidence(3) → 0.70 (clamped)
    """
    return max(FUZZY_MIN_CONFIDENCE, 1.0 - 0.12 * distance)


# ---------------------------------------------------------------------------
# Evidence-scaled scoring (Req 3) — used when evidence_confidence_enabled is on
# ---------------------------------------------------------------------------
#
# Every Approximate_Strategy Evidence_Score is bounded to [EVIDENCE_MIN,
# EVIDENCE_MAX] = [0.55, 0.92] (Req 3.4, 3.8) and is therefore strictly below
# the smallest Deterministic_Strategy constant 0.93 (Req 3.14).
#
# The weights below are chosen so the monotonicity requirements hold *by
# construction*, before any clamping:
#   * phonetic_evidence_score is non-decreasing in similarity (Req 3.2),
#     non-decreasing in key length (Req 3.12), and non-increasing in fanout
#     (Req 3.3), because each term is respectively non-decreasing /
#     non-decreasing / non-increasing in its variable and clamping to a fixed
#     interval preserves a monotone ordering.
#   * fuzzy_evidence_score is non-increasing in relative_distance (Req 3.13).
#
# The sum of the positive weights (W_SIM + W_KEY) is 0.37, so the unclamped
# phonetic base lies in [EVIDENCE_MIN - W_FAN, EVIDENCE_MIN + 0.37] =
# [0.43, 0.92]; the upper end coincides with EVIDENCE_MAX so a perfect
# similarity + longest key + singleton fanout reaches the ceiling exactly, and
# the clamp only ever raises the low end back to EVIDENCE_MIN.

EVIDENCE_MIN: float = 0.55
EVIDENCE_MAX: float = 0.92

# Phonetic weights. W_SIM + W_KEY == EVIDENCE_MAX - EVIDENCE_MIN == 0.37 so the
# best-evidence phonetic match reaches EVIDENCE_MAX exactly.
_PHONETIC_W_SIM: float = 0.27
_PHONETIC_W_KEY: float = 0.10
_PHONETIC_W_FAN: float = 0.30
# Saturation caps: contributions stop growing past these so a very long key or
# very high fanout does not dominate. KEY_CAP counts Phonetic_Key characters;
# FAN_CAP counts the fanout excess above a singleton key.
_PHONETIC_KEY_CAP: int = 12
_PHONETIC_FAN_CAP: int = 10

# The fanout>=5 hard ceiling (Req 3.7). Kept a hair below 0.75 and above
# EVIDENCE_MIN so the subsequent clamp to [0.55, 0.92] never lifts it back to
# 0.75 or higher.
_FANOUT_HIGH_THRESHOLD: int = 5
_FANOUT_HIGH_CEILING: float = 0.749

# Fuzzy slope: at relative_distance 0 the score is EVIDENCE_MAX; it declines
# linearly and is clamped at EVIDENCE_MIN. Non-increasing in relative_distance.
_FUZZY_SLOPE: float = 1.0


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp *value* to the closed interval [*low*, *high*]."""
    return max(low, min(high, value))


def phonetic_evidence_score(similarity: float, key_len: int, fanout: int) -> float:
    """Evidence_Score for a phonetic match (Req 3.1-3.4, 3.7, 3.12, 3.14).

    Combines the Normalized_Similarity between the Span and the matched alias,
    the Phonetic_Key length of the Span, and the Key_Fanout of that key within
    the current Dataset_Cache snapshot.

    Monotonicity (holds by construction, before and after clamping):
      * non-decreasing in *similarity* (Req 3.2);
      * non-decreasing in *key_len* (Req 3.12);
      * non-increasing in *fanout* (Req 3.3).

    The result is clamped to ``[EVIDENCE_MIN, EVIDENCE_MAX]`` (Req 3.4), which
    keeps it strictly below the smallest deterministic constant 0.93 (Req 3.14).
    When *fanout* is at least :data:`_FANOUT_HIGH_THRESHOLD` (5), the score is
    forced below 0.75 (Req 3.7) via a hard ceiling applied before the clamp.

    Parameters
    ----------
    similarity:
        Normalized_Similarity in [0.0, 1.0]; clamped defensively.
    key_len:
        Phonetic_Key length in characters (non-negative).
    fanout:
        Key_Fanout — the number of distinct canonical entities sharing the
        Phonetic_Key. Treated as at least 1.
    """
    sim = _clamp(similarity, 0.0, 1.0)
    key_component = min(max(key_len, 0), _PHONETIC_KEY_CAP) / _PHONETIC_KEY_CAP
    fanout_excess = max(fanout, 1) - 1
    fanout_component = min(fanout_excess, _PHONETIC_FAN_CAP) / _PHONETIC_FAN_CAP

    base = (
        EVIDENCE_MIN
        + _PHONETIC_W_SIM * sim
        + _PHONETIC_W_KEY * key_component
        - _PHONETIC_W_FAN * fanout_component
    )

    if fanout >= _FANOUT_HIGH_THRESHOLD:
        base = min(base, _FANOUT_HIGH_CEILING)

    return _clamp(base, EVIDENCE_MIN, EVIDENCE_MAX)


def fuzzy_evidence_score(relative_distance: float) -> float:
    """Evidence_Score for a fuzzy match (Req 3.8, 3.13, 3.14).

    Non-increasing in *relative_distance*: a smaller Relative_Distance yields a
    score greater than or equal to the score of a larger one. Clamped to
    ``[EVIDENCE_MIN, EVIDENCE_MAX]`` (Req 3.8), which keeps every fuzzy score
    strictly below 0.93 (Req 3.14).

    Parameters
    ----------
    relative_distance:
        Levenshtein distance divided by Span length, in [0.0, ...]; clamped
        defensively at 0.0 on the low side.
    """
    rel = max(0.0, relative_distance)
    return _clamp(EVIDENCE_MAX - _FUZZY_SLOPE * rel, EVIDENCE_MIN, EVIDENCE_MAX)


# ---------------------------------------------------------------------------
# Strategy precedence ranks — lower value wins in tie-breaks
# ---------------------------------------------------------------------------

STRATEGY_RANK: dict[str, int] = {
    "exact": 0,
    "fused": 1,
    "joined": 2,
    "initials": 3,
    "title_person": 4,
    "phonetic": 5,
    "fuzzy": 6,
    "substring": 7,
}


# ---------------------------------------------------------------------------
# Length-adaptive max_dist table for fuzzy matching
# ---------------------------------------------------------------------------

def get_max_dist(length: int) -> int:
    """Return the maximum edit distance allowed for a given input length.

    Longer inputs tolerate more edits:
      - len <= 4:  max_dist = 1
      - len 5-7:   max_dist = 2
      - len 8-11:  max_dist = 3
      - len >= 12:  max_dist = 4
    """
    if length <= 4:
        return 1
    if length <= 7:
        return 2
    if length <= 11:
        return 3
    return 4
