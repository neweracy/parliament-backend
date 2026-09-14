"""GateContext / GateConfig plumbing for the Correction_Engine precision gates.

This module defines the two structured arguments the engine functions
(``correct_single``, ``correct_text``, ``correct_words``) accept so that gate
inputs travel through one object rather than through ad-hoc signature
widening as each gate (tasks 2.x, 4.x) is layered on:

* :class:`GateConfig` — an immutable snapshot of the resolved gating settings
  (the nine tunables and five feature flags of Req 12) taken once at the
  pipeline boundary. It carries no per-request state, so one instance is safe
  to share across requests handled against the same :class:`~app.config.Settings`.

* :class:`GateContext` — the per-request bundle wrapping a :class:`GateConfig`
  together with the request-scoped gate inputs later tasks populate: the
  per-Span Span_Confidence hook, the process-global English_Lexicon handle,
  the sitting-scope member set, and (task 2.12.3) the request's ``provider``
  value together with the :class:`~app.correction.provider_profiles.ProviderGateProfile`
  resolved for it. The engine reads the four provider-calibrated thresholds
  (High_Confidence_Threshold, Lexicon_Override_Threshold, Min_Phonetic_Similarity,
  Max_Relative_Distance) and the Unknown-confidence Lexicon_Gate policy from that
  profile, so gate evaluation branches on provider without widening any
  individual strategy signature.

Baseline equivalence (Req 12.9)
-------------------------------
When every feature flag introduced by this spec is disabled, the context is
**inert**: :meth:`GateContext.is_inert` is ``True`` and every engine function
that receives it takes exactly the path it would take with no context at all.
The engine treats ``gate_context=None`` and an inert ``GateContext``
identically, so the flag-off path stays byte-for-byte Baseline (Property 12).

Later tasks read the resolved thresholds and the request-scoped inputs from
the :class:`GateContext`; this task only establishes the type and the wiring.
"""

from __future__ import annotations

import structlog

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.correction.provider_profiles import ProviderGateProfile, default_profile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

logger = structlog.get_logger("gates")


# ---------------------------------------------------------------------------
# Gate-decision observability (Req 13) — GateDecision / GateTally
# ---------------------------------------------------------------------------

# The thirteen enumerated gate values (Req 13.2). Every gate-rejection metric
# datum and every debug log event carries exactly one of these as its ``gate``
# dimension/field; ``emit_gate_rejections`` accepts only these keys. The order
# here is the evaluation order used for first-gate attribution of a
# multiply-rejected Span (Req 13.9): a Span is counted once, attributed to the
# first gate in this order that rejected it. ``asr_confidence`` (the
# Confidence_Gate) precedes ``lexicon``, which precedes ``block_list``, which
# precedes the phonetic/fuzzy/component sub-gates, then the Phase 2 gates
# (``context``, ``sitting_scope``, ``llm_veto``) which are not yet wired.
GATE_VALUES: tuple[str, ...] = (
    "asr_confidence",
    "lexicon",
    "block_list",
    "phonetic_key",
    "phonetic_similarity",
    "key_fanout",
    "absolute_distance",
    "relative_distance",
    "candidate_bound",
    "component_ambiguity",
    "context",
    "sitting_scope",
    "llm_veto",
)

# Rank of each gate in evaluation order, for first-gate attribution (Req 13.9).
_GATE_RANK: dict[str, int] = {gate: rank for rank, gate in enumerate(GATE_VALUES)}


@dataclass(frozen=True)
class GateDecision:
    """One gate rejection attributed to a single Span (Req 13.5).

    Kept entirely internal to the engine/pipeline — it never enters the
    Correction_Response contract (design.md section, "MatchResult / gate
    result"). It carries the attributed ``gate`` value, the Span text, and the
    Span_Confidence (``None`` for Unknown) so the pipeline can emit the
    debug-level per-Span rejection log (Req 13.5) and aggregate the per-gate
    counts for the metric (Req 13.1).
    """

    gate: str
    span_text: str
    span_confidence: float | None


class GateTally:
    """Mutable per-request collector of gate decisions (Req 13.1, 13.4, 13.9).

    One instance is created per request at the pipeline boundary and threaded
    into the engine functions, which record a rejection (via
    :meth:`record_rejection`) whenever a gate excludes a Span from approximate
    matching, and mark a Span as having had an approximate strategy evaluated
    (via :meth:`record_approx_evaluated`).

    Multiply-rejected Spans are counted once, attributed to the first gate in
    evaluation order (Req 13.9). Because a Span may be evaluated more than once
    across overlapping n-gram windows, a rejection is de-duplicated by its
    ``span_text`` so the same window rejection is not double counted; when the
    same Span text is rejected by two different gates the earlier gate in
    evaluation order wins.

    All bookkeeping is plain in-memory state — nothing here raises for the
    caller, matching the "emission failure never surfaces" requirement (Req
    13.11), which the pipeline enforces around the actual emit calls.
    """

    __slots__ = ("_rejections", "_approx_spans", "decisions")

    def __init__(self) -> None:
        # Attributed gate per rejected Span text (first gate in eval order).
        self._rejections: dict[str, str] = {}
        # Distinct Span texts for which an approximate strategy was evaluated.
        self._approx_spans: set[str] = set()
        # Ordered list of the attributed decisions, one per rejected Span, in
        # first-observation order — used to emit the debug logs (Req 13.5).
        self.decisions: list[GateDecision] = []

    def record_rejection(
        self, gate: str, span_text: str, span_conf: float | None
    ) -> None:
        """Record that *gate* rejected *span_text* (first-gate-wins, Req 13.9).

        Only the thirteen enumerated gate values are accepted; an unknown gate
        name is ignored so a caller mistake never corrupts the tally. A Span
        already attributed to an earlier gate in evaluation order keeps that
        attribution; a later observation with an earlier gate replaces it.
        """
        if gate not in _GATE_RANK:
            return
        existing = self._rejections.get(span_text)
        if existing is not None and _GATE_RANK[existing] <= _GATE_RANK[gate]:
            return
        self._rejections[span_text] = gate
        # Rebuild the decision for this span so ``decisions`` reflects the
        # winning attribution. Drop any prior decision for the same span text.
        self.decisions = [d for d in self.decisions if d.span_text != span_text]
        self.decisions.append(
            GateDecision(gate=gate, span_text=span_text, span_confidence=span_conf)
        )

    def record_approx_evaluated(self, span_text: str) -> None:
        """Record an Approximate_Strategy was evaluated for a Span (Req 13.4)."""
        self._approx_spans.add(span_text)

    def counts(self) -> dict[str, int]:
        """Per-gate rejection counts, one entry per gate with count > 0 (Req 13.1)."""
        counts: dict[str, int] = {}
        for gate in self._rejections.values():
            counts[gate] = counts.get(gate, 0) + 1
        return counts

    def total_rejections(self) -> int:
        """Total gate rejections across every gate value (Req 13.6)."""
        return len(self._rejections)

    def approx_spans_evaluated(self) -> int:
        """Count of Spans for which an Approximate_Strategy was evaluated (Req 13.4)."""
        return len(self._approx_spans)


# ---------------------------------------------------------------------------
# GateConfig — immutable resolved gating settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateConfig:
    """Immutable snapshot of the resolved correction-precision gating settings.

    Holds the nine tunables and five feature flags of Requirement 12, resolved
    once from :class:`~app.config.Settings` at the pipeline boundary and held
    unchanged for the request. Being frozen, one instance is safe to reuse
    across requests that share the same ``Settings``.

    The tunable defaults mirror ``Settings`` (Req 12.3) and the flag defaults
    mirror ``Settings`` (Req 12.4, 12.5) so a :class:`GateConfig` built with no
    arguments is a valid all-defaults configuration. When every flag is
    disabled the configuration is Baseline-equivalent (Req 12.9); see
    :attr:`all_flags_disabled`.
    """

    # --- Tunables (Req 12.1, 12.3) ---
    high_confidence_threshold: float = 0.90
    lexicon_override_threshold: float = 0.60
    max_relative_distance: float = 0.25
    min_phonetic_key_length: int = 4
    min_phonetic_similarity: float = 0.60
    component_match_min_length: int = 6
    max_candidates_per_span: int = 200
    context_window_words: int = 40
    out_of_scope_penalty: float = 0.10

    # --- Feature flags (Req 12.2, 12.4, 12.5) ---
    lexicon_gate_enabled: bool = False
    evidence_confidence_enabled: bool = False
    context_gate_enabled: bool = False
    sitting_scope_enabled: bool = False
    llm_veto_enabled: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> GateConfig:
        """Resolve a :class:`GateConfig` from a :class:`~app.config.Settings`.

        Copies every gating tunable and flag from *settings* as already
        resolved and range-clamped at startup (``clamp_ranges``). Reads each
        flag independently — no flag is derived from another (Req 12.2).
        """
        return cls(
            high_confidence_threshold=settings.high_confidence_threshold,
            lexicon_override_threshold=settings.lexicon_override_threshold,
            max_relative_distance=settings.max_relative_distance,
            min_phonetic_key_length=settings.min_phonetic_key_length,
            min_phonetic_similarity=settings.min_phonetic_similarity,
            component_match_min_length=settings.component_match_min_length,
            max_candidates_per_span=settings.max_candidates_per_span,
            context_window_words=settings.context_window_words,
            out_of_scope_penalty=settings.out_of_scope_penalty,
            lexicon_gate_enabled=settings.lexicon_gate_enabled,
            evidence_confidence_enabled=settings.evidence_confidence_enabled,
            context_gate_enabled=settings.context_gate_enabled,
            sitting_scope_enabled=settings.sitting_scope_enabled,
            llm_veto_enabled=settings.llm_veto_enabled,
        )

    @property
    def all_flags_disabled(self) -> bool:
        """True when every feature flag introduced by this spec is disabled.

        The Baseline configuration (Req 12.9): with this true, the gates add
        no behaviour and the engine reproduces Baseline output.
        """
        return not (
            self.lexicon_gate_enabled
            or self.evidence_confidence_enabled
            or self.context_gate_enabled
            or self.sitting_scope_enabled
            or self.llm_veto_enabled
        )


# A Span_Confidence hook maps the covered Word dicts of a Span to its
# Span_Confidence — a float in [0.0, 1.0] or ``None`` for Unknown (Req 1.3-1.5).
# Task 2.1 supplies the real implementation from ``app/correction/confidence.py``;
# until then the default hook returns ``None`` (Unknown) for every Span, which
# keeps the confidence-dependent gates inert.
SpanConfidenceHook = Callable[[Sequence[dict[str, Any]]], float | None]


def _unknown_span_confidence(_covered_words: Sequence[dict[str, Any]]) -> float | None:
    """Default Span_Confidence hook: every Span is Unknown (Req 1.5).

    Used until task 2.1 wires the real ``span_confidence`` implementation.
    Returning ``None`` (Unknown) keeps the Confidence_Gate and the
    confidence-dependent branch of the Lexicon_Gate inert.
    """
    return None


# ---------------------------------------------------------------------------
# GateContext — per-request gate inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateContext:
    """Per-request bundle of gate inputs threaded through the engine.

    Wraps an immutable :class:`GateConfig` together with the request-scoped
    inputs the gates consume:

    * :attr:`span_confidence_hook` — computes Span_Confidence from a Span's
      covered Word dicts (Req 1). Defaults to an Unknown-returning hook so the
      Confidence_Gate is inert until task 2.1 supplies the real hook.
    * :attr:`lexicon` — the process-global English_Lexicon handle (Req 2).
      ``None`` until task 2.3 loads it; a ``None`` lexicon leaves the
      Lexicon_Gate inactive (Req 2.13).
    * :attr:`sitting_scope` — the resolved sitting-scope member set (Req 8),
      a frozenset of canonical names. ``None`` when no scope applies, which
      leaves the Sitting_Scope preference inert (Req 8.2).
    * :attr:`provider` — the request's ``CorrectionOptions.provider`` value
      (task 2.12.3), defaulting to ``"deepgram"`` so a request that omits it
      behaves exactly as today.
    * :attr:`profile` — the :class:`~app.correction.provider_profiles.ProviderGateProfile`
      resolved for :attr:`provider` at the pipeline boundary (task 2.12.2's
      ``provider_profiles``). The engine reads the four provider-calibrated
      thresholds and the Unknown-confidence Lexicon_Gate policy from here rather
      than from :class:`GateConfig`. Defaults to the ``deepgram`` profile — the
      task 1.1 values with the Req 2.6 reject policy — so an all-defaults
      context is Baseline-equivalent (Req 12.9).

    The fields for not-yet-implemented signals are typed permissively (``Any``
    for the lexicon so this task need not depend on task 2.3's module) and
    default to inert values, so constructing a :class:`GateContext` from a
    Baseline configuration produces an inert context.
    """

    config: GateConfig
    span_confidence_hook: SpanConfidenceHook = _unknown_span_confidence
    # ``EnglishLexicon`` handle (task 2.3). Typed ``Any`` to avoid a dependency
    # on a module that does not exist yet; ``None`` leaves the gate inactive.
    lexicon: Any | None = None
    # Resolved sitting-scope member set (task 4.5); ``None`` when no scope.
    sitting_scope: frozenset[str] | None = field(default=None)
    # The request's ASR provider (``CorrectionOptions.provider``); default
    # ``"deepgram"`` so an omitted value behaves as today (task 2.12.3).
    provider: str = "deepgram"
    # The gate profile resolved for ``provider`` (task 2.12.2). The engine reads
    # the four calibrated thresholds and the Unknown-confidence Lexicon_Gate
    # policy from this profile. Defaults to the ``deepgram`` profile so an
    # all-defaults context reproduces the task 1.1 Baseline (Req 12.9).
    profile: ProviderGateProfile = field(default_factory=lambda: default_profile("deepgram"))

    @classmethod
    def inert(cls) -> GateContext:
        """Build an inert context carrying an all-defaults Baseline config.

        Equivalent to passing no context: every gate is disabled, the
        Span_Confidence hook returns Unknown, and no lexicon or sitting scope
        is present. Used by the engine when a caller supplies no context.
        """
        return cls(config=GateConfig())

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        span_confidence_hook: SpanConfidenceHook | None = None,
        lexicon: Any | None = None,
        sitting_scope: frozenset[str] | None = None,
        provider: str = "deepgram",
        profile: ProviderGateProfile | None = None,
    ) -> GateContext:
        """Build a :class:`GateContext` at the pipeline boundary from settings.

        Resolves the :class:`GateConfig` from *settings* and attaches the
        request-scoped inputs. Later tasks pass the real Span_Confidence hook
        (task 2.1), lexicon (task 2.3), and sitting scope (task 4.5); omitting
        one keeps the corresponding gate inert.

        The *provider* (``CorrectionOptions.provider``) and its resolved
        *profile* thread the per-provider gate calibration through the context
        (task 2.12.3). When *profile* is omitted the ``deepgram`` default is
        used, which reproduces the task 1.1 thresholds and the Req 2.6
        Unknown-confidence policy — so an omitted profile is Baseline-equivalent
        (Req 12.9). Callers resolve the profile from ``provider_profiles``
        (``app.config``), which returns the ``deepgram`` profile for every
        provider when ``provider_profiles_enabled`` is off.
        """
        return cls(
            config=GateConfig.from_settings(settings),
            span_confidence_hook=(
                span_confidence_hook
                if span_confidence_hook is not None
                else _unknown_span_confidence
            ),
            lexicon=lexicon,
            sitting_scope=sitting_scope,
            provider=provider,
            profile=profile if profile is not None else default_profile("deepgram"),
        )

    @property
    def is_inert(self) -> bool:
        """True when the context adds no gating behaviour (Req 12.9).

        Inert exactly when every feature flag is disabled. An inert context is
        indistinguishable from ``gate_context=None`` on the engine path, which
        is what keeps the flag-off output Baseline-equivalent (Property 12).
        """
        return self.config.all_flags_disabled
