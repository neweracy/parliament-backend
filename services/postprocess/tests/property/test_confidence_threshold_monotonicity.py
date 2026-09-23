"""Property test for confidence-threshold monotonicity (Property 9).

**Feature: correction-precision-gating, Property 9: Raising the confidence
threshold never adds approximate corrections**

**Validates: Requirements 1.2, 16.8**

Direction verified against requirements.md / design.md / the implementation
before writing this test (see the module docstring of
``app.correction.confidence`` and the ``correct_single`` docstring in
``app.correction.engine`` for the code-level confirmation):

* Requirement 1.2: "WHEN the Correction_Engine evaluates a Span whose
  Span_Confidence is greater than or equal to the High_Confidence_Threshold
  ... THE Correction_Engine SHALL exclude every Approximate_Strategy member
  from evaluation of that Span." So a **higher** threshold means *fewer*
  Spans satisfy ``span_confidence >= threshold``, so *fewer* Spans are
  excluded from approximate evaluation, so the count of corrections
  reporting an Approximate_Strategy can only stay the same or *increase* as
  the threshold rises.
* design.md, Property 9 (verbatim): "the run configured with the second
  [strictly higher] value SHALL produce a count of corrections reporting an
  Approximate_Strategy no lower than the count produced by the run
  configured with the first [lower] value." This is the same direction as
  Req 1.2's mechanics, not the opposite -- there is no discrepancy between
  Req 1.2 / 16.8 and design.md's Property 9 text.
* ``app.correction.confidence.confidence_gate_blocks_approximate`` returns
  ``True`` (blocking approximate matching) iff
  ``span_conf is not None and span_conf >= high_threshold``. In
  ``correct_single`` (app/correction/engine.py), ``block_approximate`` gates
  the phonetic/fuzzy/component branches: when it is ``True`` the function
  returns ``None`` before reaching them. Raising ``high_threshold`` can only
  turn some previously-``True`` blocks into ``False`` (for Spans whose
  confidence sits in ``[old_threshold, new_threshold)``) -- it can never turn
  a ``False`` block into ``True``, since the comparison only gets harder to
  satisfy as the threshold rises. So raising the threshold can only add
  opportunities for an Approximate_Strategy to run, never remove one solely
  through this gate.

So the *name* of Property 9 ("never adds") refers to what happens as the
threshold rises being monotonically non-decreasing in the correction count
-- i.e. raising the threshold never *removes* approximate corrections that a
lower threshold would keep, while it may *add* new ones for spans in the
threshold gap. The design.md property text (count for threshold_b, the
higher value, is "no lower than" the count for threshold_a) is the precise,
correctly-directed assertion this test implements; no amendment to
requirements.md or design.md is needed here -- Req 1.2, Req 16.8, and the
design.md Property 9 text all agree with the code's actual monotonicity
direction.

Because raising ``High_Confidence_Threshold`` can only ever loosen (never
tighten) the Confidence_Gate, this test also re-runs the flag-off baseline
guardrail (Property 12, Req 12.9) for the same generated input, confirming
that the monotonicity mechanics introduced by this gate do not disturb the
separately-guaranteed Baseline-equivalence property.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.correction.engine import correct_words
from app.correction.gates import GateConfig, GateContext
from app.correction.strategies import is_deterministic
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex
from tests.property.baseline_equivalence import assert_baseline_equivalent
from tests.property.strategies import word_lists

# Threshold values span the full [0.0, 1.0] range mandated by Req 16.8 /
# design.md Property 9. A modest, evenly-spaced grid keeps hypothesis's
# shrinking useful while still exercising both loose and strict thresholds.
_THRESHOLD_VALUES = [0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]


def _approx_correction_count(
    words: list[dict],
    threshold: float,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
) -> int:
    """Run ``correct_words`` at *threshold* and count Approximate_Strategy corrections.

    Builds a non-inert :class:`GateContext` (so the Confidence_Gate is
    active) whose only enabled flag is ``sitting_scope_enabled`` with no
    sitting-scope member set attached. That flag flips ``GateContext.is_inert``
    to ``False`` -- which is what makes the Confidence_Gate (and only the
    Confidence_Gate) consulted -- while adding no observable side effect of
    its own: ``_apply_sitting_scope`` returns its input unchanged whenever
    ``gate_context.sitting_scope`` is empty/``None``, and every *other* flag
    (lexicon, evidence-scaled scoring, context gate, LLM veto) stays off. This
    isolates the High_Confidence_Threshold as the only varying, effective
    input across the two runs being compared.
    """
    config = GateConfig(
        high_confidence_threshold=threshold,
        sitting_scope_enabled=True,
    )
    context = GateContext(config=config, sitting_scope=None)
    result = correct_words([dict(w) for w in words], index, snapshot, gate_context=context)
    return sum(1 for c in result.corrections if not is_deterministic(c[2]))


@st.composite
def _words_with_confidence(draw: st.DrawFn) -> list[dict]:
    """Draw a Words list in which every Word carries a Word_Confidence.

    Property 9 is scoped ("evaluated over one Correction_Request whose Words
    each carry a Word_Confidence", design.md) to inputs where every Word has a
    confidence value, so Span_Confidence is always known (never Unknown) and
    the Confidence_Gate's ``span_conf >= threshold`` comparison is exercised
    directly rather than short-circuited by the Unknown case (which Req 1.6
    already governs and Property 9 does not target).
    """
    word_list = draw(word_lists(min_size=1, max_size=40))
    for word in word_list:
        if "confidence" not in word:
            word["confidence"] = draw(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
            )
    return word_list


@given(
    words=_words_with_confidence(),
    threshold_pair=st.tuples(
        st.sampled_from(_THRESHOLD_VALUES), st.sampled_from(_THRESHOLD_VALUES)
    ).filter(lambda pair: pair[0] < pair[1]),
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_raising_confidence_threshold_never_lowers_approximate_count(
    words: list[dict],
    threshold_pair: tuple[float, float],
    property_index: MatchIndex,
    property_snapshot: DatasetSnapshot,
) -> None:
    """Property 9: a strictly higher High_Confidence_Threshold never yields
    fewer Approximate_Strategy corrections.

    For any pair of High_Confidence_Threshold values in [0.0, 1.0] with
    ``threshold_a < threshold_b``, evaluated over one Correction_Request
    whose Words each carry a Word_Confidence, against one fixed Dataset_Cache
    snapshot, with every other setting held constant, the count of
    corrections reporting an Approximate_Strategy (``phonetic``, ``fuzzy``,
    ``substring``) produced with ``threshold_b`` is greater than or equal to
    the count produced with ``threshold_a``.

    Also re-runs the flag-off Baseline-equivalence guardrail (Property 12,
    Req 12.9) on the same generated Words list, since this gate's
    monotonicity mechanics must not disturb that separately-guaranteed
    property.

    **Validates: Requirements 1.2, 16.8**
    """
    threshold_a, threshold_b = threshold_pair
    assert threshold_a < threshold_b

    count_a = _approx_correction_count(words, threshold_a, property_index, property_snapshot)
    count_b = _approx_correction_count(words, threshold_b, property_index, property_snapshot)

    assert count_b >= count_a, (
        "raising High_Confidence_Threshold must never lower the count of "
        "Approximate_Strategy corrections.\n"
        f"threshold_a={threshold_a} -> {count_a} approximate corrections\n"
        f"threshold_b={threshold_b} -> {count_b} approximate corrections\n"
        f"words: {words!r}"
    )

    # Property 12 guardrail (Req 12.9): the flag-off path must stay
    # Baseline-equivalent regardless of this gate's behaviour at any
    # threshold. Build a matching transcript text so correct_text's
    # Span-to-Word alignment (Req 1.7) is meaningful for the same input.
    text = " ".join(w["word"] for w in words)
    assert_baseline_equivalent(text, words, property_index, property_snapshot)
