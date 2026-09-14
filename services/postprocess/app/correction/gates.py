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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.correction.provider_profiles import ProviderGateProfile, default_profile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings


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
