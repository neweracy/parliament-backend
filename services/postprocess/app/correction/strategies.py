"""Match strategies for the Correction_Engine.

Each strategy function takes an input text (lowercased), the MatchIndex,
optionally the DatasetSnapshot, and returns a MatchResult or None.

Strategies are called in a short-circuiting chain by correct_single in the
documented order: exact → fused → initials → phonetic → fuzzy → substring.

The ``narrow()`` function provides candidate selection for fuzzy matching
using the intersection of length_buckets and bk_tree results.

Requirements: 3.1, 3.3, 3.4, 3.5, 3.7, 3.8, 3.9, 4.1, 4.7, 10.4

Strategy classification (task 1.3, Req 14.3)
--------------------------------------------
The requirements classify every match strategy as either **Deterministic**
(``exact``, ``fused``, ``joined``, ``initials`` — an exact key lookup, never
gated by confidence / lexicon / context / block-list) or **Approximate**
(``phonetic``, ``fuzzy``, ``substring`` — tolerates spelling divergence and
passes through the precision gates). The ``title_person`` value sits in the
``MatchStrategy`` set (Req 14.3) and in ``STRATEGY_RANK`` (rank 4, between
``initials`` and ``phonetic``), yet the requirements' Deterministic/Approximate
lists name neither it, and ``scoring.py`` historically carried no confidence
constant for it. The design flagged this as an open item; this module resolves
it.

Decision: ``title_person`` is **Deterministic**.

Rationale:
  * Mechanism. ``title_person`` is emitted from exactly one place — the
    surname-only fallback inside ``engine._match_title_person`` /
    ``_match_title_person_words``. That branch performs a direct
    ``surname_map`` dictionary lookup of a single token following a
    Title_Prefix. It is an exact key lookup with no spelling tolerance, which
    is the defining property of a Deterministic_Strategy. (The phonetic and
    fuzzy fallbacks in the same helper keep their own ``phonetic`` / ``fuzzy``
    labels and remain Approximate; they are not tagged ``title_person``.)
  * Gate behaviour. Deterministic strategies are never gated. This preserves
    the current behaviour of confident, contextually-anchored
    "Hon. <surname>" corrections: the Title_Prefix supplies its own strong
    person-context signal, so subjecting the surname lookup to the
    confidence, lexicon, or context gates would suppress legitimate
    corrections. Ranking it inside the deterministic block (rank 4, before
    ``phonetic``) is consistent with this.
  * Confidence constant. As a Deterministic strategy it carries a fixed
    per-strategy constant, not an Evidence_Score. ``scoring.py`` now defines
    ``TITLE_PERSON_CONFIDENCE = 0.90`` — the exact value already hardcoded at
    the emission site — so this classification changes no observed output and
    keeps the flag-off path Baseline-equivalent (Req 12.9).
  * Contract. ``title_person`` remains a reported ``Match_Strategy`` value
    (Req 14.3); this decision renames / removes nothing.

Gates that apply to ``title_person``: none. It is Deterministic, so the
Confidence_Gate, Lexicon_Gate, Context_Gate, Sitting_Scope penalty, and
Block_List guard all skip it, exactly as they skip ``exact`` / ``fused`` /
``joined`` / ``initials``. The ``DETERMINISTIC_STRATEGIES`` /
``APPROXIMATE_STRATEGIES`` sets and ``is_deterministic`` helper below are the
single source of truth the gate tasks (2.x, 4.x) branch on.
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
    fuzzy_evidence_score,
    get_max_dist,
    phonetic_evidence_score,
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
# Strategy classification (task 1.3, Req 14.3)
# ---------------------------------------------------------------------------
#
# Single source of truth the precision gates (tasks 2.x, 4.x) branch on.
# See the module docstring for the ``title_person`` classification decision:
# ``title_person`` is a Deterministic surname_map lookup, so it lives in
# DETERMINISTIC_STRATEGIES and no gate applies to it.

DETERMINISTIC_STRATEGIES: frozenset[str] = frozenset(
    {"exact", "fused", "joined", "initials", "title_person"}
)

APPROXIMATE_STRATEGIES: frozenset[str] = frozenset(
    {"phonetic", "fuzzy", "substring"}
)


def is_deterministic(strategy: str) -> bool:
    """Return True when ``strategy`` is a Deterministic_Strategy.

    Deterministic strategies (``exact``, ``fused``, ``joined``, ``initials``,
    ``title_person``) are exact key lookups and are never subject to the
    Confidence_Gate, Lexicon_Gate, Context_Gate, Sitting_Scope penalty, or
    Block_List guard. Approximate strategies (``phonetic``, ``fuzzy``,
    ``substring``) tolerate spelling divergence and pass through those gates.
    """
    return strategy in DETERMINISTIC_STRATEGIES


def is_approximate(strategy: str) -> bool:
    """Return True when ``strategy`` is an Approximate_Strategy."""
    return strategy in APPROXIMATE_STRATEGIES


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


@dataclass(frozen=True)
class EvidenceParams:
    """Resolved inputs for evidence-scaled Approximate_Strategy scoring (Req 3).

    Threaded from the engine (which reads them off the :class:`GateContext`)
    into ``match_phonetic`` and ``match_fuzzy`` so those strategy functions
    compute an Evidence_Score instead of a flat per-strategy constant, and
    apply the phonetic Min_Phonetic_Key_Length / Min_Phonetic_Similarity
    sub-gates (Req 3.5, 3.6).

    When ``enabled`` is ``False`` the strategy functions take their legacy
    path exactly — flat ``PHONETIC_CONFIDENCE`` / ``fuzzy_confidence`` and no
    phonetic sub-gates — so the flag-off output is Baseline-equivalent
    (Req 12.9). A ``None`` :class:`EvidenceParams` on a strategy call is
    likewise the legacy path.
    """

    enabled: bool = False
    min_phonetic_key_length: int = 4
    min_phonetic_similarity: float = 0.60


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


def match_phonetic(
    text_lower: str,
    index: MatchIndex,
    snapshot: DatasetSnapshot | None = None,
    *,
    evidence: EvidenceParams | None = None,
) -> MatchResult | None:
    """Compute phonetic_key, look up in phonetic_map.

    If multiple canonicals match, pick the one with lowest alias_ordinal.

    Checks the Block_List before returning a match — common English words
    that happen to share a phonetic key with an entity name are rejected.

    Confidence (Req 3.1-3.5, 3.7, 3.12)
    -----------------------------------
    * Legacy path (``evidence`` is ``None`` or ``evidence.enabled`` is
      ``False``): returns the flat ``PHONETIC_CONFIDENCE`` (0.90) with no
      phonetic sub-gates, matching Baseline (Req 12.9).
    * Evidence path (``evidence.enabled``): rejects the Span outright when its
      Phonetic_Key is shorter than ``min_phonetic_key_length`` (Req 3.5);
      restricts candidates to those whose Normalized_Similarity to the Span is
      at least ``min_phonetic_similarity`` (Req 3.6); and assigns the selected
      candidate a :func:`phonetic_evidence_score` from the similarity, the
      Phonetic_Key length, and the Key_Fanout of that key (Req 3.1-3.4, 3.7,
      3.12). When no candidate clears the similarity floor, returns ``None`` so
      the chain continues with the next strategy.
    """
    # Block_List guard — prevents false phonetic matches on common words
    if snapshot is not None and is_blocked(text_lower, snapshot):
        return None

    key = phonetic_key(text_lower)
    if not key:
        return None

    use_evidence = evidence is not None and evidence.enabled

    # Min_Phonetic_Key_Length sub-gate (Req 3.5): a Span whose Phonetic_Key is
    # too short carries too little phonetic evidence to match on.
    if use_evidence and len(key) < evidence.min_phonetic_key_length:
        return None

    canonicals = index.phonetic_map.get(key)
    if not canonicals:
        return None

    if not use_evidence:
        # --- Legacy path: flat confidence, lowest-ordinal tie-break ---
        if len(canonicals) == 1:
            canonical = canonicals[0]
        else:
            canonical = min(
                canonicals,
                key=lambda c: index.alias_ordinal.get(c.lower(), float("inf")),
            )
        return _make_result(canonical, PHONETIC_CONFIDENCE, "phonetic", index)

    # --- Evidence path (Req 3.1-3.4, 3.6, 3.7, 3.12) ---
    # Min_Phonetic_Similarity sub-gate: keep only candidates whose canonical
    # form is similar enough to the Span (Req 3.6). Normalized_Similarity is
    # the rapidfuzz normalized Levenshtein similarity on the lowercased forms.
    surviving: list[tuple[str, float]] = []
    for canonical in canonicals:
        similarity = Levenshtein.normalized_similarity(text_lower, canonical.lower())
        if similarity >= evidence.min_phonetic_similarity:
            surviving.append((canonical, similarity))

    if not surviving:
        return None

    fanout = index.phonetic_fanout.get(key, len(canonicals))
    key_len = len(key)

    # Select the best candidate by Evidence_Score, tie-broken deterministically
    # by lowest alias_ordinal then canonical name so selection is stable.
    def _candidate_key(item: tuple[str, float]) -> tuple:
        canonical, similarity = item
        score = phonetic_evidence_score(similarity, key_len, fanout)
        ordinal = index.alias_ordinal.get(canonical.lower(), float("inf"))
        return (-round(score, 6), ordinal, canonical)

    best_canonical, best_similarity = min(surviving, key=_candidate_key)
    score = phonetic_evidence_score(best_similarity, key_len, fanout)
    return _make_result(best_canonical, score, "phonetic", index)


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
    evidence: EvidenceParams | None = None,
) -> MatchResult | None:
    """Length-adaptive fuzzy matching using BK-tree and length_buckets.

    - Skip if input length < min_candidate_length (default 4)
    - Skip if input is in the Block_List
    - Use narrow() to get candidates
    - Compute Levenshtein distance for each candidate
    - Tie-break on (distance, alias_ordinal)
    - Score the best distance

    Confidence (Req 3.8, 3.13)
    --------------------------
    * Legacy path (``evidence`` is ``None`` or ``evidence.enabled`` is
      ``False``): returns ``fuzzy_confidence(best_distance)``, matching
      Baseline (Req 12.9).
    * Evidence path (``evidence.enabled``): returns a
      :func:`fuzzy_evidence_score` computed from the Relative_Distance
      (best distance divided by the Span character length), which is
      non-increasing in Relative_Distance and bounded to [0.55, 0.92]
      (Req 3.8, 3.13).

    Parameters
    ----------
    fuzzy_score_cutoff : float
        Reserved for future use — rapidfuzz score floor (currently unused
        since matching is distance-based via BK-tree).
    min_candidate_length : int
        Minimum input length to attempt fuzzy matching.
    evidence : EvidenceParams | None
        Evidence-scoring inputs (Req 3). ``None`` or disabled selects the
        legacy flat-confidence path.
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

    if evidence is not None and evidence.enabled:
        # Evidence path: score from Relative_Distance (Req 3.8, 3.13). Span
        # length is its lowercased character count, matching Req 4.2's length
        # measure; guarded against a zero-length Span.
        span_len = len(text_lower)
        relative_distance = best_distance / span_len if span_len else 1.0
        confidence = fuzzy_evidence_score(relative_distance)
    else:
        confidence = fuzzy_confidence(best_distance)
    return _make_result(canonical, confidence, "fuzzy", index)


# ---------------------------------------------------------------------------
# Strategy 6: Substring match
# ---------------------------------------------------------------------------


def match_substring(text_lower: str, index: MatchIndex, snapshot: DatasetSnapshot | None = None) -> MatchResult | None:
    """Check if text is a substring of any canonical_map key.

    Only try when input length >= MIN_SUBSTRING_LENGTH (6).
    Returns confidence 0.80 on match.

    Checks the Block_List before returning a match — common English words
    that happen to be substrings of entity names are rejected.

    If multiple matches, pick the one with lowest alias_ordinal.
    """
    if len(text_lower) < MIN_SUBSTRING_LENGTH:
        return None

    # Block_List guard — prevents false substring matches on common words
    if snapshot is not None and is_blocked(text_lower, snapshot):
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
