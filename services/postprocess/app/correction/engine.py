"""Correction_Engine — correct_single, correct_text, correct_words, sort key, party display.

Provides the single-token correction entry point that short-circuits through
the strategy chain in the documented order, the deterministic tie-break sort
key for choosing among competing corrections across overlapping spans, the
party display heuristic wrapper, the ``correct_text`` function that ports
the JavaScript ``correctLocations`` algorithm, and the ``correct_words``
function that ports the word-level n-gram joining loop from
``formatTranscriptionResponse``.

Requirements: 3.1, 3.2, 3.9, 3.10, 3.11, 3.12, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.correction.blocklist import is_stopword, is_title, is_word_stopword
from app.correction.capitalization import (
    merged_punctuated_word,
    replaced_punctuated_word,
)
from app.correction.confidence import (
    align_span_to_words,
    confidence_gate_blocks_approximate,
    span_confidence,
)
from app.correction.context import context_gate_rejects_person
from app.correction.gates import GateContext, GateTally
from app.correction.lexicon import lexicon_gate_blocks_approximate
from app.correction.scoring import JOINED_CONFIDENCE, STRATEGY_RANK, TITLE_PERSON_CONFIDENCE
from app.correction.strategies import (
    MIN_CANDIDATE_LENGTH,
    EvidenceParams,
    MatchResult,
    get_party_display,
    is_deterministic,
    match_component,
    match_exact,
    match_fused,
    match_fuzzy,
    match_initials,
    match_phonetic,
    match_substring,
)
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex


def correct_single(
    text: str,
    span_len: int,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    *,
    fuzzy_score_cutoff: float = 0.70,
    min_candidate_length: int = MIN_CANDIDATE_LENGTH,
    gate_context: GateContext | None = None,
    span_conf: float | None = None,
    tally: GateTally | None = None,
    words: list[dict] | None = None,
    span_start_index: int | None = None,
    acceptance_threshold: float | None = None,
) -> MatchResult | None:
    """Run the short-circuiting strategy chain for a single text span.

    Behaviour:
      1. Lowercase the text.
      2. If the lowercased text is a stopword → return None immediately.
      3. If len(text_lower) < min_candidate_length (default 4): try only
         ``match_exact`` and ``match_fused``, then return.
      4. Otherwise, try each strategy in order, returning on first hit:
         exact → fused → initials → phonetic → fuzzy → substring.
      5. If no strategy matches, return None.

    Confidence_Gate (Req 1)
    -----------------------
    The Deterministic_Strategy members (``exact``, ``fused``, ``initials``) are
    evaluated at every Span_Confidence including Unknown (Req 1.1). The
    Approximate_Strategy members (``phonetic``, ``fuzzy``, ``substring``) are
    evaluated only when the Confidence_Gate does not block them. The gate is
    consulted **only when the GateContext is non-inert** — i.e. at least one
    feature flag is on — so that with every flag off this function reproduces
    the Baseline chain exactly (Req 12.9); see ``app.correction.confidence`` for
    the activation rationale. When the gate blocks approximate matching and no
    deterministic strategy hits, the caller preserves the Span unchanged
    (Req 1.9).

    Lexicon_Gate (Req 2)
    --------------------
    A Span of ordinary English words is also excluded from the
    Approximate_Strategy members when the ``lexicon_gate_enabled`` flag is on
    (and the context non-inert), the Span is absent from the Dataset_Cache
    alias set, every token is a lexicon member, and Span_Confidence is Unknown
    or at/above the Lexicon_Override_Threshold (Req 2.5, 2.6, 2.8). A known
    confidence strictly below the override permits approximate evaluation
    (Req 2.7). Deterministic strategies are never gated (Req 2.9, 2.14). When
    the flag is off or the lexicon failed to load, approximate evaluation is
    permitted for any Span no other gate rejects (Req 2.13). A rejected Span is
    preserved unchanged with no correction entry (Req 2.12), handled by the
    caller exactly as for the Confidence_Gate.

    Parameters
    ----------
    text : str
        The raw text span to correct (not yet lowercased).
    span_len : int
        The number of tokens this span covers (used externally for
        tie-breaking, not consumed here).
    index : MatchIndex
        The derived lookup structures built from the Dataset_Cache.
    snapshot : DatasetSnapshot
        The immutable dataset snapshot (provides stopword/block lists).
    fuzzy_score_cutoff : float
        Minimum score cutoff passed to ``match_fuzzy`` (default 0.70).
    min_candidate_length : int
        Minimum input length for full strategy chain (default 4).
    gate_context : GateContext | None
        The per-request gate inputs (task 1.4). ``None`` is normalised to an
        inert context, which is the Baseline path: no gate applies and the
        strategy chain runs exactly as before. Later tasks (2.x, 4.x) read the
        resolved thresholds and request-scoped inputs from this context to
        gate the Approximate_Strategy members; this task only threads it
        through so those tasks widen no signature.
    span_conf : float | None
        The Span_Confidence for this Span (Req 1.3-1.5), or ``None`` for
        Unknown. Consulted for the Confidence_Gate only when the gate context
        is non-inert. Callers (``correct_text``, ``correct_words``, and the
        title-person helpers) derive it from the Words aligned to the Span.
    tally : GateTally | None
        Optional per-request observability collector (task 6.1, Req 13). When
        supplied and the gate context is non-inert, this function records which
        gate rejected the Span from approximate matching — attributed to the
        first gate in evaluation order (asr_confidence before lexicon before
        the phonetic/fuzzy/component sub-gates, Req 13.9) — and records the Span
        as having had an Approximate_Strategy evaluated when the approximate
        chain runs (Req 13.4). Purely observational: it never changes the match
        outcome, is ``None`` on the Baseline path, and its bookkeeping never
        raises (Req 13.11).
    words : list[dict] | None
        The request's Words list, used by the Context_Gate (Req 7) to locate
        the Span within the transcript when deciding whether a Context_Signal
        supports an Approximate person match. ``None`` (the transcript-text
        path with no Words, or a caller that supplies none) is treated as an
        empty Words list, which — with no speaker attribution and an Unknown
        Capitalization_Prior — yields no signal, so an unsupported Approximate
        person match is rejected when the Context_Gate is active.
    span_start_index : int | None
        The index of this Span's first Word within *words* (Req 7.1a, 7.4, 7.5).
        Callers pass the token/word index at which the Span begins. ``None``
        defaults to 0. Unused when the Context_Gate is inactive.

    Confidence_Gate, Lexicon_Gate, and Context_Gate (Req 1, 2, 7)
    -------------------------------------------------------------
    The Context_Gate (Req 7) is applied to an Approximate_Strategy match on a
    **person** entity after the strategy produces it: when the context is
    non-inert and ``context_gate_enabled`` is set and no Context_Signal is
    present for the Span, the match is rejected and ``None`` returned, so the
    caller preserves the Span and no further Approximate_Strategy is evaluated
    (Req 7.2, 7.8, 7.9). Deterministic matches and non-person matches are never
    context-gated (Req 7.3, 7.7). A Title_Prefix immediately preceding the Span
    is itself a Context_Signal (Req 7.1a), so a title-prefixed person match
    passes the gate keeping its normal confidence (Req 7.6, 7.10). With every
    flag off the gate is inert and the output stays Baseline (Req 12.9).

    Returns
    -------
    MatchResult | None
        The best match if any strategy succeeds, otherwise None.
    """
    # Normalise an absent context to an inert one so callers and later gate
    # tasks share one shape. An inert context leaves this function's path
    # unchanged (Req 12.9).
    if gate_context is None:
        gate_context = GateContext.inert()

    text_lower = text.lower()

    # Confidence_Gate (Req 1.2, 1.11): the Approximate_Strategy members are
    # excluded when the gate is active AND blocks them. The gate is active only
    # when the context is non-inert (at least one flag on) — with every flag
    # off this stays False and the full Baseline chain runs (Req 12.9). See
    # app.correction.confidence for the activation rationale. The
    # High_Confidence_Threshold is read from the provider profile on the context
    # (task 2.12.3), so it branches on ``CorrectionOptions.provider``; with
    # ``provider_profiles_enabled`` off every provider resolves the ``deepgram``
    # profile — the task 1.1 threshold — so the value is unchanged (Req 12.9).
    block_approximate = (
        not gate_context.is_inert
        and confidence_gate_blocks_approximate(
            span_conf, gate_context.profile.high_confidence_threshold
        )
    )
    # Observability (Req 13.1, 13.5, 13.9): attribute a Confidence_Gate
    # rejection to the ``asr_confidence`` gate — the first gate in evaluation
    # order. Purely a tally write; does not change the outcome.
    if block_approximate and tally is not None:
        tally.record_rejection("asr_confidence", text, span_conf)

    # Lexicon_Gate (Req 2.5-2.8, 2.10, 2.13): reject the Approximate_Strategy
    # members for a Span of ordinary English words. Active only when the
    # ``lexicon_gate_enabled`` flag is on and the context is non-inert — with
    # the flag off (or every flag off) this stays False and approximate
    # evaluation is permitted (Req 2.13, 12.9). Deterministic strategies are
    # never gated (Req 2.9, 2.14), so the check sits alongside the
    # Confidence_Gate before the approximate chain. Alias membership is the
    # whole Span against the Dataset_Cache alias set (``index.canonical_map``
    # holds lowercased alias/canonical keys); an alias is never gated
    # (Req 2.5, 2.8). Multi-token Spans are gated only when every token is a
    # lexicon member and the Span is not an alias (Req 2.8) — the helper
    # enforces this. Membership is evaluated per token lowercased and stripped
    # of edge punctuation inside the helper (Req 2.10). The
    # Lexicon_Override_Threshold and the Unknown-confidence rejection policy are
    # read from the provider profile on the context (task 2.12.3), so the gate
    # branches on ``CorrectionOptions.provider``: ``deepgram`` keeps the Req 2.6
    # unconditional Unknown-confidence rejection, while ``khaya``/``hybrid`` opt
    # out of it (``lexicon_gate_reject_unknown=False``) only when
    # ``provider_profiles_enabled`` is on. With the flag off every provider
    # resolves the ``deepgram`` profile, so the threshold and policy are the
    # task 1.1 / Req 2.6 values and behaviour is unchanged (Req 12.9).
    if (
        not block_approximate
        and not gate_context.is_inert
        and gate_context.config.lexicon_gate_enabled
    ):
        block_approximate = lexicon_gate_blocks_approximate(
            text_lower.split(),
            span_conf,
            lexicon=gate_context.lexicon,
            is_alias=text_lower in index.canonical_map,
            override_threshold=gate_context.profile.lexicon_override_threshold,
            reject_unknown=gate_context.profile.lexicon_gate_reject_unknown,
        )
        # Observability (Req 13.1, 13.5, 13.9): attribute a Lexicon_Gate
        # rejection to the ``lexicon`` gate. This branch only runs when the
        # Confidence_Gate did not already block, so ``lexicon`` is correctly
        # the first gate to reject this Span here (Req 13.9). Tally-only.
        if block_approximate and tally is not None:
            tally.record_rejection("lexicon", text, span_conf)

    # Stopword guard — checked before any strategy (Requirement 4.7)
    if is_stopword(text_lower, snapshot):
        return None

    # Short inputs (< min_candidate_length chars): only exact and fused
    # (both Deterministic — evaluated at every Span_Confidence, Req 1.1).
    if len(text_lower) < min_candidate_length:
        result = match_exact(text_lower, index)
        if result is not None:
            return result
        result = match_fused(text_lower, index)
        if result is not None:
            return result
        return None

    # --- Deterministic_Strategy members: evaluated at every Span_Confidence
    #     including Unknown, never gated (Req 1.1) ---
    result = match_exact(text_lower, index)
    if result is not None:
        return result

    result = match_fused(text_lower, index)
    if result is not None:
        return result

    result = match_initials(text_lower, index)
    if result is not None:
        return result

    # --- Approximate_Strategy members: excluded when the Confidence_Gate
    #     blocks them (Req 1.2, 1.11) ---
    if block_approximate:
        return None

    # Observability (Req 13.4): the Span has passed every gate that would keep
    # it out of approximate matching, so at least one Approximate_Strategy is
    # about to be evaluated for it. Record it once for the
    # approx-spans-evaluated metric. Tally-only; does not change the outcome.
    if tally is not None:
        tally.record_approx_evaluated(text)

    # Evidence-scaled scoring (Req 3). When ``evidence_confidence_enabled`` is
    # on (and the context non-inert), the phonetic and fuzzy strategies compute
    # an Evidence_Score and the phonetic Min_Phonetic_Key_Length /
    # Min_Phonetic_Similarity sub-gates apply (Req 3.1-3.8, 3.12-3.14). With the
    # flag off the params are inert and the strategies return their legacy flat
    # constants, so the flag-off path is Baseline-equivalent (Req 12.9).
    evidence = _resolve_evidence_params(gate_context)

    result = match_phonetic(text_lower, index, snapshot, evidence=evidence)
    if result is not None:
        return _finalize_approximate(
            result, text, span_len, gate_context, words, span_start_index, snapshot,
            index, tally, acceptance_threshold,
        )

    result = match_fuzzy(
        text_lower,
        index,
        snapshot,
        fuzzy_score_cutoff=fuzzy_score_cutoff,
        min_candidate_length=min_candidate_length,
        evidence=evidence,
    )
    if result is not None:
        return _finalize_approximate(
            result, text, span_len, gate_context, words, span_start_index, snapshot,
            index, tally, acceptance_threshold,
        )

    # Component_Match (Req 5) replaces the legacy arbitrary-infix substring
    # strategy, ranked last after ``fuzzy`` (Req 5.10). Because it changes the
    # accepted-match set, it rides the same non-inert-context gate as the other
    # precision behaviours: an inert context (every flag off) takes the legacy
    # ``match_substring`` path so the flag-off output stays Baseline-equivalent
    # (Req 12.9); a non-inert context uses ``match_component``. The Lexicon_Gate
    # sub-check (Req 5.5) is active only when ``lexicon_gate_enabled`` is on and
    # the lexicon loaded — independent of the Confidence_Gate's earlier block,
    # so a lexicon word whose confidence sits just below the override (which the
    # Lexicon_Gate above lets through) is still rejected here (Req 5.5).
    if gate_context.is_inert:
        result = match_substring(text_lower, index, snapshot)
    else:
        lexicon_active = (
            gate_context.config.lexicon_gate_enabled
            and gate_context.lexicon is not None
            and getattr(gate_context.lexicon, "loaded", False)
        )
        result = match_component(
            text_lower,
            index,
            snapshot,
            min_len=gate_context.config.component_match_min_length,
            lexicon=gate_context.lexicon,
            lexicon_active=lexicon_active,
        )
    if result is not None:
        return _finalize_approximate(
            result, text, span_len, gate_context, words, span_start_index, snapshot,
            index, tally, acceptance_threshold,
        )

    return None


def _finalize_approximate(
    result: MatchResult,
    text: str,
    span_len: int,
    gate_context: GateContext,
    words: list[dict] | None,
    span_start_index: int | None,
    snapshot: DatasetSnapshot,
    index: MatchIndex,
    tally: GateTally | None,
    acceptance_threshold: float | None,
) -> MatchResult | None:
    """Apply the Context_Gate then the Sitting_Scope penalty to an approximate match.

    Runs the two Phase 2 gates that act on an Approximate_Strategy match in
    evaluation order: the Context_Gate first (Req 7), then the Sitting_Scope
    Evidence_Score penalty (Req 8). The Context_Gate may reject the match
    outright (``None``); only a surviving match reaches the Sitting_Scope step.
    Returning ``None`` from either gate causes the caller to preserve the Span
    and evaluate no further Approximate_Strategy for it (Req 7.8, 7.9, 8.9).
    """
    gated = _apply_context_gate(
        result, text, span_len, gate_context, words, span_start_index, snapshot,
        index, tally,
    )
    if gated is None:
        return None
    return _apply_sitting_scope(gated, text, gate_context, tally, acceptance_threshold)


def _apply_sitting_scope(
    result: MatchResult,
    text: str,
    gate_context: GateContext,
    tally: GateTally | None,
    acceptance_threshold: float | None,
) -> MatchResult | None:
    """Apply the Sitting_Scope Out_Of_Scope_Penalty to an approximate match (Req 8).

    Subtracts ``Out_Of_Scope_Penalty`` from the Evidence_Score of an
    Approximate_Strategy match on a **person** candidate whose canonical entity
    is absent from the resolved Sitting_Scope, clamping the adjusted score at a
    lower bound of 0.0 (Req 8.3), and returns a new :class:`MatchResult` carrying
    the adjusted confidence. Deterministic matches and non-person candidates are
    left unadjusted (Req 8.6); because ``correct_single`` reaches this helper
    only on the approximate strategies, ``result.strategy`` is already
    approximate, but the ``is_deterministic`` guard keeps the contract explicit.

    Activation (Req 8.2, 8.5, 12.9)
    -------------------------------
    The penalty is applied only when the context is non-inert **and**
    ``sitting_scope_enabled`` is set **and** a non-empty Sitting_Scope member
    set is present on the context. With every flag off, the flag off, or no
    scope available (whitespace-only id, empty/errored/timed-out lookup — all
    resolved to ``sitting_scope=None`` by the pipeline, Req 8.5), the match
    rides through unadjusted and the output stays Baseline (Req 12.9).

    Rejection (Req 8.9)
    -------------------
    When *acceptance_threshold* is supplied and a person candidate whose
    Evidence_Score met that threshold falls below it after the penalty, the
    candidate is rejected (``None`` returned) and the rejection is recorded under
    the ``sitting_scope`` gate value on the *tally*. A candidate already below
    the threshold, or with no threshold supplied, keeps the adjusted score for
    the caller's own threshold comparison.
    """
    if gate_context.is_inert or not gate_context.config.sitting_scope_enabled:
        return result
    scope = gate_context.sitting_scope
    if not scope:
        # No scope member set available (Req 8.2, 8.5): no adjustment.
        return result
    if result.entity_kind != "person" or is_deterministic(result.strategy):
        # Restrict the penalty to Approximate_Strategy person candidates (Req 8.6).
        return result
    if result.canonical in scope:
        # In-scope person candidate — no penalty (Req 8.3).
        return result

    penalty = gate_context.config.out_of_scope_penalty
    adjusted = max(0.0, result.confidence - penalty)

    # Rejection (Req 8.9): a candidate that met the acceptance threshold but
    # falls below it after the penalty is rejected under the ``sitting_scope``
    # gate. Uses the original (pre-penalty) confidence to decide whether it had
    # met the threshold.
    if (
        acceptance_threshold is not None
        and result.confidence >= acceptance_threshold
        and adjusted < acceptance_threshold
    ):
        if tally is not None:
            tally.record_rejection("sitting_scope", text, None)
        return None

    return MatchResult(
        canonical=result.canonical,
        confidence=adjusted,
        strategy=result.strategy,
        entity_kind=result.entity_kind,
        entity_type=result.entity_type,
    )


def _apply_context_gate(
    result: MatchResult,
    text: str,
    span_len: int,
    gate_context: GateContext,
    words: list[dict] | None,
    span_start_index: int | None,
    snapshot: DatasetSnapshot,
    index: MatchIndex,
    tally: GateTally | None,
) -> MatchResult | None:
    """Apply the Context_Gate to an Approximate_Strategy match (Req 7).

    Returns *result* unchanged when the gate accepts it, or ``None`` when the
    gate rejects it — in which case the caller preserves the Span unchanged and
    (because ``correct_single`` returns ``None``) evaluates no further
    Approximate_Strategy for that Span (Req 7.8, 7.9).

    The gate is applied only when the context is non-inert **and**
    ``context_gate_enabled`` is set — the same activation pattern as the
    Confidence_Gate and Lexicon_Gate — so with every flag off (or this flag
    off) the match rides through unchanged and the output stays Baseline
    (Req 12.9). It rejects only an Approximate_Strategy match on a **person**
    entity with no Context_Signal (Req 7.2); Deterministic matches (Req 7.3)
    and non-person matches (Req 7.7) are never gated. A title-prefixed person
    match carries Context_Signal (a), so it passes the gate keeping its normal
    confidence (Req 7.6, 7.10).

    Because ``correct_single`` reaches this helper only on the approximate
    strategies, ``result.strategy`` here is always approximate; the
    ``is_deterministic`` guard is passed for completeness and to keep the pure
    gate function's contract explicit.
    """
    if gate_context.is_inert or not gate_context.config.context_gate_enabled:
        return result
    rejects = context_gate_rejects_person(
        result,
        span_start_index if span_start_index is not None else 0,
        span_len,
        words if words is not None else [],
        snapshot,
        index,
        gate_context.config.context_window_words,
        is_deterministic=is_deterministic(result.strategy),
    )
    if not rejects:
        return result
    # Observability (Req 13.2): a Context_Gate rejection is recorded under the
    # ``context`` gate value. Span_Confidence is not recomputed here — the tally
    # carries the Span text and gate; the confidence field is best-effort None
    # (the Context_Gate decision does not depend on it).
    if tally is not None:
        tally.record_rejection("context", text, None)
    return None


def _resolve_evidence_params(gate_context: GateContext) -> EvidenceParams:
    """Build the :class:`EvidenceParams` for the Approximate_Strategy scorers.

    Evidence scoring is active only when the context is non-inert (at least one
    flag on) **and** ``evidence_confidence_enabled`` is set. Otherwise an inert
    ``EvidenceParams`` (``enabled=False``) is returned so the strategies take
    their legacy flat-confidence path and the output stays Baseline-equivalent
    (Req 12.9).
    """
    if gate_context.is_inert or not gate_context.config.evidence_confidence_enabled:
        return EvidenceParams(enabled=False)
    # The two provider-calibrated thresholds (Min_Phonetic_Similarity,
    # Max_Relative_Distance) are read from the provider profile on the context
    # (task 2.12.3) so they branch on ``CorrectionOptions.provider``; the
    # non-calibrated params (key length, candidate cap) stay on GateConfig. With
    # ``provider_profiles_enabled`` off every provider resolves the ``deepgram``
    # profile — the task 1.1 values — so these are unchanged (Req 12.9).
    return EvidenceParams(
        enabled=True,
        min_phonetic_key_length=gate_context.config.min_phonetic_key_length,
        min_phonetic_similarity=gate_context.profile.min_phonetic_similarity,
        max_relative_distance=gate_context.profile.max_relative_distance,
        max_candidates_per_span=gate_context.config.max_candidates_per_span,
    )


def correction_sort_key(
    result: MatchResult,
    span_len: int,
    sitting_scope: frozenset[str] | None = None,
) -> tuple:
    """Deterministic sort key for selecting among competing corrections.

    Lower value = better match. Applied when multiple candidates exist
    for overlapping spans.

    sort_key = (-round(confidence, 6), member_rank, -span_len,
                STRATEGY_RANK[strategy], canonical)

    Tie-break order (most significant first):
      1. Higher confidence wins (negated so lower tuple value = better).
      2. Sitting_Scope member wins over a non-member at equal confidence
         (Req 8.4) — a member sorts ahead of a non-member *before* the existing
         span-length / strategy-rank / canonical order. Applied only when a
         non-empty *sitting_scope* is supplied; otherwise this component is a
         constant and the ordering is unchanged (Baseline, Req 12.9).
      3. Longer span wins (negated).
      4. Lower strategy rank wins (exact < fused < joined < ... < substring).
      5. Lexicographic canonical name for full determinism.

    Parameters
    ----------
    result : MatchResult
        The match result to compute the sort key for.
    span_len : int
        The number of tokens the matched span covers.
    sitting_scope : frozenset[str] | None
        The resolved Sitting_Scope member set (Req 8.4). When supplied and
        non-empty, a candidate whose canonical is a member sorts ahead of a
        non-member at equal confidence, before the existing tie-break. ``None``
        or empty leaves the ordering unchanged.

    Returns
    -------
    tuple
        A tuple suitable for ``min()`` or ``sorted()`` comparisons.
    """
    # 0 for a member (sorts first), 1 otherwise. A constant 1 when no scope is
    # supplied, so the tie-break order is identical to Baseline (Req 12.9).
    member_rank = 0 if sitting_scope and result.canonical in sitting_scope else 1
    return (
        -round(result.confidence, 6),
        member_rank,
        -span_len,
        STRATEGY_RANK.get(result.strategy, 99),
        result.canonical,
    )


def apply_party_display(
    result: MatchResult, original_text: str, index: MatchIndex
) -> MatchResult:
    """Apply party display heuristic — abbreviation vs full name.

    For party entities, determines whether to show the abbreviation
    (when the original input is short, i.e. ``len(original) <= len(abbr) + 1``)
    or the canonical full name.

    Non-party entities are returned unchanged.

    Parameters
    ----------
    result : MatchResult
        The match result to potentially transform.
    original_text : str
        The original text that was matched (pre-lowercasing).
    index : MatchIndex
        The match index containing the party abbreviation map.

    Returns
    -------
    MatchResult
        Either the original result (non-party or no abbreviation found)
        or a new MatchResult with the display-appropriate canonical name.
    """
    if result.entity_kind != "party":
        return result

    display = get_party_display(original_text, result.canonical, index)
    return MatchResult(
        canonical=display,
        confidence=result.confidence,
        strategy=result.strategy,
        entity_kind=result.entity_kind,
        entity_type=result.entity_type,
    )


# ---------------------------------------------------------------------------
# Token representation for character-offset tokenization
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z\u00C0-\u00FF'\-]+")


@dataclass
class _Token:
    """One whitespace-delimited token with its character offsets."""

    word: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# TextCorrection and TextCorrectionResult
# ---------------------------------------------------------------------------


@dataclass
class TextCorrection:
    """One correction applied to the text."""

    start_char: int  # Character offset of the original span start
    end_char: int  # Character offset of the original span end
    original: str  # The original text that was replaced
    replacement: str  # The corrected replacement text
    entity_kind: str  # EntityKind value
    entity_type: str  # EntityType value
    strategy: str  # MatchStrategy value
    confidence: float  # Match confidence


@dataclass
class TextCorrectionResult:
    """Result of correct_text containing the corrected text and metadata."""

    text: str  # The corrected text
    corrections: list[TextCorrection]  # Applied corrections
    entities_found: list[tuple[str, str, str]]  # (canonical, kind, type) including identity matches


# ---------------------------------------------------------------------------
# Title-person matching (Level 2 strategy)
# ---------------------------------------------------------------------------


def _match_title_person(
    tokens: list[_Token],
    title_index: int,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    min_confidence: float,
    *,
    fuzzy_score_cutoff: float = 0.70,
    min_candidate_length: int = MIN_CANDIDATE_LENGTH,
    gate_context: GateContext | None = None,
    words: list[dict] | None = None,
    tally: GateTally | None = None,
) -> tuple[MatchResult, int] | None:
    """Try to match person name tokens following a title token.

    Tries windows of 3, 2, 1 tokens after the title. On match, returns
    the MatchResult and the number of name tokens consumed.

    Span_Confidence for each name window is derived from the Words aligned to
    that window (Req 1.7). A Title_Prefix immediately preceding a high-confidence
    Span still excludes the Approximate_Strategy members from the name window,
    because the Confidence_Gate applies to the name Span regardless of the
    preceding title (Req 1.11).
    """
    threshold = min(min_confidence, 0.65)
    max_lookahead = min(3, len(tokens) - title_index - 1)

    if max_lookahead < 1:
        return None

    # Name tokens begin at the token position immediately after the title,
    # which aligns to the same index in the Words list (Req 1.7, 1.11).
    name_start = title_index + 1

    # Try window sizes from largest to smallest
    for win_size in range(max_lookahead, 0, -1):
        name_tokens = tokens[name_start : name_start + win_size]
        phrase = " ".join(t.word for t in name_tokens)

        # Skip if single-token window is a stopword
        if win_size == 1 and is_stopword(phrase.lower(), snapshot):
            continue

        # Skip if the last token in the window is a stopword
        if win_size > 1 and is_stopword(name_tokens[-1].word.lower(), snapshot):
            continue

        span_conf = _slice_span_confidence(
            words, name_start, [t.word for t in name_tokens]
        )
        match = correct_single(
            phrase,
            win_size,
            index,
            snapshot,
            fuzzy_score_cutoff=fuzzy_score_cutoff,
            min_candidate_length=min_candidate_length,
            gate_context=gate_context,
            span_conf=span_conf,
            tally=tally,
            words=words,
            span_start_index=name_start,
            acceptance_threshold=threshold,
        )
        if match and match.entity_kind == "person" and match.confidence >= threshold:
            return (match, win_size)

    # Surname-only fallback: try single token after title via surname_map
    last_token = tokens[title_index + 1] if title_index + 1 < len(tokens) else None
    if last_token:
        surname = last_token.word.lower()
        candidates = index.surname_map.get(surname)
        if candidates:
            canonical = candidates[0]
            kind = index.entity_kind_map.get(canonical)
            if kind == "person":
                entity_type = index.entity_type_map.get(canonical, "person")
                result = MatchResult(
                    canonical=canonical,
                    confidence=TITLE_PERSON_CONFIDENCE,
                    strategy="title_person",
                    entity_kind="person",
                    entity_type=entity_type,
                )
                return (result, 1)

        # Phonetic then fuzzy on single token after title. Evidence-scaled
        # scoring (Req 3) applies to these surname fallbacks too when enabled;
        # inert when the flag is off (Req 12.9).
        evidence = _resolve_evidence_params(
            gate_context if gate_context is not None else GateContext.inert()
        )
        phonetic_match = match_phonetic(surname, index, evidence=evidence)
        if (
            phonetic_match
            and phonetic_match.entity_kind == "person"
            and phonetic_match.confidence >= threshold
        ):
            return (phonetic_match, 1)

        fuzzy_match = match_fuzzy(surname, index, snapshot, evidence=evidence)
        if (
            fuzzy_match
            and fuzzy_match.entity_kind == "person"
            and fuzzy_match.confidence >= threshold
        ):
            return (fuzzy_match, 1)

    return None


# ---------------------------------------------------------------------------
# correct_text — port of JS correctLocations
# ---------------------------------------------------------------------------


def _slice_span_confidence(
    words: list[dict] | None,
    start_index: int,
    token_words: list[str],
) -> float | None:
    """Derive Span_Confidence for a token slice from the request Words (Req 1.7-1.10).

    Aligns the Span tokens to the Words at the same sequence positions and
    returns the minimum clamped Word_Confidence over the aligned Words. Returns
    Unknown (``None``) when the Words list is absent or empty (Req 1.10) or when
    the tokens do not align (Req 1.8), which leaves the Confidence_Gate inactive
    for that Span (Req 1.6).
    """
    if not words:
        return None
    covered = align_span_to_words(token_words, words, start_index)
    if covered is None:
        return None
    return span_confidence(covered)


def correct_text(
    text: str,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    *,
    min_confidence: float = 0.75,
    fuzzy_score_cutoff: float = 0.70,
    min_candidate_length: int = MIN_CANDIDATE_LENGTH,
    gate_context: GateContext | None = None,
    words: list[dict] | None = None,
    tally: GateTally | None = None,
) -> TextCorrectionResult:
    """Correct all entity references in a transcript text.

    Ports the JavaScript ``correctLocations`` function. Scans 1–4 word
    n-grams at each position, applies title-person branch first, then the
    main strategy chain, and returns the corrected text with metadata.

    Algorithm:
      1. Tokenize text by character offsets using regex [A-Za-zÀ-ÿ'-]+
      2. For each position, check title-person branch first
      3. Then try n-gram windows (4→1) with joined fallback for n > 1
      4. Record identity matches in entities_found
      5. Apply corrections end-to-start

    Parameters
    ----------
    text : str
        The full transcript text to correct.
    index : MatchIndex
        The derived lookup structures from the Dataset_Cache.
    snapshot : DatasetSnapshot
        The immutable dataset snapshot (block lists, stopwords, etc.).
    min_confidence : float
        Minimum confidence threshold to accept a correction (default 0.75).
    fuzzy_score_cutoff : float
        Minimum score cutoff passed to ``match_fuzzy`` (default 0.70).
    min_candidate_length : int
        Minimum input length for full strategy chain (default 4).
    gate_context : GateContext | None
        The per-request gate inputs (task 1.4), forwarded to ``correct_single``
        and the title-person helper. ``None`` is normalised to an inert
        context so the transcript-text path is unchanged from Baseline
        (Req 12.9).
    words : list[dict] | None
        The request's Words list, used to derive Span_Confidence for each Span
        by aligning Span tokens to the Words at the same sequence positions
        (Req 1.7). When absent or empty, every Span_Confidence is Unknown so the
        Confidence_Gate stays inactive for the transcript path (Req 1.10). When
        the gate context is inert this is unused (Baseline path).

    Returns
    -------
    TextCorrectionResult
        The corrected text, list of corrections applied, and entities found.
    """
    # Normalise an absent context to an inert one (Baseline path, Req 12.9).
    if gate_context is None:
        gate_context = GateContext.inert()

    if not text or not text.strip():
        return TextCorrectionResult(text=text, corrections=[], entities_found=[])

    # --- Step 1: Tokenize with character offsets ---
    tokens: list[_Token] = []
    for m in _TOKEN_RE.finditer(text):
        tokens.append(_Token(word=m.group(), start=m.start(), end=m.end()))

    if not tokens:
        return TextCorrectionResult(text=text, corrections=[], entities_found=[])

    corrections: list[TextCorrection] = []
    entities_found: list[tuple[str, str, str]] = []
    consumed: set[int] = set()

    i = 0
    while i < len(tokens):
        if i in consumed:
            i += 1
            continue

        # --- Step 2: Title-person branch (checked first) ---
        if is_title(tokens[i].word, snapshot):
            title_result = _match_title_person(
                tokens,
                i,
                index,
                snapshot,
                min_confidence,
                fuzzy_score_cutoff=fuzzy_score_cutoff,
                min_candidate_length=min_candidate_length,
                gate_context=gate_context,
                words=words,
                tally=tally,
            )
            if title_result:
                match, tokens_consumed = title_result

                # Name tokens start after the title
                name_start = i + 1
                name_slice = tokens[name_start : name_start + tokens_consumed]

                if name_slice:
                    original = text[name_slice[0].start : name_slice[-1].end]

                    # Double-title guard: strip leading word from canonical if it
                    # matches the title token to avoid duplication
                    corrected_name = match.canonical
                    title_word = tokens[i].word.lower()
                    canonical_first_word = corrected_name.split()[0].lower() if corrected_name.split() else ""
                    if title_word == canonical_first_word:
                        # Strip the first word from the canonical
                        parts = corrected_name.split(None, 1)
                        corrected_name = parts[1] if len(parts) > 1 else corrected_name

                    is_identity = corrected_name.lower() == original.lower()

                    # Record entity in entities_found regardless
                    entity_kind = match.entity_kind or "person"
                    entity_type = match.entity_type or "person"
                    entities_found.append((match.canonical, entity_kind, entity_type))

                    if not is_identity:
                        # Emit correction for the name tokens only (title stays as-is)
                        corrections.append(
                            TextCorrection(
                                start_char=name_slice[0].start,
                                end_char=name_slice[-1].end,
                                original=original,
                                replacement=corrected_name,
                                entity_kind=entity_kind,
                                entity_type=entity_type,
                                strategy=match.strategy,
                                confidence=match.confidence,
                            )
                        )

                    # Mark title and name tokens as consumed
                    consumed.add(i)
                    for j in range(tokens_consumed):
                        consumed.add(name_start + j)

                    # Advance past consumed tokens
                    i += tokens_consumed + 1
                    continue

        # --- Step 3: Main n-gram windows (4→1) ---
        best_match: MatchResult | None = None
        best_ngram_size = 0
        best_confidence = 0.0

        max_n = min(4, len(tokens) - i)
        for n in range(max_n, 0, -1):
            # Skip if any token in this window is already consumed
            if any(i + j in consumed for j in range(n)):
                continue

            token_slice = tokens[i : i + n]
            phrase = " ".join(t.word for t in token_slice)

            # N-gram stopword edge guards (only for n > 1)
            if n > 1:
                first_word = token_slice[0].word.lower().rstrip(".")
                last_word = token_slice[-1].word.lower().rstrip(".")
                first_is_initial = len(token_slice[0].word) <= 2 and re.match(
                    r"^[a-z]\.?$", token_slice[0].word, re.IGNORECASE
                )
                first_is_title_prefix = is_title(token_slice[0].word, snapshot)
                if not first_is_initial and not first_is_title_prefix and is_stopword(first_word, snapshot):
                    continue
                if is_stopword(last_word, snapshot):
                    continue

            # Strategy A: try the phrase as-is via correct_single.
            # Derive Span_Confidence from the Words aligned to this token slice
            # (Req 1.7); Unknown when no Words list or the tokens diverge.
            span_conf = _slice_span_confidence(
                words, i, [t.word for t in token_slice]
            )
            match = correct_single(
                phrase,
                n,
                index,
                snapshot,
                fuzzy_score_cutoff=fuzzy_score_cutoff,
                min_candidate_length=min_candidate_length,
                gate_context=gate_context,
                span_conf=span_conf,
                tally=tally,
                words=words,
                span_start_index=i,
                acceptance_threshold=min_confidence,
            )

            # Strategy B: joined match (for n > 1 when correct_single fails)
            if match is None and n > 1:
                joined_text = "".join(t.word for t in token_slice).lower()
                joined_text_stripped = re.sub(r"[\s\-']", "", joined_text)
                canonical = index.fused_map.get(joined_text_stripped)
                if canonical:
                    entity_kind = index.entity_kind_map.get(canonical, "location")
                    entity_type = index.entity_type_map.get(canonical, "supplementary")
                    match = MatchResult(
                        canonical=canonical,
                        confidence=JOINED_CONFIDENCE,
                        strategy="joined",
                        entity_kind=entity_kind,
                        entity_type=entity_type,
                    )

            # Apply party display heuristic
            if match is not None:
                match = apply_party_display(match, phrase, index)

            # Accept if it meets the confidence threshold
            if match and match.confidence >= min_confidence:
                is_identity = match.canonical.lower() == phrase.lower()

                if is_identity:
                    # Already correctly spelled — record as recognized entity
                    entities_found.append(
                        (match.canonical, match.entity_kind, match.entity_type)
                    )
                    for j in range(n):
                        consumed.add(i + j)
                    # Identity match found — don't try smaller windows
                    best_match = None
                    break

                if match.confidence > best_confidence:
                    best_match = match
                    best_ngram_size = n
                    best_confidence = match.confidence

        # --- Step 4: Record the best match as a correction ---
        if best_match:
            token_slice = tokens[i : i + best_ngram_size]
            original = text[token_slice[0].start : token_slice[-1].end]

            entity_kind = best_match.entity_kind or "location"
            entity_type = best_match.entity_type or "supplementary"

            corrections.append(
                TextCorrection(
                    start_char=token_slice[0].start,
                    end_char=token_slice[-1].end,
                    original=original,
                    replacement=best_match.canonical,
                    entity_kind=entity_kind,
                    entity_type=entity_type,
                    strategy=best_match.strategy,
                    confidence=best_match.confidence,
                )
            )
            entities_found.append((best_match.canonical, entity_kind, entity_type))
            for j in range(best_ngram_size):
                consumed.add(i + j)
        elif i not in consumed:
            consumed.add(i)

        i += 1

    # --- Step 5: Apply corrections end-to-start to preserve offsets ---
    result = text
    sorted_corrections = sorted(corrections, key=lambda c: c.start_char, reverse=True)
    for c in sorted_corrections:
        result = result[: c.start_char] + c.replacement + result[c.end_char :]

    return TextCorrectionResult(
        text=result,
        corrections=corrections,
        entities_found=entities_found,
    )


# ---------------------------------------------------------------------------
# WordCorrectionResult and correct_words — word-level n-gram joining
# ---------------------------------------------------------------------------

# Minimum token length for word-level matching (shorter tokens are skipped
# unless they match a party abbreviation of length 3-4)
_WORD_MIN_TOKEN_LENGTH = 3


@dataclass
class WordCorrectionResult:
    """Result of correct_words.

    Attributes
    ----------
    words : list[dict]
        Output word dicts (may be fewer than input if tokens were joined).
    corrections : list[tuple[str, str, str, float, str, str]]
        Each tuple is (original, corrected, strategy, confidence, kind, type).
    entities_found : list[tuple[str, str, str]]
        Each tuple is (canonical, kind, type) for recognized entities
        including identity matches.
    """

    words: list[dict]
    corrections: list[tuple[str, str, str, float, str, str]]
    entities_found: list[tuple[str, str, str]]


def _match_title_person_words(
    word_dicts: list[dict],
    title_index: int,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    min_confidence: float,
    *,
    fuzzy_score_cutoff: float = 0.70,
    min_candidate_length: int = MIN_CANDIDATE_LENGTH,
    gate_context: GateContext | None = None,
    tally: GateTally | None = None,
) -> tuple[MatchResult, int] | None:
    """Try to match person name tokens following a title in word dicts.

    Similar to _match_title_person but operates on word dicts rather
    than _Token objects. Returns (MatchResult, name_tokens_consumed) or None.
    """
    threshold = min(min_confidence, 0.65)
    max_lookahead = min(3, len(word_dicts) - title_index - 1)

    if max_lookahead < 1:
        return None

    for win_size in range(max_lookahead, 0, -1):
        name_words = word_dicts[title_index + 1: title_index + 1 + win_size]
        phrase = " ".join(wd.get("word", "") for wd in name_words)

        if win_size == 1 and is_stopword(phrase.lower(), snapshot):
            continue
        if win_size > 1 and is_stopword(
            name_words[-1].get("word", "").lower(), snapshot
        ):
            continue

        # Span_Confidence is derived directly from the covered Word dicts
        # (Req 1.3-1.5); a high-confidence name Span still excludes the
        # Approximate_Strategy members even behind a Title_Prefix (Req 1.11).
        span_conf = span_confidence(name_words)
        match = correct_single(
            phrase,
            win_size,
            index,
            snapshot,
            fuzzy_score_cutoff=fuzzy_score_cutoff,
            min_candidate_length=min_candidate_length,
            gate_context=gate_context,
            span_conf=span_conf,
            tally=tally,
            words=word_dicts,
            span_start_index=title_index + 1,
            acceptance_threshold=threshold,
        )
        if match and match.entity_kind == "person" and match.confidence >= threshold:
            return (match, win_size)

    # Surname-only fallback on single token after title
    if title_index + 1 < len(word_dicts):
        last_word = word_dicts[title_index + 1].get("word", "")
        surname = last_word.lower()
        candidates = index.surname_map.get(surname)
        if candidates:
            canonical = candidates[0]
            kind = index.entity_kind_map.get(canonical)
            if kind == "person":
                entity_type = index.entity_type_map.get(canonical, "person")
                result = MatchResult(
                    canonical=canonical,
                    confidence=TITLE_PERSON_CONFIDENCE,
                    strategy="title_person",
                    entity_kind="person",
                    entity_type=entity_type,
                )
                return (result, 1)

        # Evidence-scaled scoring (Req 3) applies to these surname fallbacks
        # too when enabled; inert when the flag is off (Req 12.9).
        evidence = _resolve_evidence_params(
            gate_context if gate_context is not None else GateContext.inert()
        )
        phonetic_match = match_phonetic(surname, index, evidence=evidence)
        if (
            phonetic_match
            and phonetic_match.entity_kind == "person"
            and phonetic_match.confidence >= threshold
        ):
            return (phonetic_match, 1)

        fuzzy_match = match_fuzzy(surname, index, snapshot, evidence=evidence)
        if (
            fuzzy_match
            and fuzzy_match.entity_kind == "person"
            and fuzzy_match.confidence >= threshold
        ):
            return (fuzzy_match, 1)

    return None


def _apply_merged_punctuated_word(
    merged: dict,
    corrected_text: str,
    source_words: list[dict],
    gate_context: GateContext,
) -> None:
    """Set (or omit) the merged Word's ``punctuated_word`` in place (Req 6.7, 6.9).

    Gated behind a non-inert :class:`~app.correction.gates.GateContext` so the
    flag-off path is byte-for-byte Baseline (Req 12.9): with every flag off the
    ``merged`` dict keeps the ``punctuated_word`` it inherited from the first
    source Word via ``dict(first_w)``, exactly as today. When any flag is on the
    field is rewritten to the corrected text plus the last merged Word's
    terminal ``.?!`` (Req 6.7), or removed when no merged Word carried a
    ``punctuated_word`` string (Req 6.9).
    """
    if gate_context.is_inert:
        return
    new_value = merged_punctuated_word(corrected_text, source_words)
    if new_value is None:
        merged.pop("punctuated_word", None)
    else:
        merged["punctuated_word"] = new_value


def _apply_replaced_punctuated_word(
    corrected_w: dict,
    corrected_text: str,
    source_word: dict,
    gate_context: GateContext,
) -> None:
    """Set (or omit) a replaced Word's ``punctuated_word`` in place (Req 6.8, 6.9).

    Gated behind a non-inert :class:`~app.correction.gates.GateContext` so the
    flag-off path is byte-for-byte Baseline (Req 12.9): with every flag off the
    ``corrected_w`` dict keeps the ``punctuated_word`` it copied from the source
    Word via ``dict(w)``, exactly as today. When any flag is on the field is
    rewritten to the corrected text plus the source Word's prior terminal
    ``.?!`` (Req 6.8), or removed when the source Word carried no
    ``punctuated_word`` string (Req 6.9).
    """
    if gate_context.is_inert:
        return
    new_value = replaced_punctuated_word(corrected_text, source_word)
    if new_value is None:
        corrected_w.pop("punctuated_word", None)
    else:
        corrected_w["punctuated_word"] = new_value


def correct_words(
    words: list[dict],
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    *,
    word_accept_threshold: float = 0.90,
    min_confidence: float = 0.75,
    fuzzy_score_cutoff: float = 0.70,
    min_candidate_length: int = MIN_CANDIDATE_LENGTH,
    gate_context: GateContext | None = None,
    tally: GateTally | None = None,
) -> WordCorrectionResult:
    """Correct all entity references in a transcript word list.

    Ports the word-level n-gram joining loop from the JavaScript
    ``formatTranscriptionResponse`` function. Scans windows of 3→2→1
    tokens at each position.

    Four guards:
      a. Word-stopword skip: reject windows containing a word-stopword
      b. Word accept threshold: only accept matches >= threshold (0.90)
      c. Whole-phrase equality: identity match records entity but no correction
      d. Single-token expansion: for n=1, only accept parties or corrections
         whose canonical has at most 2 whitespace tokens

    Parameters
    ----------
    words : list[dict]
        Word dicts (from Pydantic model_dump with by_alias=True), each
        having at minimum ``word``, ``start``, ``end``, ``confidence``,
        plus any extra provider fields.
    index : MatchIndex
        The derived lookup structures from the Dataset_Cache.
    snapshot : DatasetSnapshot
        The immutable dataset snapshot (block lists, stopwords, etc.).
    word_accept_threshold : float
        Minimum confidence to accept a match at word level (default 0.90).
    min_confidence : float
        Minimum confidence for title-person matching (default 0.75).
    fuzzy_score_cutoff : float
        Minimum score cutoff passed to ``match_fuzzy`` (default 0.70).
    min_candidate_length : int
        Minimum input length for full strategy chain (default 4).
    gate_context : GateContext | None
        The per-request gate inputs (task 1.4), forwarded to ``correct_single``
        and the title-person helper. ``None`` is normalised to an inert
        context so the word-level path is unchanged from Baseline (Req 12.9).
        Span_Confidence is derived directly from the covered Word dicts
        (Req 1.3-1.5) and passed to ``correct_single`` for the Confidence_Gate.

    Returns
    -------
    WordCorrectionResult
        The corrected words list (may be shorter due to joining),
        the corrections list, and entities_found.
    """
    # Normalise an absent context to an inert one (Baseline path, Req 12.9).
    if gate_context is None:
        gate_context = GateContext.inert()

    if not words:
        return WordCorrectionResult(words=[], corrections=[], entities_found=[])

    output: list[dict] = []
    corrections: list[tuple[str, str, str, float, str, str]] = []
    entities_found: list[tuple[str, str, str]] = []

    i = 0
    while i < len(words):
        w = words[i]
        current_word = w.get("word", "") or ""

        # --- Title-person branch ---
        if is_title(current_word, snapshot):
            title_result = _match_title_person_words(
                words,
                i,
                index,
                snapshot,
                min_confidence,
                fuzzy_score_cutoff=fuzzy_score_cutoff,
                min_candidate_length=min_candidate_length,
                gate_context=gate_context,
                tally=tally,
            )
            if title_result:
                match, name_count = title_result

                # Push the title word unchanged
                output.append(dict(w))

                # Determine the corrected name
                corrected_name = match.canonical
                # Double-title guard
                title_lower = current_word.lower()
                canonical_first = (
                    corrected_name.split()[0].lower()
                    if corrected_name.split()
                    else ""
                )
                if title_lower == canonical_first:
                    parts = corrected_name.split(None, 1)
                    corrected_name = parts[1] if len(parts) > 1 else corrected_name

                # Build the merged name word
                name_words = words[i + 1: i + 1 + name_count]
                original_phrase = " ".join(
                    nw.get("word", "") for nw in name_words
                )

                entity_kind = match.entity_kind or "person"
                entity_type = match.entity_type or "person"
                entities_found.append(
                    (match.canonical, entity_kind, entity_type)
                )

                is_identity = corrected_name.lower() == original_phrase.lower()

                if is_identity:
                    # Push name words unchanged
                    for nw in name_words:
                        output.append(dict(nw))
                else:
                    # Merge into one word
                    first_name = name_words[0]
                    last_name = name_words[-1]
                    merged = dict(first_name)
                    merged["word"] = corrected_name
                    merged["end"] = last_name.get("end", first_name.get("end"))
                    merged["locationCorrected"] = True
                    merged["entityKind"] = entity_kind
                    merged["entityType"] = entity_type
                    _apply_merged_punctuated_word(
                        merged, corrected_name, name_words, gate_context
                    )
                    output.append(merged)

                    corrections.append((
                        original_phrase,
                        corrected_name,
                        match.strategy,
                        match.confidence,
                        entity_kind,
                        entity_type,
                    ))

                i += 1 + name_count
                continue

            # No person match — push title as-is
            output.append(dict(w))
            i += 1
            continue

        # --- Word-stopword skip ---
        if is_word_stopword(current_word, snapshot):
            output.append(dict(w))
            i += 1
            continue

        # --- Minimum token length guard ---
        if len(current_word) < _WORD_MIN_TOKEN_LENGTH:
            output.append(dict(w))
            i += 1
            continue

        # --- Windows 3→2→1 ---
        matched = False

        for n in (3, 2, 1):
            if i + n > len(words):
                continue

            window = words[i: i + n]
            window_words = [wd.get("word", "") or "" for wd in window]

            # Guard (a): word-stopword skip for any token in window
            if n > 1 and any(
                is_word_stopword(ww, snapshot) for ww in window_words
            ):
                continue

            phrase = " ".join(window_words)

            # Span_Confidence for this window derives directly from its covered
            # Word dicts (Req 1.3-1.5); Unknown when none carry a confidence.
            span_conf = span_confidence(window)

            # Try correction via correct_single
            match = correct_single(
                phrase,
                n,
                index,
                snapshot,
                fuzzy_score_cutoff=fuzzy_score_cutoff,
                min_candidate_length=min_candidate_length,
                gate_context=gate_context,
                span_conf=span_conf,
                tally=tally,
                words=words,
                span_start_index=i,
                acceptance_threshold=word_accept_threshold,
            )

            # For n > 1: also try joined (fused) match
            if match is None and n > 1:
                joined_text = "".join(ww.lower() for ww in window_words)
                joined_stripped = re.sub(r"[\s\-']", "", joined_text)
                canonical = index.fused_map.get(joined_stripped)
                if canonical:
                    ek = index.entity_kind_map.get(canonical, "location")
                    et = index.entity_type_map.get(canonical, "supplementary")
                    match = MatchResult(
                        canonical=canonical,
                        confidence=JOINED_CONFIDENCE,
                        strategy="joined",
                        entity_kind=ek,
                        entity_type=et,
                    )

            if match is None:
                continue

            # Apply party display heuristic
            match = apply_party_display(match, phrase, index)

            # Guard (b): word accept threshold
            if match.confidence < word_accept_threshold:
                continue

            # Guard (c): whole-phrase equality (identity match)
            # For n > 1: if the match canonical equals the joined phrase,
            # it's an identity match — record entity but don't correct.
            # For n = 1: skip identity check (JS behaviour — case normalization
            # like "ndc" → "NDC" IS treated as a correction)
            if n > 1 and match.canonical.lower() == phrase.lower():
                entities_found.append(
                    (match.canonical, match.entity_kind, match.entity_type)
                )
                # For identity matches, push words unchanged
                for wd in window:
                    output.append(dict(wd))
                i += n
                matched = True
                break

            # For n=1, check true identity (exact case match means no correction)
            if n == 1 and match.canonical == current_word:
                entities_found.append(
                    (match.canonical, match.entity_kind, match.entity_type)
                )
                output.append(dict(w))
                i += 1
                matched = True
                break

            # Guard (d): single-token expansion guard
            if n == 1:
                is_party = match.entity_kind == "party"
                canonical_token_count = len(match.canonical.split())
                if not is_party and canonical_token_count > 2:
                    # Reject: single word expanding to > 2 tokens
                    continue

            # --- Accept the match ---
            entity_kind = match.entity_kind or "location"
            entity_type = match.entity_type or "supplementary"

            if n > 1:
                # Merge multiple words into one
                first_w = window[0]
                last_w = window[-1]
                merged = dict(first_w)
                merged["word"] = match.canonical
                merged["end"] = last_w.get("end", first_w.get("end"))
                merged["locationCorrected"] = True
                merged["entityKind"] = entity_kind
                merged["entityType"] = entity_type
                _apply_merged_punctuated_word(
                    merged, match.canonical, window, gate_context
                )
                output.append(merged)
            else:
                # Update single word in place
                corrected_w = dict(w)
                corrected_w["word"] = match.canonical
                corrected_w["locationCorrected"] = True
                corrected_w["entityKind"] = entity_kind
                corrected_w["entityType"] = entity_type
                _apply_replaced_punctuated_word(
                    corrected_w, match.canonical, w, gate_context
                )
                output.append(corrected_w)

            original_phrase = phrase
            corrections.append((
                original_phrase,
                match.canonical,
                match.strategy,
                match.confidence,
                entity_kind,
                entity_type,
            ))
            entities_found.append(
                (match.canonical, entity_kind, entity_type)
            )

            i += n
            matched = True
            break

        if not matched:
            # No match in any window — pass through unchanged
            output.append(dict(w))
            i += 1

    return WordCorrectionResult(
        words=output,
        corrections=corrections,
        entities_found=entities_found,
    )
