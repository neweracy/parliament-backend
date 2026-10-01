"""Regression tests for the five recorded false positives (task 6.3.2, Req 16.13).

The introduction to the ``correction-precision-gating`` requirements records
five ordinary English words that the Baseline engine mis-corrected into
Ghanaian entities, each previously suppressed by appending a Block_List row:

* ``thank``          -> Ghana
* ``sage``           -> Sege
* ``district``       -> Bole
* ``transportation`` -> Joseph Bukari Nikpe
* ``later``          -> Lartey (alias of *Agnes Naa Momo Lartey*)

Req 16.13 requires, for each, a regression test that supplies the token with a
Word_Confidence at or above the default High_Confidence_Threshold (0.90) and
asserts the token is left UNCHANGED in both the transcript text and the Words
list, with NO corrections entry covering it.

Which snapshot, and why (the crux of a *gate* regression test)
--------------------------------------------------------------
These tests deliberately run against :func:`tests.evaluation.dataset.build_fixture_snapshot`,
whose ``block_list`` is **EMPTY** by default, rather than the property fixture
:func:`tests.property.fixtures.build_property_snapshot`, whose
``FIXTURE_BLOCK_LIST`` already contains all five tokens. With a populated block
list the tokens would survive trivially — the Block_List guard would reject the
approximate match before any gate ran, and the test would prove nothing about
the gates. The evaluation snapshot has the SAME collision targets (Ghana, Sege,
Bole, Joseph Bukari Nikpe, and *Agnes Naa Momo Lartey* with the ``Lartey``
alias) but NO block list, so a token that survives here survives *because the
Confidence_Gate and Lexicon_Gate suppressed the approximate match* — which is
exactly the regression Req 16.13 guards (the false positives must stay fixed
after the Block_List is retired, Req 11.2).

The gates are ACTIVE: the :class:`~app.correction.gates.GateContext` is built
from a :class:`~app.config.Settings` with the three Phase-1 flags on
(``lexicon_gate_enabled``, ``evidence_confidence_enabled``, ``context_gate_enabled``),
the bundled English_Lexicon attached, and the ``deepgram`` provider profile
resolved — mirroring :func:`tests.evaluation.measure.gated_gate_context` and the
production pipeline boundary. All five words are ordinary English lexicon members
supplied at confidence 0.95 (>= the 0.90 High_Confidence_Threshold), so the
Confidence_Gate excludes every Approximate_Strategy for them and, independently,
the Lexicon_Gate rejects them (they are lexicon members, absent from the alias
set, at/above the Lexicon_Override_Threshold).
"""

from __future__ import annotations

import pytest

from app.config import Settings, clamp_ranges, provider_profiles
from app.correction.engine import correct_text, correct_words
from app.correction.gates import GateContext
from app.datasets.cache import DatasetSnapshot
from tests.evaluation.dataset import build_fixture_snapshot, load_fixture_lexicon

# Each entry: (token, collision_target). ``collision_target`` is the canonical
# (or aliased canonical) the Baseline engine mis-corrected the token into, and
# the string the token must NOT become. The five are the exact recorded
# false positives from the requirements introduction (Req 16.13).
RECORDED_FALSE_POSITIVES: list[tuple[str, str]] = [
    ("thank", "Ghana"),
    ("sage", "Sege"),
    ("district", "Bole"),
    ("transportation", "Joseph Bukari Nikpe"),
    ("later", "Agnes Naa Momo Lartey"),
]

# High confidence, at/above the default High_Confidence_Threshold of 0.90
# (Req 16.13 requires ">= default High_Confidence_Threshold"; 0.95 clears it).
HIGH_CONFIDENCE = 0.95


@pytest.fixture(scope="module")
def empty_blocklist_snapshot() -> DatasetSnapshot:
    """The evaluation fixture snapshot with an EMPTY Block_List (Req 11.2, 16.13).

    Carries the collision targets (Ghana, Sege, Bole, Joseph Bukari Nikpe,
    Agnes Naa Momo Lartey) but no Block_List entries, so a surviving token
    proves the GATES — not the list — suppressed the false positive.
    """
    return build_fixture_snapshot()


@pytest.fixture(scope="module")
def gated_context() -> GateContext:
    """A gated GateContext: Phase-1 flags on, lexicon + ``deepgram`` profile.

    Mirrors :func:`tests.evaluation.measure.gated_gate_context` and the pipeline
    boundary: the three Phase-1 gate flags enabled, run through ``clamp_ranges``
    as startup does, the bundled English_Lexicon attached (Req 2.5-2.8), and the
    ``deepgram`` provider profile resolved (task 2.12.3). Sitting_Scope and LLM
    Veto stay disabled — this is a rule-stage gating regression.
    """
    settings = clamp_ranges(
        Settings(
            lexicon_gate_enabled=True,
            evidence_confidence_enabled=True,
            context_gate_enabled=True,
            sitting_scope_enabled=False,
            llm_veto_enabled=False,
        )
    )
    profile = provider_profiles(settings).get("deepgram")
    return GateContext.from_settings(
        settings,
        lexicon=load_fixture_lexicon(),
        provider="deepgram",
        profile=profile,
    )


def _words_for(token: str) -> list[dict]:
    """Build a single high-confidence Word carrying *token* (Req 16.13)."""
    return [
        {
            "word": token,
            "start": 0.0,
            "end": 0.5,
            "confidence": HIGH_CONFIDENCE,
            "punctuated_word": token,
        }
    ]


@pytest.mark.parametrize(
    ("token", "collision_target"),
    RECORDED_FALSE_POSITIVES,
    ids=[token for token, _ in RECORDED_FALSE_POSITIVES],
)
def test_recorded_false_positive_unchanged_in_text(
    token: str,
    collision_target: str,
    empty_blocklist_snapshot: DatasetSnapshot,
    gated_context: GateContext,
) -> None:
    """A high-confidence recorded false positive is left unchanged by correct_text.

    With the gates active and the token supplied at confidence 0.95 (>= the
    0.90 High_Confidence_Threshold), ``correct_text`` must leave the token
    verbatim in ``.text``, must record no correction covering it, and must never
    emit the collision target.

    **Validates: Requirements 16.13**
    """
    snapshot = empty_blocklist_snapshot
    words = _words_for(token)

    result = correct_text(
        token,
        snapshot.index,
        snapshot,
        gate_context=gated_context,
        words=words,
    )

    # The token is left verbatim in the corrected transcript text.
    assert result.text == token, (
        f"'{token}' was rewritten to '{result.text}' (expected unchanged)"
    )
    # No correction entry covers the single-token Span.
    assert result.corrections == [], (
        f"'{token}' produced corrections {result.corrections} "
        f"(expected none; collision target was '{collision_target}')"
    )
    # Defensive: the collision target never appears in the output text.
    assert collision_target not in result.text


@pytest.mark.parametrize(
    ("token", "collision_target"),
    RECORDED_FALSE_POSITIVES,
    ids=[token for token, _ in RECORDED_FALSE_POSITIVES],
)
def test_recorded_false_positive_unchanged_in_words(
    token: str,
    collision_target: str,
    empty_blocklist_snapshot: DatasetSnapshot,
    gated_context: GateContext,
) -> None:
    """A high-confidence recorded false positive is left unchanged by correct_words.

    With the gates active and the token supplied at confidence 0.95, the output
    Word must equal the input Word text, no correction tuple may name the token,
    and the collision target must not appear.

    **Validates: Requirements 16.13**
    """
    snapshot = empty_blocklist_snapshot
    words = _words_for(token)

    result = correct_words(
        words,
        snapshot.index,
        snapshot,
        gate_context=gated_context,
    )

    # The Words list is unchanged: one Word, same text.
    assert len(result.words) == 1, (
        f"'{token}' changed the Word count to {len(result.words)} (expected 1)"
    )
    assert result.words[0].get("word") == token, (
        f"'{token}' was rewritten to '{result.words[0].get('word')}' in the "
        f"Words list (expected unchanged)"
    )
    # No correction tuple names the token. Each tuple is
    # (original, corrected, strategy, confidence, kind, type).
    assert result.corrections == [], (
        f"'{token}' produced corrections {result.corrections} "
        f"(expected none; collision target was '{collision_target}')"
    )
    # Defensive: the collision target is never written into the Word.
    assert result.words[0].get("word") != collision_target
