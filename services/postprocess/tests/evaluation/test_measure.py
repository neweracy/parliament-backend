"""Precision/recall harness tests — Baseline vs gated (Req 10, 11.2, 11.5, 16.13).

These tests run the REAL Correction_Engine (:func:`app.correction.engine.correct_text`)
over the labelled Evaluation_Corpus (task 6.4.1) under the Baseline and gated
configurations in one process and assert the measured outcomes hold:

* the harness runs offline and produces a report for both configs (Req 10.6, 10.10);
* the corpus satisfies the size floors, else the run fails (Req 10.11);
* gating leaves >= 98% of the Negative_Set unchanged (Req 10.4, 10.12);
* gated recall is >= 0.95x Baseline_Recall (Req 10.5, 10.12);
* gating does not *reduce* precision — it must be >= Baseline precision, which is
  the whole point of the gates (the false-positive-reduction goal of Req 10);
* the former Block_List tokens are >= 98% unchanged with the list empty
  (Req 11.2) — gating handles them WITHOUT the block list;
* the five recorded false positives are not mis-corrected under gating (Req 16.13);
* Baseline and gated make the same stopword/word-stopword rejection decisions
  (Req 11.5), which the shared snapshot and identical thresholds guarantee.

The whole module is offline and LLM-free (Req 10.8): it calls the engine
directly and never touches Bedrock, the network, or a database.
"""

from __future__ import annotations

import pytest

from tests.evaluation.corpus import load_evaluation_corpus
from tests.evaluation.dataset import build_fixture_snapshot
from tests.evaluation.measure import (
    MeasurementReport,
    baseline_gate_context,
    evaluate_span,
    gated_gate_context,
    measure_precision_recall,
)

# The Deterministic_Strategy members (Req 2.9, 2.14) — never gated.
_DETERMINISTIC_STRATEGIES = frozenset({"exact", "fused", "joined", "initials"})

# Req 10.4 / 11.2 unchanged-rate floor.
_UNCHANGED_FLOOR = 0.98
# Req 10.5 gated-recall floor relative to Baseline_Recall.
_RECALL_RATIO_FLOOR = 0.95
# The five recorded false positives (Req 16.13).
_RECORDED_FALSE_POSITIVES = ("thank", "sage", "district", "transportation", "later")


@pytest.fixture(scope="module")
def report() -> MeasurementReport:
    """Run the Baseline and gated measurement once for the whole module.

    Module-scoped so the (relatively expensive) full-corpus run happens once;
    the whole run stays well within the Req 10.9 300-second budget.
    """
    return measure_precision_recall()


# ---------------------------------------------------------------------------
# Harness runs and reports (Req 10.6, 10.10, 10.11)
# ---------------------------------------------------------------------------


class TestHarnessRunsAndReports:
    def test_corpus_meets_size_floors(self) -> None:
        # Req 10.1-10.3, 10.11: the corpus must clear its size floors, else the
        # harness run fails with a diagnostic naming the unmet criterion.
        corpus = load_evaluation_corpus()
        total = len(corpus.negative_set) + len(corpus.positive_set)
        assert len(corpus.negative_set) >= 200, "Req 10.2: Negative_Set < 200 Spans"
        assert len(corpus.positive_set) >= 100, "Req 10.3: Positive_Set < 100 Spans"
        assert total >= 300, "Req 10.1: fewer than 300 labelled Spans"

    def test_report_has_both_configs(self, report: MeasurementReport) -> None:
        # Req 10.6, 10.10: precision, recall, and per-strategy counts reported
        # for BOTH the Baseline and the gated configuration in a single run.
        assert report.baseline.config == "baseline"
        assert report.gated.config == "gated"
        for m in (report.baseline, report.gated):
            assert 0.0 <= m.precision <= 1.0
            assert 0.0 <= m.recall <= 1.0
            # Applied-correction counts per Match_Strategy are populated.
            assert isinstance(m.applied_by_strategy, dict)

    def test_summary_lines_render(self, report: MeasurementReport) -> None:
        lines = report.summary_lines()
        assert len(lines) == 2
        assert lines[0].startswith("[baseline]")
        assert lines[1].startswith("[gated]")


# ---------------------------------------------------------------------------
# Gating reduces false positives without destroying recall (Req 10.4, 10.5)
# ---------------------------------------------------------------------------


class TestGatingOutcomes:
    def test_gated_negative_unchanged_rate(self, report: MeasurementReport) -> None:
        # Req 10.4, 10.12: gating leaves >= 98% of the Negative_Set unchanged.
        m = report.gated
        assert m.negative_unchanged_rate >= _UNCHANGED_FLOOR, (
            f"Req 10.4: gated Negative_Set unchanged rate "
            f"{m.negative_unchanged_rate:.4f} < {_UNCHANGED_FLOOR}; "
            f"changed={[(c.original, c.replacement, c.strategy) for c in m.changed_negatives]}\n"
            + "\n".join(report.summary_lines())
        )

    def test_gated_recall_not_below_floor(self, report: MeasurementReport) -> None:
        # Req 10.5, 10.12: gated recall >= 0.95 x Baseline_Recall.
        floor = _RECALL_RATIO_FLOOR * report.baseline.recall
        assert report.gated.recall >= floor, (
            f"Req 10.5: gated recall {report.gated.recall:.4f} < "
            f"{_RECALL_RATIO_FLOOR} x Baseline_Recall ({floor:.4f})\n"
            + "\n".join(report.summary_lines())
        )

    def test_gating_does_not_reduce_precision(self, report: MeasurementReport) -> None:
        # The gates exist to raise precision by cutting false positives, so the
        # gated precision must be at least the Baseline precision (Req 10 goal).
        assert report.gated.precision >= report.baseline.precision, (
            f"gated precision {report.gated.precision:.4f} < baseline "
            f"{report.baseline.precision:.4f} — gating should not lose precision\n"
            + "\n".join(report.summary_lines())
        )

    def test_gating_cuts_false_positives(self, report: MeasurementReport) -> None:
        # Directional check: gating must reduce (or at least not increase) the
        # false-positive count relative to Baseline — the measurable improvement.
        assert report.gated.false_positives <= report.baseline.false_positives


# ---------------------------------------------------------------------------
# Former Block_List handled by gating without the list (Req 11.2)
# ---------------------------------------------------------------------------


class TestBlockListRetirement:
    def test_former_blocklist_unchanged_without_list(
        self, report: MeasurementReport
    ) -> None:
        # Req 11.2, 11.10: with the Block_List EMPTY, gating leaves >= 98% of the
        # former Block_List tokens unchanged. The snapshot carries no block list
        # (tests/evaluation/dataset.build_fixture_snapshot), so any token left
        # unchanged is handled by the gates alone, not the retired list.
        total = len(report.blocklist_tokens)
        assert total >= 130, "Req 11.1: fewer than 130 former Block_List tokens"
        changed = report.gated.changed_blocklist_tokens
        unchanged_rate = (total - len(changed)) / total
        assert unchanged_rate >= _UNCHANGED_FLOOR, (
            f"Req 11.2: former Block_List unchanged rate {unchanged_rate:.4f} < "
            f"{_UNCHANGED_FLOOR} with the list empty; changed="
            f"{[(c.original, c.replacement, c.strategy) for c in changed]}"
        )

    def test_recorded_false_positives_not_miscorrected(self) -> None:
        # Req 16.13: the five recorded false positives, supplied with a high
        # Word_Confidence, are NOT mis-corrected under gating — unchanged with
        # no correction covering them.
        snapshot = build_fixture_snapshot()
        ctx = gated_gate_context()
        corpus = load_evaluation_corpus()
        by_text = {s.text.lower(): s for s in corpus.negative_set}
        for token in _RECORDED_FALSE_POSITIVES:
            span = by_text.get(token)
            assert span is not None, f"Req 16.13: '{token}' missing from Negative_Set"
            outcome = evaluate_span(span, ctx, snapshot)
            assert not outcome.corrected, (
                f"Req 16.13: '{token}' was mis-corrected under gating to "
                f"'{outcome.replacement}' via {outcome.strategy}"
            )


# ---------------------------------------------------------------------------
# Baseline and gated agree on non-approximate rejection decisions (Req 11.5)
# ---------------------------------------------------------------------------


class TestStopwordParity:
    def test_deterministic_corrections_preserved_under_gating(self) -> None:
        # Req 11.5, 2.9, 2.14: the gates act only on the Approximate_Strategy
        # members; Deterministic_Strategy matches and stopword/word-stopword
        # rejections are never touched. So EVERY correction the Baseline applied
        # via a deterministic strategy must be applied identically under gating
        # — same replacement, same strategy. (The reverse is not an equality:
        # gating can *raise* a deterministic count when it suppresses an
        # approximate match that Baseline's short-circuit chain had preferred
        # over a deterministic one, so a per-strategy count comparison would be
        # misleading. The per-Span preservation below is the true invariant.)
        snapshot = build_fixture_snapshot()
        baseline_ctx = baseline_gate_context()
        gated_ctx = gated_gate_context()
        corpus = load_evaluation_corpus()

        changed: list[str] = []
        for span in (*corpus.negative_set, *corpus.positive_set):
            base = evaluate_span(span, baseline_ctx, snapshot)
            if not (base.corrected and base.strategy in _DETERMINISTIC_STRATEGIES):
                continue
            gated = evaluate_span(span, gated_ctx, snapshot)
            if not (
                gated.corrected
                and gated.strategy == base.strategy
                and gated.replacement == base.replacement
            ):
                changed.append(
                    f"{span.text!r} baseline={base.strategy}->{base.replacement} "
                    f"gated={gated.strategy}->{gated.replacement}"
                )
        assert not changed, (
            "Req 11.5: gating altered a deterministic/stopword decision for "
            f"{len(changed)} Span(s): {changed}"
        )
