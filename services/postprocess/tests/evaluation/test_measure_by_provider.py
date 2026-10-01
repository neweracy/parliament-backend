"""Per-provider harness reporting tests (task 2.12.5, Req 10.6, 10.7, 1.5, 2.6).

These tests exercise the per-provider extension of the Evaluation_Harness
(:func:`tests.evaluation.measure.measure_by_provider`) that splits the
precision/recall report by ASR provider (``deepgram``, ``khaya``, ``hybrid``).
Because the gate-rejection metric dimensions stay CLOSED (task 2.12.4 keeps
``provider`` out of the metric per Req 13.3), provider-level visibility comes
through this report — so these assertions confirm the report is provider-split,
not that any metric gained a ``provider`` dimension.

The report measures the SAME corpus under each provider's gate profile
(``provider_profiles_enabled=True``, so ``khaya``/``hybrid`` resolve a distinct
profile from ``deepgram``) and each provider's realistic per-word-confidence
condition (``deepgram`` keeps confidence; ``khaya``/``hybrid`` strip it so
Span_Confidence is Unknown, Req 1.5, 1.10).

The tests assert STRUCTURAL properties — all three providers present, metrics in
``[0, 1]``, ``deepgram`` matching the existing gated results, and the confidence
condition being applied — rather than a specific ``khaya``/``hybrid`` precision,
which is not required to be deterministic across the calibration.

The whole module is offline and LLM-free (Req 10.8): it calls the engine through
the harness and never touches Bedrock, the network, or a database.
"""

from __future__ import annotations

import pytest

from app.correction.provider_profiles import SUPPORTED_PROVIDERS
from tests.evaluation.corpus import load_evaluation_corpus
from tests.evaluation.dataset import build_fixture_snapshot
from tests.evaluation.measure import (
    ProviderReport,
    _blocklist_token_set,
    _measure_config,
    baseline_gate_context,
    gated_gate_context,
    measure_by_provider,
    prepare_corpus_span_for_provider,
    provider_gate_context,
)

# Providers whose realistic condition is Unknown Span_Confidence (text-only ASR).
_TEXT_ONLY_PROVIDERS = ("khaya", "hybrid")


@pytest.fixture(scope="module")
def provider_report() -> ProviderReport:
    """Run the per-provider measurement once for the whole module."""
    return measure_by_provider()


# ---------------------------------------------------------------------------
# The report is provider-split and well-formed (Req 10.6, 10.7)
# ---------------------------------------------------------------------------


class TestProviderReportShape:
    def test_report_covers_all_three_providers(
        self, provider_report: ProviderReport
    ) -> None:
        # Task 2.12.5: the harness produces a report for all three providers.
        assert set(provider_report.by_provider) == set(SUPPORTED_PROVIDERS)
        for provider in SUPPORTED_PROVIDERS:
            assert provider_report.by_provider[provider].config == provider

    def test_metrics_bounded(self, provider_report: ProviderReport) -> None:
        # Structural: every provider's precision/recall/unchanged-rate is a
        # valid fraction in [0, 1]; per-strategy counts are populated.
        for provider in SUPPORTED_PROVIDERS:
            m = provider_report.by_provider[provider]
            assert 0.0 <= m.precision <= 1.0
            assert 0.0 <= m.recall <= 1.0
            assert 0.0 <= m.negative_unchanged_rate <= 1.0
            assert isinstance(m.applied_by_strategy, dict)

    def test_summary_lines_render_per_provider(
        self, provider_report: ProviderReport
    ) -> None:
        lines = provider_report.summary_lines()
        assert len(lines) == len(SUPPORTED_PROVIDERS)
        for provider, line in zip(SUPPORTED_PROVIDERS, lines, strict=True):
            assert line.startswith(f"[{provider}]")


# ---------------------------------------------------------------------------
# deepgram matches the existing gated results (same profile, same condition)
# ---------------------------------------------------------------------------


class TestDeepgramParity:
    def test_deepgram_matches_existing_gated_measurement(
        self, provider_report: ProviderReport
    ) -> None:
        # The deepgram profile reproduces the task 1.1 defaults, and deepgram
        # keeps the corpus confidence as-is, so measuring deepgram through the
        # per-provider path must yield the SAME precision/recall/FP as the
        # existing gated_gate_context measurement.
        corpus = load_evaluation_corpus()
        snapshot = build_fixture_snapshot()
        blocklist = _blocklist_token_set()
        gated = _measure_config(
            "gated", gated_gate_context(), corpus, snapshot, blocklist
        )
        deepgram = provider_report.by_provider["deepgram"]
        assert deepgram.true_positives == gated.true_positives
        assert deepgram.false_positives == gated.false_positives
        assert deepgram.false_negatives == gated.false_negatives
        assert deepgram.negative_unchanged == gated.negative_unchanged
        assert deepgram.precision == pytest.approx(gated.precision)
        assert deepgram.recall == pytest.approx(gated.recall)

    def test_deepgram_precision_at_least_baseline(
        self, provider_report: ProviderReport
    ) -> None:
        # As in the base harness, gating must not reduce precision below the
        # Baseline for deepgram — the false-positive-reduction goal of Req 10.
        corpus = load_evaluation_corpus()
        snapshot = build_fixture_snapshot()
        blocklist = _blocklist_token_set()
        baseline = _measure_config(
            "baseline", baseline_gate_context(), corpus, snapshot, blocklist
        )
        deepgram = provider_report.by_provider["deepgram"]
        assert deepgram.precision >= baseline.precision, (
            f"deepgram precision {deepgram.precision:.4f} < baseline "
            f"{baseline.precision:.4f}\n" + "\n".join(provider_report.summary_lines())
        )


# ---------------------------------------------------------------------------
# khaya/hybrid are measured under the Unknown Span_Confidence condition
# ---------------------------------------------------------------------------


class TestTextOnlyProviderCondition:
    def test_confidence_stripped_for_text_only_providers(self) -> None:
        # Req 1.5, 1.10: for khaya/hybrid the harness prepares the SAME corpus
        # Spans with per-word confidence removed (text-only provider), while
        # deepgram keeps it.
        corpus = load_evaluation_corpus()
        sample = corpus.negative_set[0]
        assert any("confidence" in w for w in sample.words), (
            "fixture precondition: the corpus Span carries per-word confidence"
        )

        deepgram_span = prepare_corpus_span_for_provider(sample, "deepgram")
        assert any("confidence" in w for w in deepgram_span.words)
        assert deepgram_span.provider == "deepgram"

        for provider in _TEXT_ONLY_PROVIDERS:
            prepared = prepare_corpus_span_for_provider(sample, provider)
            assert all("confidence" not in w for w in prepared.words), (
                f"{provider}: confidence must be stripped (Unknown Span_Confidence)"
            )
            assert prepared.provider == provider
            # The text, offset, and label are untouched — same Span, new condition.
            assert prepared.text == sample.text
            assert prepared.span_start == sample.span_start
            assert prepared.enclosing_text == sample.enclosing_text
            assert prepared.label == sample.label

    def test_text_only_profiles_do_not_reject_unknown(self) -> None:
        # Task 2.12.3 calibration: with provider_profiles_enabled the khaya and
        # hybrid profiles carry lexicon_gate_reject_unknown=False, while deepgram
        # keeps the Req 2.6 reject policy. This is what the per-provider report
        # is built to validate.
        assert provider_gate_context("deepgram").profile.lexicon_gate_reject_unknown
        for provider in _TEXT_ONLY_PROVIDERS:
            ctx = provider_gate_context(provider)
            assert not ctx.profile.lexicon_gate_reject_unknown
            assert ctx.provider == provider

    def test_text_only_providers_report_sensible_numbers(
        self, provider_report: ProviderReport
    ) -> None:
        # We do NOT assert a specific khaya/hybrid precision (not deterministic
        # across the calibration); only that the harness RAN and produced
        # sensible, bounded per-provider numbers with the totals populated.
        corpus = load_evaluation_corpus()
        for provider in _TEXT_ONLY_PROVIDERS:
            m = provider_report.by_provider[provider]
            assert m.negative_total == len(corpus.negative_set)
            assert m.positive_total == len(corpus.positive_set)
            # Every negative Span is either changed or left unchanged.
            assert m.negative_unchanged + m.false_positives == m.negative_total
            assert 0.0 <= m.precision <= 1.0
            assert 0.0 <= m.recall <= 1.0


# ---------------------------------------------------------------------------
# Report-only: no metric dimension change (Req 13.3)
# ---------------------------------------------------------------------------


class TestNoMetricDimensionChange:
    def test_gate_rejection_metric_dimensions_unchanged(self) -> None:
        # Req 13.3: the gate-rejection metric carries only the `gate` dimension
        # plus dimension names already emitted — NOT `provider`. The per-provider
        # split lives entirely in the harness report structure, so emitting the
        # metric must not reference a provider dimension. Confirm the metric
        # emitter has not gained a provider dimension.
        import inspect

        from app.obs import metrics

        source = inspect.getsource(metrics.emit_gate_rejections)
        assert "provider" not in source, (
            "Req 13.3: emit_gate_rejections must not carry a `provider` dimension; "
            "per-provider visibility is report-only (task 2.12.5)."
        )
