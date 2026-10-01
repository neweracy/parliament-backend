"""Property test for the baseline-equivalence guardrail (Property 12).

**Feature: correction-precision-gating, Property 12: Baseline equivalence
when all new flags are disabled**

**Validates: Requirements 12.9**

For all Correction_Requests evaluated against a fixed Dataset_Cache snapshot
with every feature flag introduced by this specification disabled, the
Correction_Engine must produce the same transcript text, the same Words
list, and the same corrections list in the same order carrying the same
Match_Strategy and the same confidence value per correction as the Baseline.

This test owns the hypothesis-driven hook into the shared guardrail
(:mod:`tests.property.baseline_equivalence`). Every later gate task (2.2,
2.5, 2.7, 2.9, 2.11, 2.12.6, and the Phase-1/Phase-2 checkpoints) re-runs the
guardrail by importing and calling
:func:`tests.property.baseline_equivalence.assert_baseline_equivalent`
directly from its own test module -- this module is the canonical,
standalone property test for Requirement 12.9 itself, run with >=100
generated examples per Req 16.10.

Inputs are drawn from the shared fixed in-memory Dataset_Cache fixture
(Req 16.15, task 6.3.1) via :mod:`tests.property.strategies`, mixing tokens
from the fixture alias set, the bundled English_Lexicon, and random letter
sequences so both the Approximate_Strategy paths (that the new gates would
otherwise affect) and the Deterministic_Strategy paths are exercised. The
LLM_Refiner is not invoked by ``correct_text``/``correct_words`` (it runs in
a later pipeline stage), so no Bedrock/AWS call occurs in this test.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex
from tests.property.baseline_equivalence import assert_baseline_equivalent
from tests.property.strategies import word_lists


@st.composite
def _text_and_words(draw: st.DrawFn) -> tuple[str, list[dict]]:
    """Draw one Words list and a matching transcript text built from it.

    Building the transcript text by joining the same Words list's tokens
    keeps ``correct_text``'s Span-to-Word alignment (Req 1.7) meaningful for
    this test, rather than drawing two independently-shaped inputs.
    """
    word_list = draw(word_lists(min_size=1, max_size=60))
    text = " ".join(w["word"] for w in word_list)
    return text, word_list


@given(text_and_words=_text_and_words())
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_baseline_equivalence_when_all_new_flags_disabled(
    text_and_words: tuple[str, list[dict]],
    property_index: MatchIndex,
    property_snapshot: DatasetSnapshot,
) -> None:
    """Property 12: the flag-off engine path matches Baseline exactly.

    For any generated transcript text and Words list drawn from the shared
    Dataset_Cache fixture, running the Correction_Engine with every new
    feature flag disabled (via an explicit all-off ``GateContext``) must
    produce identical transcript text, Words list, and corrections (same
    order, ``Match_Strategy``, and confidence per correction) as running it
    with no gate context at all (the engine's own Baseline path).

    **Validates: Requirements 12.9**
    """
    text, words = text_and_words
    assert_baseline_equivalent(text, words, property_index, property_snapshot)
