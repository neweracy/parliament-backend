"""Confidence scoring constants, fuzzy confidence, and tie-break precedence.

Defines the confidence values returned by each match strategy, the
length-adaptive max_dist table for fuzzy matching, and the strategy
precedence ranks used for deterministic tie-breaking.

Requirements: 3.1, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 4.7, 10.4
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
