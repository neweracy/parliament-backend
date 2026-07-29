"""Match strategies for the Correction_Engine.

Each strategy function takes an input text (lowercased), the MatchIndex,
optionally the DatasetSnapshot, and returns a MatchResult or None.

Strategies are called in a short-circuiting chain by correct_single in the
documented order: exact → fused → initials → phonetic → fuzzy → substring.

The ``narrow()`` function provides candidate selection for fuzzy matching
using the intersection of length_buckets and bk_tree results.

Requirements: 3.1, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 4.1, 4.7, 10.4
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

from app.correction.blocklist import is_blocked
from app.correction.phonetics import phonetic_key
from app.correction.scoring import (
    EXACT_CONFIDENCE,
    FUSED_CONFIDENCE,
    INITIALS_MULTI_CONFIDENCE,
    INITIALS_SINGLE_CONFIDENCE,
    PHONETIC_CONFIDENCE,
    SUBSTRING_CONFIDENCE,
    fuzzy_confidence,
    get_max_dist,
)
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex

# ---------------------------------------------------------------------------
# Minimum candidate length for fuzzy matching
# ---------------------------------------------------------------------------

MIN_CANDIDATE_LENGTH: int = 4

# Minimum input length for substring matching
MIN_SUBSTRING_LENGTH: int = 6


# ---------------------------------------------------------------------------
# MatchResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    """Result of a successful match strategy."""

    canonical: str
    confidence: float
    strategy: str  # MatchStrategy value
    entity_kind: str
    entity_type: str


# ---------------------------------------------------------------------------
# Helper: build a MatchResult from a canonical name
# ---------------------------------------------------------------------------


def _make_result(
    canonical: str,
    confidence: float,
    strategy: str,
    index: MatchIndex,
) -> MatchResult:
    """Build a MatchResult from a canonical name, looking up kind/type."""
    entity_kind = index.entity_kind_map.get(canonical, "location")
    entity_type = index.entity_type_map.get(canonical, "supplementary")
    return MatchResult(
        canonical=canonical,
        confidence=confidence,
        strategy=strategy,
        entity_kind=entity_kind,
        entity_type=entity_type,
    )


# ---------------------------------------------------------------------------
# Strategy 1: Exact match
# ---------------------------------------------------------------------------


def match_exact(text_lower: str, index: MatchIndex) -> MatchResult | None:
    """Direct lookup in canonical_map.

    Returns confidence 1.00 on match.
    """
    canonical = index.canonical_map.get(text_lower)
    if canonical is None:
        return None
    return _make_result(canonical, EXACT_CONFIDENCE, "exact", index)


# ---------------------------------------------------------------------------
# Strategy 2: Fused match
# ---------------------------------------------------------------------------


def match_fused(text_lower: str, index: MatchIndex) -> MatchResult | None:
    """Strip all spaces, hyphens, and apostrophes; look up in fused_map.

    Returns confidence 0.98 on match.
    """
    fused = re.sub(r"[\s\-']", "", text_lower)
    if not fused:
        return None
    canonical = index.fused_map.get(fused)
    if canonical is None:
        return None
    return _make_result(canonical, FUSED_CONFIDENCE, "fused", index)


# ---------------------------------------------------------------------------
# Strategy 3: Initials match
# ---------------------------------------------------------------------------


def match_initials(text_lower: str, index: MatchIndex) -> MatchResult | None:
    """Look up in initial_surname_map.

    Returns 0.95 for single-initial match (e.g. "k.ofori-atta"),
    0.93 for multi-initial match (e.g. "j.j.rawlings").

    The input is normalized: spaces around dots/periods are collapsed,
    then the key is looked up directly.
    """
    # Normalize: remove spaces around dots for initial patterns
    # "K. Ofori-Atta" (lowered) → "k. ofori-atta" → "k.ofori-atta"
    normalized = re.sub(r"\s*\.\s*", ".", text_lower)
    # Also try with spaces converted to dots for patterns like "k ofori-atta"
    # but primary lookup uses the dot-normalized form
    normalized = normalized.replace(" ", ".")

    # Remove trailing dot if present
    if normalized.endswith("."):
        normalized = normalized[:-1]

    canonicals = index.initial_surname_map.get(normalized)
    if not canonicals:
        return None

    # Pick the first canonical (deterministic via load order)
    canonical = canonicals[0]

    # Determine if single or multi-initial
    # Count the dots before the surname part
    parts = normalized.split(".")
    # Parts: ['k', 'ofori-atta'] → single initial
    # Parts: ['j', 'j', 'rawlings'] → multi initial
    num_initials = len(parts) - 1  # last part is surname

    if num_initials >= 2:
        confidence = INITIALS_MULTI_CONFIDENCE
    else:
        confidence = INITIALS_SINGLE_CONFIDENCE

    return _make_result(canonical, confidence, "initials", index)


# ---------------------------------------------------------------------------
# Strategy 4: Phonetic match
# ---------------------------------------------------------------------------


def match_phonetic(text_lower: str, index: MatchIndex) -> MatchResult | None:
    """Compute phonetic_key, look up in phonetic_map.

    If multiple canonicals match, pick the one with lowest alias_ordinal.
    Returns confidence 0.90 on match.
    """
    key = phonetic_key(text_lower)
    if not key:
        return None

    canonicals = index.phonetic_map.get(key)
    if not canonicals:
        return None

    # Pick the canonical with the lowest alias_ordinal for determinism
    if len(canonicals) == 1:
        canonical = canonicals[0]
    else:
        # Tie-break: lowest alias_ordinal
        canonical = min(
            canonicals,
            key=lambda c: index.alias_ordinal.get(c.lower(), float("inf")),
        )

    return _make_result(canonical, PHONETIC_CONFIDENCE, "phonetic", index)


# ---------------------------------------------------------------------------
# Strategy 5: Fuzzy match
# ---------------------------------------------------------------------------


def match_fuzzy(
    text_lower: str,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    *,
    fuzzy_score_cutoff: float = 0.70,
    min_candidate_length: int = 4,
) -> MatchResult | None:
    """Length-adaptive fuzzy matching using BK-tree and length_buckets.

    - Skip if input length < min_candidate_length (default 4)
    - Skip if input is in the Block_List
    - Use narrow() to get candidates
    - Compute Levenshtein distance for each candidate
    - Tie-break on (distance, alias_ordinal)
    - Return fuzzy_confidence(best_distance) if within max_dist

    Parameters
    ----------
    fuzzy_score_cutoff : float
        Reserved for future use — rapidfuzz score floor (currently unused
        since matching is distance-based via BK-tree).
    min_candidate_length : int
        Minimum input length to attempt fuzzy matching.
    """
    if len(text_lower) < min_candidate_length:
        return None

    # Block_List guard — inside match_fuzzy only (Requirement 4.1)
    if is_blocked(text_lower, snapshot):
        return None

    max_dist = get_max_dist(len(text_lower))

    candidates = narrow(text_lower, index, max_dist)
    if not candidates:
        return None

    # Score each candidate
    best_candidate: str | None = None
    best_distance: int = max_dist + 1
    best_ordinal: int = float("inf")  # type: ignore[assignment]

    for candidate in candidates:
        dist = Levenshtein.distance(text_lower, candidate)
        if dist > max_dist:
            continue
        if dist == 0:
            # Exact match — shouldn't reach here but handle gracefully
            continue

        ordinal = index.alias_ordinal.get(candidate, float("inf"))

        # Tie-break: (distance, alias_ordinal)
        if dist < best_distance or (dist == best_distance and ordinal < best_ordinal):
            best_distance = dist
            best_ordinal = ordinal  # type: ignore[assignment]
            best_candidate = candidate

    if best_candidate is None:
        return None

    canonical = index.canonical_map.get(best_candidate)
    if canonical is None:
        return None

    confidence = fuzzy_confidence(best_distance)
    return _make_result(canonical, confidence, "fuzzy", index)


# ---------------------------------------------------------------------------
# Strategy 6: Substring match
# ---------------------------------------------------------------------------


def match_substring(text_lower: str, index: MatchIndex) -> MatchResult | None:
    """Check if text is a substring of any canonical_map key.

    Only try when input length >= MIN_SUBSTRING_LENGTH (6).
    Returns confidence 0.80 on match.

    If multiple matches, pick the one with lowest alias_ordinal.
    """
    if len(text_lower) < MIN_SUBSTRING_LENGTH:
        return None

    best_key: str | None = None
    best_ordinal: int = float("inf")  # type: ignore[assignment]

    for key in index.canonical_map:
        if text_lower in key and text_lower != key:
            ordinal = index.alias_ordinal.get(key, float("inf"))
            if ordinal < best_ordinal:
                best_ordinal = ordinal  # type: ignore[assignment]
                best_key = key

    if best_key is None:
        return None

    canonical = index.canonical_map[best_key]
    return _make_result(canonical, SUBSTRING_CONFIDENCE, "substring", index)


# ---------------------------------------------------------------------------
# Candidate narrowing: narrow()
# ---------------------------------------------------------------------------


def narrow(text_lower: str, index: MatchIndex, max_dist: int) -> list[str]:
    """Get candidates from bk_tree ∩ length_buckets.

    - Get candidates from bk_tree.find(text_lower, max_dist)
    - Get candidates from length_buckets for lengths within ±max_dist
    - Return intersection (candidates appearing in both) for narrowing
    - If bk_tree is None, fall back to length_buckets only
    """
    input_len = len(text_lower)

    # Gather length-bucket candidates (lengths within ±max_dist of input)
    bucket_candidates: set[str] = set()
    for length in range(
        max(1, input_len - max_dist),
        input_len + max_dist + 1,
    ):
        bucket = index.length_buckets.get(length)
        if bucket:
            bucket_candidates.update(bucket)

    if index.bk_tree is None:
        # Fallback: length_buckets only
        return list(bucket_candidates)

    # BK-tree candidates
    bk_results = index.bk_tree.find(text_lower, max_dist)
    bk_candidates: set[str] = {item for _dist, item in bk_results}

    # Return intersection for better narrowing
    intersection = bucket_candidates & bk_candidates
    if intersection:
        return list(intersection)

    # If intersection is empty, fall back to bk_tree results
    # (can happen when length_buckets don't cover the right range)
    return list(bk_candidates) if bk_candidates else list(bucket_candidates)


# ---------------------------------------------------------------------------
# Party display heuristic
# ---------------------------------------------------------------------------


def get_party_display(original: str, canonical: str, index: MatchIndex) -> str:
    """Determine how to display a matched party name.

    Heuristic:
    - If len(original) <= len(abbr) + 1, return the abbreviation (uppercased)
    - Otherwise return the canonical full name
    - If no abbreviation exists in party_abbr_map, return canonical
    """
    abbr = index.party_abbr_map.get(canonical)
    if abbr is None:
        return canonical

    if len(original) <= len(abbr) + 1:
        return abbr.upper()

    return canonical
