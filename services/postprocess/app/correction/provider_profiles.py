"""Per-provider gate profiles for the Correction_Engine (spec task 2.12).

This module will host the frozen per-provider gate profile that calibrates the
precision gates differently for the ``deepgram``, ``khaya``, and ``hybrid`` ASR
providers (``CorrectionOptions.provider``). Task 2.12.2 populates the profile
model and its ``Settings`` resolution; task 2.12.3 threads the resolved profile
through ``GateContext``/``GateConfig`` and branches gate evaluation on it. This
task (2.12.1) creates the module and records the decision that drives it — no
profile model and no behaviour change yet.

Decision (task 2.12.1): Unknown-confidence Lexicon_Gate for ``khaya``/``hybrid``
================================================================================
Mirrors the ``title_person`` classification decision recorded in the
``app/correction/strategies.py`` module docstring (task 1.3): state the open
item, make one reasoned decision, and record the rationale, the gates it affects,
and the requirement conflict — rather than silently implementing behaviour that
contradicts the written requirement. The full record lives at
``docs/provider-gating-decision.md``; this docstring is the in-code copy.

The open item
-------------
``CorrectionOptions.provider`` (``Literal["deepgram", "khaya", "hybrid"]``,
default ``"deepgram"``) exists on ``app/models/request.py`` and is read nowhere
in the service. The three providers differ structurally in what they supply:

* ``deepgram`` — a word list with per-word timestamps and per-word ``confidence``;
  Span_Confidence is a real number for most Spans.
* ``khaya`` — **text only**: no word list, no per-word timestamps, no per-word
  confidence. Span_Confidence is therefore **Unknown** for every Span (Req 1.5;
  Req 1.10 for the empty-Words-list case).
* ``hybrid`` — mixes the two; Khaya-sourced Spans are likewise Unknown.

For an Unknown-confidence Span the Confidence_Gate (Req 1.2, 1.6) is inert —
``confidence_gate_blocks_approximate`` returns ``False`` — which is intended
(Assumption 1). But the Lexicon_Gate fires **unconditionally**: Req 2.6 rejects a
lexicon-member, non-alias, single-token Span for every Approximate_Strategy
member *whenever* Span_Confidence is Unknown, and the Req 2.7 override (known
confidence below the Lexicon_Override_Threshold) is never reachable when there is
no confidence value at all. So Deepgram-calibrated thresholds are applied, in
their most aggressive form, to the Ghanaian-language provider exactly where
mangled Ghanaian names most need repair.

The decision
------------
**No — a ``khaya``/``hybrid`` transcript with Unknown Span_Confidence should NOT
trigger the unconditional Lexicon_Gate rejection of Req 2.6.** The
Unknown-confidence path is to be **calibrated per provider**: a provider-specific
Unknown-confidence policy carried on the (forthcoming) gate profile replaces the
blanket rejection for ``khaya``/``hybrid``. The ``deepgram`` provider keeps the
current Req 2.6 behaviour exactly, the whole mechanism sits behind a boolean flag
defaulting to disabled, and the ``deepgram`` profile reproduces today's defaults
so the flag-off path stays baseline-equivalent (Req 12.9).

Rationale (condensed; see the docs file for the full argument):

1. Khaya is the Ghanaian-language ASR. On that path "Unknown confidence" is the
   structural absence of a signal the provider never emits, not evidence a word
   was heard clearly; suppressing approximate matching there inverts the spec's
   intent.
2. The Req 2.7 override is structurally unreachable on Khaya/hybrid (never a known
   confidence), so the spec's safety valve for borderline words cannot fire for an
   entire provider. A provider-calibrated policy restores an equivalent valve.
3. The English_Lexicon overlaps Ghanaian proper nouns; a *mangled* name reaches
   only the Approximate strategies, which Req 2.6 unconditionally blocks under
   Unknown confidence — bounded by the confidence signal on Deepgram, unbounded on
   Khaya.
4. Baseline safety is preserved: ``deepgram`` unchanged, mechanism flag-gated off.

Gates affected
--------------
* **Lexicon_Gate** (Req 2.5–2.8, engine) — primary: the Unknown-confidence branch
  becomes provider-calibrated for ``khaya``/``hybrid``; unchanged for ``deepgram``.
* **Confidence_Gate** (Req 1.2, 1.6, ``confidence.py``) — already inert under
  Unknown confidence; unchanged, but the ``provider`` value rides the same
  ``GateContext`` so task 2.12.3 branches both gates from one place.
* Deterministic strategies (``exact``/``fused``/``joined``/``initials``/
  ``title_person``) — never gated (Req 2.9, 2.14), unaffected.
* ``context``/``sitting_scope``/``llm_veto`` — unaffected.

Requirement conflict — MUST be amended before implementation
------------------------------------------------------------
This decision **contradicts Requirement 2.6 as written** (which mandates the
unconditional Unknown-confidence rejection with no provider qualification) and
**Assumption 1** (which states the Lexicon_Gate "applies unconditionally" when a
provider omits confidence). Both MUST be amended **before** task 2.12.3 branches
gate evaluation on provider:

* Req 2.6 — qualify the unconditional Unknown-confidence rejection as the
  ``deepgram`` behaviour and add a criterion permitting a provider-calibrated
  Unknown-confidence policy for providers that structurally cannot supply per-word
  confidence (``khaya``, ``hybrid``).
* Assumption 1 — replace "applies unconditionally" with the provider-calibrated
  policy and cross-reference the new Req 2.6 criterion.

Until those amendments land, the mechanism stays flag-gated off and the
``deepgram`` profile reproduces Req 2.6 exactly. This module does **not**
implement the behaviour change; tasks 2.12.2/2.12.3 do.
"""

from __future__ import annotations

from dataclasses import dataclass

# Scaffolding only. Task 2.12.2 adds the frozen per-provider gate profile model
# (a Pydantic v2 model or frozen dataclass) and its ``Settings`` resolution;
# task 2.12.3 threads it through ``GateContext``/``GateConfig``. Keeping this
# module import-safe and side-effect-free lets those tasks build on it without a
# behaviour change here.

#: The provider values a gate profile is resolved for, matching
#: ``CorrectionOptions.provider`` (``app/models/request.py``). Declared here so
#: task 2.12.2 has a single source of truth for the set of profiles to build.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("deepgram", "khaya", "hybrid")


# ---------------------------------------------------------------------------
# Per-provider gate profile model (task 2.12.2)
# ---------------------------------------------------------------------------

#: Baseline (task 1.1) gate-parameter defaults. The ``deepgram`` profile MUST
#: reproduce these exactly so the current production path is unchanged, and the
#: flag-off configuration resolves this profile for every provider (Req 12.9).
_DEEPGRAM_DEFAULTS: dict[str, float] = {
    "high_confidence_threshold": 0.90,
    "lexicon_override_threshold": 0.60,
    "min_phonetic_similarity": 0.60,
    "max_relative_distance": 0.25,
}


@dataclass(frozen=True)
class ProviderGateProfile:
    """Immutable per-provider snapshot of the gate parameters that differ by
    ASR provider (``CorrectionOptions.provider``).

    Carries only the parameters task 2.12 calibrates per provider: the four
    fractional thresholds plus the Unknown-confidence Lexicon_Gate policy
    decided in task 2.12.1. Task 2.12.3 threads the resolved profile through
    ``GateContext``/``GateConfig`` and branches gate evaluation on it; this
    task only defines and resolves the profiles.

    Being frozen, one instance is safe to reuse across requests handled against
    the same :class:`~app.config.Settings`. The defaults reproduce the
    ``deepgram`` (task 1.1) values so a profile built with no arguments is
    Baseline-equivalent.
    """

    # --- Gate parameters that differ by provider (Req 12.1, 12.3) ---
    high_confidence_threshold: float = 0.90
    lexicon_override_threshold: float = 0.60
    min_phonetic_similarity: float = 0.60
    max_relative_distance: float = 0.25

    # --- Unknown-confidence Lexicon_Gate policy (task 2.12.1 decision) ---
    #: Whether an Unknown-confidence, lexicon-member, non-alias, single-token
    #: Span is rejected unconditionally for the Approximate_Strategy members
    #: (Req 2.6 as written). ``deepgram`` keeps this ``True``; ``khaya`` and
    #: ``hybrid`` set it ``False`` so a provider that structurally cannot supply
    #: confidence is not blanket-blocked. The value only takes effect when the
    #: profile flag is enabled (task 2.12.3 wires the gate branch).
    lexicon_gate_reject_unknown: bool = True


#: The fractional gate parameters carried on a profile, paired with the
#: SCREAMING_SNAKE_CASE prefix suffix used to build each per-provider env var
#: and the task 1.1 range for clamping. Every parameter here uses the closed
#: interval [0.0, 1.0] (Req 12.11).
PROFILE_FLOAT_PARAMS: tuple[str, ...] = (
    "high_confidence_threshold",
    "lexicon_override_threshold",
    "min_phonetic_similarity",
    "max_relative_distance",
)

#: Range for every fractional profile parameter (Req 12.11).
PROFILE_FLOAT_RANGE: tuple[float, float] = (0.0, 1.0)

#: The Unknown-confidence Lexicon_Gate policy per provider (task 2.12.1). This
#: is a calibrated constant, not an env-driven tunable: ``deepgram`` keeps the
#: Req 2.6 behaviour, ``khaya``/``hybrid`` opt out. It only takes effect when
#: the profile flag is enabled.
_LEXICON_GATE_REJECT_UNKNOWN: dict[str, bool] = {
    "deepgram": True,
    "khaya": False,
    "hybrid": False,
}


def provider_env_var(provider: str, param: str) -> str:
    """Return the SCREAMING_SNAKE_CASE env var name for *provider*/*param*.

    e.g. ``provider_env_var("khaya", "high_confidence_threshold")`` ->
    ``"PROVIDER_KHAYA_HIGH_CONFIDENCE_THRESHOLD"``.
    """
    return f"PROVIDER_{provider.upper()}_{param.upper()}"


def default_profile(provider: str) -> ProviderGateProfile:
    """Return the all-defaults profile for *provider* (no env overrides).

    The ``deepgram`` profile reproduces the task 1.1 defaults exactly; every
    provider shares those four fractional defaults and differs only in the
    Unknown-confidence Lexicon_Gate policy.
    """
    return ProviderGateProfile(
        high_confidence_threshold=_DEEPGRAM_DEFAULTS["high_confidence_threshold"],
        lexicon_override_threshold=_DEEPGRAM_DEFAULTS["lexicon_override_threshold"],
        min_phonetic_similarity=_DEEPGRAM_DEFAULTS["min_phonetic_similarity"],
        max_relative_distance=_DEEPGRAM_DEFAULTS["max_relative_distance"],
        lexicon_gate_reject_unknown=_LEXICON_GATE_REJECT_UNKNOWN[provider],
    )
