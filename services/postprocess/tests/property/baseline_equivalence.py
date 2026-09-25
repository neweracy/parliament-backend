"""Reusable baseline-equivalence guardrail assertion (Property 12).

This module is the SHARED guardrail every later gate task (2.2, 2.5, 2.7, 2.9,
2.11, 2.12.6, and the Phase-1/Phase-2 checkpoints) re-runs to prove the
flag-off path stays Baseline-equivalent (Req 12.9). It is deliberately kept
separate from the ``test_*.py`` module that owns the hypothesis property
(``test_baseline_equivalence.py``) so any test module in this package -- or a
future gate's own test module -- can import and call
:func:`assert_baseline_equivalent` directly instead of duplicating the
comparison logic.

Baseline, here, means "every feature flag introduced by this spec disabled"
(:attr:`~app.correction.gates.GateConfig.all_flags_disabled`), which is
exactly the flag-off configuration Req 12.9 requires to reproduce the
pre-redesign engine output. The codebase carries no separate legacy/pre-redesign
code path (no ``_legacy_correct`` hook, no parallel module) -- the gates
themselves are written to be true no-ops when their flag is off (see
``app/correction/gates.py``: ``GateContext.is_inert`` and every gate call
site's "consulted only when the context is non-inert" guard). So the correct
Baseline reference implementation for this property is: **the same engine
functions invoked with no gate context at all** (``gate_context=None``, which
``correct_text``/``correct_words`` normalise to ``GateContext.inert()``)
against **the same engine functions invoked with an explicit, fully-disabled
``GateContext``** built from an all-flags-off ``GateConfig``. The two calls
must be indistinguishable -- that indistinguishability is the property.

Usage
-----
    from tests.property.baseline_equivalence import assert_baseline_equivalent

    def test_something_still_baseline_equivalent(property_index, property_snapshot):
        assert_baseline_equivalent(text, words, property_index, property_snapshot)

**Feature: correction-precision-gating, Property 12: Baseline equivalence
when all new flags are disabled**
"""

from __future__ import annotations

from dataclasses import dataclass

from app.correction.engine import correct_text, correct_words
from app.correction.gates import GateConfig, GateContext
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex

# The all-flags-disabled GateConfig -- every field left at its documented
# default flag value of False (GateConfig's own dataclass defaults already
# declare every flag False so this is the literal Baseline: no lexicon gate,
# no evidence-scaled confidence, no context gate, no sitting-scope
# preference, no LLM veto). Tunables are irrelevant when every flag reading
# them is off.
_BASELINE_GATE_CONFIG = GateConfig(
    lexicon_gate_enabled=False,
    evidence_confidence_enabled=False,
    context_gate_enabled=False,
    sitting_scope_enabled=False,
    llm_veto_enabled=False,
)

assert _BASELINE_GATE_CONFIG.all_flags_disabled, (
    "the Baseline GateConfig must have every new flag disabled"
)

# A fully materialised, non-inert-shaped GateContext wrapping the all-off
# config. Constructing it explicitly (rather than relying on gate_context=None)
# exercises the "explicit but inert" path through GateContext.is_inert, so the
# guardrail also proves those two Baseline-equivalent entry points agree.
_BASELINE_GATE_CONTEXT = GateContext(config=_BASELINE_GATE_CONFIG)

assert _BASELINE_GATE_CONTEXT.is_inert, (
    "a GateContext built from an all-flags-disabled GateConfig must be inert"
)


@dataclass(frozen=True, slots=True)
class BaselineDivergence:
    """One field-level divergence between the flag-off run and the Baseline."""

    field: str
    baseline: object
    flag_off: object


def _diff(field: str, baseline: object, flag_off: object) -> BaselineDivergence | None:
    if baseline != flag_off:
        return BaselineDivergence(field=field, baseline=baseline, flag_off=flag_off)
    return None


def compare_text_results(baseline_result, flag_off_result) -> list[BaselineDivergence]:
    """Compare two ``TextCorrectionResult``s for Baseline equivalence.

    Checks the corrected transcript text and, for the corrections list, the
    same order, ``Match_Strategy`` (``strategy``), and confidence per
    correction, plus the span/replacement fields so a divergence pinpoints
    exactly which correction moved.
    """
    divergences: list[BaselineDivergence] = []

    d = _diff("text", baseline_result.text, flag_off_result.text)
    if d:
        divergences.append(d)

    baseline_corrections = baseline_result.corrections
    flag_off_corrections = flag_off_result.corrections

    d = _diff("corrections.length", len(baseline_corrections), len(flag_off_corrections))
    if d:
        divergences.append(d)

    for i in range(min(len(baseline_corrections), len(flag_off_corrections))):
        b = baseline_corrections[i]
        f = flag_off_corrections[i]
        for attr in (
            "start_char",
            "end_char",
            "original",
            "replacement",
            "strategy",
            "confidence",
        ):
            d = _diff(f"corrections[{i}].{attr}", getattr(b, attr), getattr(f, attr))
            if d:
                divergences.append(d)

    return divergences


def compare_word_results(baseline_result, flag_off_result) -> list[BaselineDivergence]:
    """Compare two ``WordCorrectionResult``s for Baseline equivalence.

    Checks the Words list (order, text, ``start``/``end``, and the
    ``punctuated_word``/entity flags a correction may set) and the
    corrections list (same order, strategy, and confidence per correction).
    """
    divergences: list[BaselineDivergence] = []

    baseline_words = baseline_result.words
    flag_off_words = flag_off_result.words

    d = _diff("words.length", len(baseline_words), len(flag_off_words))
    if d:
        divergences.append(d)

    for i in range(min(len(baseline_words), len(flag_off_words))):
        b = baseline_words[i]
        f = flag_off_words[i]
        # Compare the full dict -- this covers word text, start/end,
        # confidence passthrough, punctuated_word, and any entity-flag fields
        # a correction sets, without hard-coding a field allowlist that could
        # silently drop a future field from the guardrail's coverage.
        d = _diff(f"words[{i}]", b, f)
        if d:
            divergences.append(d)

    baseline_corrections = baseline_result.corrections
    flag_off_corrections = flag_off_result.corrections

    d = _diff("corrections.length", len(baseline_corrections), len(flag_off_corrections))
    if d:
        divergences.append(d)

    for i in range(min(len(baseline_corrections), len(flag_off_corrections))):
        b = baseline_corrections[i]
        f = flag_off_corrections[i]
        # Each tuple is (original, corrected, strategy, confidence, kind, type).
        for pos, name in enumerate(
            ("original", "corrected", "strategy", "confidence", "kind", "type")
        ):
            d = _diff(f"corrections[{i}].{name}", b[pos], f[pos])
            if d:
                divergences.append(d)

    return divergences


def run_baseline_and_flag_off(
    text: str,
    words: list[dict],
    index: MatchIndex,
    snapshot: DatasetSnapshot,
):
    """Run the engine twice -- once as Baseline, once with an explicit all-off context.

    Returns a 4-tuple of
    ``(baseline_text_result, flag_off_text_result, baseline_word_result, flag_off_word_result)``.

    "Baseline" is ``gate_context=None`` (the engine's own documented Baseline
    normalisation to an inert context). "Flag-off" is an explicit
    :class:`~app.correction.gates.GateContext` built from an all-flags-disabled
    :class:`~app.correction.gates.GateConfig`. Both must be byte-for-byte
    equivalent (Req 12.9); this helper only runs them, it does not assert.
    """
    baseline_text = correct_text(text, index, snapshot, words=words, gate_context=None)
    flag_off_text = correct_text(
        text, index, snapshot, words=words, gate_context=_BASELINE_GATE_CONTEXT
    )

    # correct_words does not mutate its input list, but pass independent
    # copies defensively so neither run can observe the other's mutation.
    baseline_words = correct_words([dict(w) for w in words], index, snapshot, gate_context=None)
    flag_off_words = correct_words(
        [dict(w) for w in words], index, snapshot, gate_context=_BASELINE_GATE_CONTEXT
    )

    return baseline_text, flag_off_text, baseline_words, flag_off_words


def assert_baseline_equivalent(
    text: str,
    words: list[dict],
    index: MatchIndex,
    snapshot: DatasetSnapshot,
) -> None:
    """Assert the flag-off engine path is Baseline-equivalent for one input.

    Runs :func:`run_baseline_and_flag_off` and fails with a detailed diff
    report naming every divergent field if the flag-off run departs from
    Baseline on transcript text, the Words list, or the corrections list
    (order, ``Match_Strategy``, confidence) for either ``correct_text`` or
    ``correct_words``.

    This is the reusable guardrail every later gate task re-runs to prove
    Property 12 still holds (Req 12.9, 16.16).
    """
    baseline_text, flag_off_text, baseline_words, flag_off_words = run_baseline_and_flag_off(
        text, words, index, snapshot
    )

    divergences = compare_text_results(baseline_text, flag_off_text)
    divergences += compare_word_results(baseline_words, flag_off_words)

    if divergences:
        report = "\n".join(
            f"  field: {d.field}\n    baseline: {d.baseline!r}\n    flag_off:  {d.flag_off!r}"
            for d in divergences
        )
        raise AssertionError(
            "Baseline-equivalence guardrail (Property 12) failed -- the "
            "flag-off path diverged from Baseline:\n"
            f"{report}\n"
            f"input text: {text!r}\n"
            f"input words: {words!r}"
        )
