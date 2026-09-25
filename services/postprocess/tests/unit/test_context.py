"""Unit tests for app/correction/context.py — the Context_Gate (Req 7).

Covers the pure Context_Signal detection functions and their engine wiring:

* :func:`context_window` — Speaker_Turn vs token-window selection (Req 7.4, 7.5).
* :func:`has_context_signal` — the four Req 7.1 signals: (a) preceding
  Title_Prefix, (b) "Member for", (c) constituency in window, (d) uppercase
  Capitalization_Prior.
* :func:`context_gate_rejects_person` — rejection only of Approximate person
  matches with no signal (Req 7.2), accept Deterministic without signal
  (Req 7.3), accept non-person without signal (Req 7.7).
* Engine integration through ``correct_single``: a no-signal approximate person
  match is rejected when the gate is active; deterministic and non-person are
  accepted; baseline equivalence holds when the context is inert (Req 12.9).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.correction.capitalization import CapPrior
from app.correction.context import (
    context_gate_rejects_person,
    context_window,
    has_context_signal,
)
from app.correction.engine import correct_single
from app.correction.gates import GateConfig, GateContext
from app.correction.strategies import MatchResult
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _snapshot(records, *, title_prefixes=frozenset({"honourable", "hon", "minister"})):
    index = build_index(records)
    return index, DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset({"the", "a", "for", "of"}),
        word_stopwords=frozenset({"the", "for", "of"}),
        title_prefixes=title_prefixes,
    )


def _person_env():
    """An env where 'mensa' approximately matches the person 'Mensah'."""
    records = [
        EntityRecord(
            canonical="Samuel Mensah",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["mensah"],
            source="persons",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Tema East",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=[],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
            source="supplementary",
            source_rank=0,
        ),
    ]
    return _snapshot(records)


def _w(word: str, *, confidence=None, speaker=None, punctuated=None) -> dict:
    d: dict = {"word": word, "start": 0.0, "end": 0.1}
    if confidence is not None:
        d["confidence"] = confidence
    if speaker is not None:
        d["speaker"] = speaker
    if punctuated is not None:
        d["punctuated_word"] = punctuated
    return d


def _person_match() -> MatchResult:
    return MatchResult(
        canonical="Samuel Mensah",
        confidence=0.80,
        strategy="phonetic",
        entity_kind="person",
        entity_type="mp",
    )


# ---------------------------------------------------------------------------
# context_window — Req 7.4, 7.5
# ---------------------------------------------------------------------------


class TestContextWindow:
    def test_empty_words_is_empty_range(self):
        assert list(context_window(0, 1, [], 40)) == []

    def test_token_window_no_speaker(self):
        # Req 7.5: +/- window_words, truncated at bounds.
        words = [_w(str(i)) for i in range(10)]
        win = context_window(5, 1, words, 2)
        # span at index 5, +/-2 → [3, 8)
        assert list(win) == [3, 4, 5, 6, 7]

    def test_token_window_truncated_at_start(self):
        words = [_w(str(i)) for i in range(5)]
        win = context_window(0, 1, words, 3)
        assert list(win) == [0, 1, 2, 3]

    def test_token_window_truncated_at_end(self):
        words = [_w(str(i)) for i in range(5)]
        win = context_window(4, 1, words, 3)
        assert list(win) == [1, 2, 3, 4]

    def test_speaker_turn_window(self):
        # Req 7.4: enclosing Speaker_Turn. Speakers: 0 0 1 1 0
        words = [
            _w("a", speaker=0),
            _w("b", speaker=0),
            _w("c", speaker=1),
            _w("d", speaker=1),
            _w("e", speaker=0),
        ]
        # Span at index 2 (speaker 1) → the contiguous speaker-1 run [2, 4)
        win = context_window(2, 1, words, 40)
        assert list(win) == [2, 3]

    def test_speaker_turn_ignores_window_words(self):
        # A large window_words is irrelevant when a speaker turn bounds it.
        words = [_w("a", speaker=7)] * 3 + [_w("b", speaker=9)] + [_w("c", speaker=7)] * 3
        # index 3 is the lone speaker-9 turn
        win = context_window(3, 1, words, 100)
        assert list(win) == [3]

    def test_mixed_speaker_span_falls_back_to_token_window(self):
        # A Span whose covered Words disagree on speaker uses the token window.
        words = [_w("a", speaker=0), _w("b", speaker=1), _w("c", speaker=1)]
        win = context_window(0, 2, words, 1)
        # covered = indices 0,1 (speakers 0,1 disagree) → token window +/-1
        assert list(win) == [0, 1, 2]


# ---------------------------------------------------------------------------
# has_context_signal — the four Req 7.1 signals
# ---------------------------------------------------------------------------


class TestHasContextSignal:
    def test_title_prefix_precedes(self):
        # Signal (a): "Hon Mensa" — title immediately before the Span.
        index, snapshot = _person_env()
        words = [_w("Hon"), _w("Mensa")]
        assert has_context_signal(1, 1, words, snapshot, index, 40) is True

    def test_title_prefix_with_intervening_punctuation(self):
        # Req 7.1a: only punctuation may intervene; "Hon" "." "Mensa".
        index, snapshot = _person_env()
        words = [_w("Hon"), _w("."), _w("Mensa")]
        assert has_context_signal(2, 1, words, snapshot, index, 40) is True

    def test_title_prefix_case_insensitive(self):
        index, snapshot = _person_env()
        words = [_w("HONOURABLE"), _w("mensa")]
        assert has_context_signal(1, 1, words, snapshot, index, 40) is True

    def test_intervening_non_punct_breaks_adjacency(self):
        # A non-title, non-punctuation word between the title and the Span
        # breaks signal (a) (and there is no other signal here).
        index, snapshot = _person_env()
        words = [_w("hon"), _w("said"), _w("mensa")]
        assert has_context_signal(2, 1, words, snapshot, index, 40) is False

    def test_member_for_signal(self):
        # Signal (b): "Member for" within the window.
        index, snapshot = _person_env()
        words = [_w("Member"), _w("for"), _w("something"), _w("mensa")]
        assert has_context_signal(3, 1, words, snapshot, index, 40) is True

    def test_member_for_case_insensitive_and_punctuated(self):
        index, snapshot = _person_env()
        words = [_w("member"), _w("FOR,"), _w("mensa")]
        assert has_context_signal(2, 1, words, snapshot, index, 40) is True

    def test_member_without_for_is_no_signal(self):
        index, snapshot = _person_env()
        words = [_w("member"), _w("said"), _w("mensa")]
        assert has_context_signal(2, 1, words, snapshot, index, 40) is False

    def test_constituency_in_window_signal(self):
        # Signal (c): a constituency ("Tema East") matched deterministically
        # within the window supports the person Span.
        index, snapshot = _person_env()
        words = [_w("Tema"), _w("East"), _w("said"), _w("mensa")]
        assert has_context_signal(3, 1, words, snapshot, index, 40) is True

    def test_uppercase_prior_signal(self):
        # Signal (d): uppercase Capitalization_Prior on the Span's first Word.
        # Need a non-sentence-initial position so the prior is not discounted.
        index, snapshot = _person_env()
        words = [
            _w("and", punctuated="and"),
            _w("mensa", punctuated="Mensa"),
        ]
        assert has_context_signal(1, 1, words, snapshot, index, 40) is True

    def test_no_signal(self):
        # A lone lowercase word mid-sentence with no title, no member-for, no
        # constituency, and a lowercase prior → no signal.
        index, snapshot = _person_env()
        words = [
            _w("and", punctuated="and"),
            _w("mensa", punctuated="mensa"),
        ]
        assert has_context_signal(1, 1, words, snapshot, index, 40) is False

    def test_empty_words_no_signal(self):
        # Req 7.5 / 6.5: empty Words list → Unknown prior, no window content.
        index, snapshot = _person_env()
        assert has_context_signal(0, 1, [], snapshot, index, 40) is False

    def test_precomputed_uppercase_prior_short_circuits(self):
        index, snapshot = _person_env()
        words = [_w("mensa")]
        assert (
            has_context_signal(
                0, 1, words, snapshot, index, 40, cap_prior=CapPrior.UPPERCASE
            )
            is True
        )


# ---------------------------------------------------------------------------
# context_gate_rejects_person — Req 7.2, 7.3, 7.7
# ---------------------------------------------------------------------------


class TestContextGateRejectsPerson:
    def test_approx_person_no_signal_rejected(self):
        # Req 7.2: approximate person match with no signal → reject.
        index, snapshot = _person_env()
        words = [_w("and", punctuated="and"), _w("mensa", punctuated="mensa")]
        assert (
            context_gate_rejects_person(
                _person_match(), 1, 1, words, snapshot, index, 40,
                is_deterministic=False,
            )
            is True
        )

    def test_approx_person_with_signal_accepted(self):
        # A title precedes the Span → signal (a) → not rejected.
        index, snapshot = _person_env()
        words = [_w("Hon"), _w("mensa")]
        assert (
            context_gate_rejects_person(
                _person_match(), 1, 1, words, snapshot, index, 40,
                is_deterministic=False,
            )
            is False
        )

    def test_deterministic_person_never_rejected(self):
        # Req 7.3: deterministic person match accepted without a signal.
        index, snapshot = _person_env()
        words = [_w("and"), _w("mensa")]
        det = MatchResult(
            canonical="Samuel Mensah", confidence=0.98, strategy="fused",
            entity_kind="person", entity_type="mp",
        )
        assert (
            context_gate_rejects_person(
                det, 1, 1, words, snapshot, index, 40, is_deterministic=True
            )
            is False
        )

    def test_non_person_never_rejected(self):
        # Req 7.7: a non-person approximate match is never context-gated.
        index, snapshot = _person_env()
        words = [_w("and"), _w("tama")]
        loc = MatchResult(
            canonical="Tema East", confidence=0.80, strategy="phonetic",
            entity_kind="location", entity_type="constituency",
        )
        assert (
            context_gate_rejects_person(
                loc, 1, 1, words, snapshot, index, 40, is_deterministic=False
            )
            is False
        )


# ---------------------------------------------------------------------------
# Engine integration through correct_single
# ---------------------------------------------------------------------------


def _context_context() -> GateContext:
    """A non-inert context with only the Context_Gate flag on."""
    return GateContext(config=GateConfig(context_gate_enabled=True))


class TestEngineContextGate:
    def test_no_signal_approx_person_rejected(self):
        # An approximate person match with no context signal is rejected: the
        # Span is preserved (correct_single returns None).
        index, snapshot = _person_env()
        words = [_w("and", punctuated="and"), _w("mensa", punctuated="mensa")]
        result = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=_context_context(),
            words=words, span_start_index=1,
        )
        assert result is None

    def test_title_prefixed_person_accepted_with_normal_confidence(self):
        # A title precedes the name Span → signal (a). The person match passes
        # the gate and keeps its normal (unboosted) confidence (Req 7.6, 7.10).
        index, snapshot = _person_env()
        words = [_w("Hon"), _w("mensa")]
        baseline = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=GateContext.inert(),
        )
        gated = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=_context_context(),
            words=words, span_start_index=1,
        )
        assert gated is not None
        assert gated.canonical == "Samuel Mensah"
        # Same confidence as the ungated approximate match — no boost/reduction.
        assert baseline is not None
        assert gated.confidence == baseline.confidence

    def test_member_for_context_accepts_person(self):
        index, snapshot = _person_env()
        words = [_w("Member"), _w("for"), _w("Tema"), _w("mensa")]
        result = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=_context_context(),
            words=words, span_start_index=3,
        )
        assert result is not None
        assert result.canonical == "Samuel Mensah"

    def test_deterministic_person_accepted_without_signal(self):
        # 'mensah' is an exact alias → deterministic → accepted with no signal.
        index, snapshot = _person_env()
        words = [_w("and"), _w("mensah")]
        result = correct_single(
            "mensah", 1, index, snapshot,
            gate_context=_context_context(),
            words=words, span_start_index=1,
        )
        assert result is not None
        assert result.canonical == "Samuel Mensah"
        assert result.strategy == "exact"

    def test_non_person_accepted_without_signal(self):
        # An approximate location match ("kumase" -> "Kumasi") needs no context
        # signal (Req 7.7): the Context_Gate only guards person entities.
        index, snapshot = _person_env()
        words = [_w("and"), _w("kumase")]
        result = correct_single(
            "kumase", 1, index, snapshot,
            gate_context=_context_context(),
            words=words, span_start_index=1,
        )
        assert result is not None
        assert result.canonical == "Kumasi"
        assert result.entity_kind == "location"

    def test_inert_context_is_baseline(self):
        # Req 12.9: with every flag off the Context_Gate is inert — a no-signal
        # approximate person match applies exactly as Baseline.
        index, snapshot = _person_env()
        words = [_w("and"), _w("mensa")]
        result = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=GateContext.inert(),
            words=words, span_start_index=1,
        )
        assert result is not None
        assert result.canonical == "Samuel Mensah"

    def test_no_context_matches_inert(self):
        # gate_context=None normalises to inert (Baseline path).
        index, snapshot = _person_env()
        result = correct_single("mensa", 1, index, snapshot)
        assert result is not None
        assert result.canonical == "Samuel Mensah"

    def test_speaker_turn_window_used_for_signal(self):
        # The "Member for" pair lies in the Span's speaker turn but a token
        # window of 0 would miss it; the speaker turn still finds it (Req 7.4).
        index, snapshot = _person_env()
        words = [
            _w("Member", speaker=1),
            _w("for", speaker=1),
            _w("Tema", speaker=1),
            _w("mensa", speaker=1),
        ]
        result = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=GateContext(
                config=GateConfig(context_gate_enabled=True, context_window_words=0)
            ),
            words=words, span_start_index=3,
        )
        assert result is not None
        assert result.canonical == "Samuel Mensah"

    def test_token_window_used_when_no_speaker(self):
        # Without speaker labels, a token window of 0 misses the "Member for"
        # pair two tokens back → rejected. Widening the window finds it.
        index, snapshot = _person_env()
        words = [_w("Member"), _w("for"), _w("Tema"), _w("mensa")]
        rejected = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=GateContext(
                config=GateConfig(context_gate_enabled=True, context_window_words=0)
            ),
            words=words, span_start_index=3,
        )
        assert rejected is None
        accepted = correct_single(
            "mensa", 1, index, snapshot,
            gate_context=GateContext(
                config=GateConfig(context_gate_enabled=True, context_window_words=40)
            ),
            words=words, span_start_index=3,
        )
        assert accepted is not None
        assert accepted.canonical == "Samuel Mensah"
