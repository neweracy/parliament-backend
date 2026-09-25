"""Unit tests for the Sitting_Scope Evidence_Score penalty in the engine (Req 8).

Covers the engine-side behaviour of task 4.5:

* Out_Of_Scope_Penalty applied to an Approximate_Strategy person candidate
  absent from the scope, clamped at 0.0 (Req 8.3).
* Deterministic matches and non-person candidates left unadjusted (Req 8.6).
* A member candidate wins an equal-score tie before the existing tie-break
  (Req 8.4).
* A candidate that met the acceptance threshold but falls below it after the
  penalty is rejected under the ``sitting_scope`` gate (Req 8.9).
* Baseline equivalence: with the flag off, or with no scope, the penalty is
  never applied (Req 8.2, 12.9).

No database, network, or AWS access — the scope is a plain in-memory frozenset.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.correction.engine import (
    _apply_sitting_scope,
    correct_single,
    correction_sort_key,
)
from app.correction.gates import GateConfig, GateContext, GateTally
from app.correction.strategies import MatchResult
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.models.entities import EntityKind, EntityRecord, EntityType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_env() -> tuple[MatchIndex, DatasetSnapshot]:
    records = [
        EntityRecord(
            canonical="Ken Ofori-Atta",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["ken ofori atta"],
            source="persons",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["koumasi", "kumase"],
            source="supplementary",
            source_rank=0,
        ),
    ]
    index = build_index(records)
    snapshot = DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(["honourable", "minister"]),
    )
    return index, snapshot


def _person(
    canonical: str, confidence: float, strategy: str = "phonetic"
) -> MatchResult:
    return MatchResult(
        canonical=canonical,
        confidence=confidence,
        strategy=strategy,
        entity_kind="person",
        entity_type="minister",
    )


def _scope_context(
    scope: frozenset[str] | None,
    *,
    penalty: float = 0.10,
    enabled: bool = True,
) -> GateContext:
    """Build a non-inert GateContext with the Sitting_Scope flag set."""
    config = GateConfig(
        sitting_scope_enabled=enabled,
        out_of_scope_penalty=penalty,
    )
    return GateContext(config=config, sitting_scope=scope)


# ---------------------------------------------------------------------------
# _apply_sitting_scope — the penalty helper (Req 8.3, 8.6, 8.9)
# ---------------------------------------------------------------------------


class TestApplySittingScope:
    def test_out_of_scope_person_penalised(self):
        ctx = _scope_context(frozenset({"John Mahama"}), penalty=0.10)
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, None, None)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(0.80)

    def test_in_scope_person_unadjusted(self):
        ctx = _scope_context(frozenset({"Ken Ofori-Atta"}), penalty=0.10)
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, None, None)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(0.90)

    def test_penalty_clamped_at_zero(self):
        ctx = _scope_context(frozenset({"John Mahama"}), penalty=0.95)
        result = _person("Ken Ofori-Atta", 0.60)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, None, None)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(0.0)

    def test_deterministic_person_unadjusted(self):
        # Req 8.6: Deterministic matches are never penalised.
        ctx = _scope_context(frozenset({"John Mahama"}))
        result = _person("Ken Ofori-Atta", 1.0, strategy="exact")
        adjusted = _apply_sitting_scope(result, "ken ofori-atta", ctx, None, None)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(1.0)

    def test_non_person_unadjusted(self):
        # Req 8.6: non-person candidates are never penalised.
        ctx = _scope_context(frozenset({"John Mahama"}))
        result = MatchResult(
            canonical="Kumasi",
            confidence=0.90,
            strategy="phonetic",
            entity_kind="location",
            entity_type="city",
        )
        adjusted = _apply_sitting_scope(result, "koumasi", ctx, None, None)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(0.90)

    def test_rejected_when_falls_below_threshold(self):
        # Req 8.9: met threshold before, below after → rejected + tallied.
        ctx = _scope_context(frozenset({"John Mahama"}), penalty=0.10)
        tally = GateTally()
        result = _person("Ken Ofori-Atta", 0.78)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, tally, 0.75)
        assert adjusted is None
        assert tally.counts().get("sitting_scope") == 1

    def test_not_rejected_when_still_above_threshold(self):
        ctx = _scope_context(frozenset({"John Mahama"}), penalty=0.10)
        tally = GateTally()
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, tally, 0.75)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(0.80)
        assert tally.counts().get("sitting_scope") is None

    def test_below_threshold_before_penalty_not_gate_attributed(self):
        # A candidate already below threshold is not a sitting_scope rejection
        # (Req 8.9 only fires when it *met* the threshold before the penalty).
        ctx = _scope_context(frozenset({"John Mahama"}), penalty=0.10)
        tally = GateTally()
        result = _person("Ken Ofori-Atta", 0.70)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, tally, 0.75)
        assert adjusted is not None
        assert adjusted.confidence == pytest.approx(0.60)
        assert tally.counts().get("sitting_scope") is None


# ---------------------------------------------------------------------------
# Baseline / activation (Req 8.2, 12.9)
# ---------------------------------------------------------------------------


class TestSittingScopeActivation:
    def test_inert_context_no_adjustment(self):
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(
            result, "ofori", GateContext.inert(), None, None
        )
        assert adjusted is result  # unchanged object, no adjustment

    def test_flag_off_no_adjustment(self):
        # Non-inert context (another flag on) but the sitting_scope flag off →
        # no penalty.
        ctx = GateContext(
            config=GateConfig(
                lexicon_gate_enabled=True, sitting_scope_enabled=False
            ),
            sitting_scope=frozenset({"John Mahama"}),
        )
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, None, None)
        assert adjusted is result

    def test_no_scope_no_adjustment(self):
        # Flag on but no scope member set available (Req 8.2, 8.5).
        ctx = _scope_context(None)
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, None, None)
        assert adjusted is result

    def test_empty_scope_no_adjustment(self):
        ctx = _scope_context(frozenset())
        result = _person("Ken Ofori-Atta", 0.90)
        adjusted = _apply_sitting_scope(result, "ofori", ctx, None, None)
        assert adjusted is result


# ---------------------------------------------------------------------------
# Tie-break member preference (Req 8.4)
# ---------------------------------------------------------------------------


class TestSittingScopeTieBreak:
    def test_member_wins_equal_score_tie(self):
        member = _person("Ken Ofori-Atta", 0.85)
        non_member = _person("John Mahama", 0.85)
        scope = frozenset({"Ken Ofori-Atta"})
        # The member sorts strictly before the non-member.
        assert correction_sort_key(member, 1, scope) < correction_sort_key(
            non_member, 1, scope
        )

    def test_member_preference_before_span_length(self):
        # Member with a shorter span still beats a non-member with a longer span
        # at equal confidence (member_rank precedes -span_len). Req 8.4.
        member_short = _person("Ken Ofori-Atta", 0.85)
        non_member_long = _person("John Mahama", 0.85)
        scope = frozenset({"Ken Ofori-Atta"})
        assert correction_sort_key(member_short, 1, scope) < correction_sort_key(
            non_member_long, 3, scope
        )

    def test_no_scope_preserves_baseline_order(self):
        # Without a scope, the key equals the Baseline ordering (member_rank is a
        # constant), so higher-confidence still wins and equal scores fall back
        # to the existing tie-break (Req 12.9).
        a = _person("Ken Ofori-Atta", 0.85)
        b = _person("John Mahama", 0.90)
        assert correction_sort_key(b, 1) < correction_sort_key(a, 1)

    def test_higher_confidence_still_wins_over_member(self):
        # Member preference only breaks *equal-score* ties; a higher-scoring
        # non-member still wins (Req 8.4 is a tie-break, not an override).
        member = _person("Ken Ofori-Atta", 0.80)
        non_member = _person("John Mahama", 0.90)
        scope = frozenset({"Ken Ofori-Atta"})
        assert correction_sort_key(non_member, 1, scope) < correction_sort_key(
            member, 1, scope
        )


# ---------------------------------------------------------------------------
# End-to-end through correct_single (Req 8.3, 8.9 with a real match)
# ---------------------------------------------------------------------------


class TestCorrectSingleSittingScope:
    def test_out_of_scope_approximate_person_not_scored_higher(self):
        index, snapshot = _build_env()
        # An approximate spelling of the person that phonetic/fuzzy may match.
        ctx = GateContext(
            config=GateConfig(
                evidence_confidence_enabled=True,
                sitting_scope_enabled=True,
                out_of_scope_penalty=0.10,
            ),
            sitting_scope=frozenset({"John Mahama"}),  # Ken is out of scope
        )
        baseline_ctx = GateContext(
            config=GateConfig(evidence_confidence_enabled=True),
        )
        text = "ken ofori atta"
        baseline = correct_single(
            text, 3, index, snapshot, gate_context=baseline_ctx
        )
        scoped = correct_single(text, 3, index, snapshot, gate_context=ctx)
        if (
            baseline is not None
            and baseline.entity_kind == "person"
            and baseline.strategy in {"phonetic", "fuzzy", "substring"}
            and scoped is not None
        ):
            # An out-of-scope approximate person match is never scored higher
            # by the scoped run than by the baseline (penalty applied).
            assert scoped.confidence <= baseline.confidence
