"""Precision/recall measurement — Baseline vs gated (Req 10, 11.2, 16.13).

This module runs the **real** :func:`app.correction.engine.correct_text` over
the labelled Evaluation_Corpus (task 6.4.1) under two configurations in one
process and reports precision and recall for each, so the precision improvement
gating buys is *measured*, not asserted (Req 10.6, 10.10):

* **Baseline** — an inert :class:`~app.correction.gates.GateContext` with every
  new feature flag off. This reproduces the pre-gating engine (Req 12.9) and
  yields Baseline_Precision / Baseline_Recall (Req 10.10).
* **Gated** — a :class:`~app.correction.gates.GateContext` built from a
  :class:`~app.config.Settings` with the gates enabled (``lexicon_gate_enabled``,
  ``evidence_confidence_enabled``, ``context_gate_enabled``), the bundled
  English_Lexicon attached, and the ``deepgram`` provider profile. This is the
  configuration whose false-positive reduction Req 10.4 / 11.2 require.

How a Span is scored
--------------------
For each :class:`~tests.evaluation.corpus.CorpusSpan` the engine is run once per
config over ``span.enclosing_text`` (passing ``span.words`` so the
Confidence_Gate can derive Span_Confidence). The Span's first token sits at a
known character offset in the enclosing text (the text is a space-joined token
list), so a returned :class:`~app.correction.engine.TextCorrection` whose
character range covers that offset tells us whether — and to what canonical —
the engine corrected the Span.

* **Negative_Set** (``label is None``) — any correction covering the Span is a
  **false positive**; leaving it unchanged is the desired outcome (Req 10.4).
* **Positive_Set** (``label == canonical``) — a correction *to the labelled
  canonical* is a **true positive**; no correction, or a correction to a
  *different* entity, is a **false negative** for recall, and a correction to a
  different entity is *also* a false positive for precision (Req 10.5, 10.6).

Precision = TP / (TP + FP); recall = TP / (TP + FN), computed per config.

Everything is offline and deterministic: no network, no AWS, no LLM — the
harness measures only the rule-stage engine (Req 10.8). The LLM_Refiner is never
invoked here; this module calls the engine directly rather than the async
pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings, clamp_ranges, provider_profiles
from app.correction.engine import TextCorrection, correct_text
from app.correction.gates import GateContext
from app.datasets.cache import DatasetSnapshot
from tests.evaluation.corpus import CorpusSpan, EvaluationCorpus, load_evaluation_corpus
from tests.evaluation.dataset import build_fixture_snapshot, load_fixture_lexicon

# The engine's transcript-path acceptance thresholds. Held identical across
# Baseline and gated runs so the only difference between the two is the gates
# themselves (Req 10.6, 10.10): the gated run must not win by moving a
# threshold the Baseline did not have.
_MIN_CONFIDENCE = 0.75
_FUZZY_SCORE_CUTOFF = 0.70
_MIN_CANDIDATE_LENGTH = 4


# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------


def baseline_gate_context() -> GateContext:
    """The Baseline config: an inert context, every new flag off (Req 10.10, 12.9)."""
    return GateContext.inert()


def gated_gate_context() -> GateContext:
    """The gated config: Phase-1 gates on, lexicon + ``deepgram`` profile attached.

    Builds a :class:`~app.config.Settings` with the three Phase-1 gate flags
    enabled (the ``khaya``/``hybrid`` provider mechanism stays off so the
    ``deepgram`` profile — the task 1.1 defaults — is resolved), runs it through
    ``clamp_ranges`` exactly as startup does, attaches the bundled
    English_Lexicon (Req 2.5-2.8), and resolves the ``deepgram`` provider
    profile (task 2.12.3). The Sitting_Scope and LLM Veto flags stay disabled —
    this harness measures the rule-stage gates only.
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


# ---------------------------------------------------------------------------
# Span → character offset → correction lookup
# ---------------------------------------------------------------------------


def _span_char_start(span: CorpusSpan) -> int:
    """Character offset of the Span's first token within ``enclosing_text``.

    The enclosing text is a single-space join of the Word tokens, so the offset
    of the token at ``span_start`` is the summed length of the preceding tokens
    plus one space each. Uses the ``word`` field of the covered Word dicts,
    which are the exact tokens joined into ``enclosing_text``.
    """
    tokens = [w.get("word", "") for w in span.words]
    offset = 0
    for i in range(span.span_start):
        offset += len(tokens[i]) + 1  # token + the single joining space
    return offset


def _correction_covering(
    corrections: list[TextCorrection], char_start: int
) -> TextCorrection | None:
    """Return the correction whose character range covers ``char_start``, if any.

    A :class:`~app.correction.engine.TextCorrection` records the character range
    ``[start_char, end_char)`` of the original Span it replaced. The Span under
    test starts at ``char_start``; a correction covers it when its range
    contains that offset. Only one correction can cover a given offset because
    the engine consumes tokens as it applies them.
    """
    for c in corrections:
        if c.start_char <= char_start < c.end_char:
            return c
    return None


@dataclass(frozen=True)
class SpanOutcome:
    """The engine's decision on one Span under one config."""

    span: CorpusSpan
    corrected: bool
    replacement: str | None
    strategy: str | None

    @property
    def is_true_positive(self) -> bool:
        """A Positive_Set Span corrected to its labelled canonical (Req 10.5, 10.6)."""
        return (
            self.span.label is not None
            and self.corrected
            and self.replacement == self.span.label
        )

    @property
    def is_false_positive(self) -> bool:
        """A correction that should not have happened (Req 10.6).

        For a Negative_Set Span, any correction is a false positive. For a
        Positive_Set Span, a correction to a *different* entity than the label
        is a false positive too.
        """
        if not self.corrected:
            return False
        if self.span.label is None:
            return True
        return self.replacement != self.span.label

    @property
    def is_false_negative(self) -> bool:
        """A Positive_Set Span the engine failed to correct to its label (Req 10.5)."""
        return self.span.label is not None and not self.is_true_positive


def evaluate_span(
    span: CorpusSpan, gate_context: GateContext, snapshot: DatasetSnapshot
) -> SpanOutcome:
    """Run the engine over one Span's enclosing text and classify the outcome.

    Runs :func:`~app.correction.engine.correct_text` (the rule-stage entry
    point) over ``span.enclosing_text`` with ``span.words`` supplied so the
    Confidence_Gate can derive Span_Confidence, then looks up whether a
    correction covers the Span's first-token offset.
    """
    result = correct_text(
        span.enclosing_text,
        snapshot.index,
        snapshot,
        min_confidence=_MIN_CONFIDENCE,
        fuzzy_score_cutoff=_FUZZY_SCORE_CUTOFF,
        min_candidate_length=_MIN_CANDIDATE_LENGTH,
        gate_context=gate_context,
        words=list(span.words),
    )
    char_start = _span_char_start(span)
    covering = _correction_covering(result.corrections, char_start)
    if covering is None:
        return SpanOutcome(span=span, corrected=False, replacement=None, strategy=None)
    return SpanOutcome(
        span=span,
        corrected=True,
        replacement=covering.replacement,
        strategy=covering.strategy,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangedNegative:
    """Diagnostic for a Negative_Set Span the engine changed (Req 10.7)."""

    original: str
    replacement: str
    config: str
    strategy: str


@dataclass
class ConfigMetrics:
    """Precision/recall and diagnostics for one configuration (Req 10.6, 10.7)."""

    config: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    negative_total: int = 0
    negative_unchanged: int = 0
    positive_total: int = 0
    applied_by_strategy: dict[str, int] = field(default_factory=dict)
    changed_negatives: list[ChangedNegative] = field(default_factory=list)
    # Former Block_List tokens (a subset of the Negative_Set) the config changed.
    changed_blocklist_tokens: list[ChangedNegative] = field(default_factory=list)

    def _bump_strategy(self, strategy: str | None) -> None:
        key = strategy or "?"
        self.applied_by_strategy[key] = self.applied_by_strategy.get(key, 0) + 1

    @property
    def precision(self) -> float:
        """TP / (TP + FP); 1.0 when no correction was applied (Req 10.6)."""
        applied = self.true_positives + self.false_positives
        if applied == 0:
            return 1.0
        return self.true_positives / applied

    @property
    def recall(self) -> float:
        """TP / positive_total; 1.0 when the Positive_Set is empty (Req 10.5, 10.6)."""
        if self.positive_total == 0:
            return 1.0
        return self.true_positives / self.positive_total

    @property
    def negative_unchanged_rate(self) -> float:
        """Fraction of Negative_Set Spans left unchanged (Req 10.4)."""
        if self.negative_total == 0:
            return 1.0
        return self.negative_unchanged / self.negative_total


@dataclass(frozen=True)
class MeasurementReport:
    """The full Baseline-vs-gated report the harness produces (Req 10.6)."""

    baseline: ConfigMetrics
    gated: ConfigMetrics
    # Former Block_List tokens, so the harness can score the Req 11.2 subset.
    blocklist_tokens: frozenset[str]

    def summary_lines(self) -> list[str]:
        """Human-readable one-line-per-config summary for failure diagnostics."""
        lines: list[str] = []
        for m in (self.baseline, self.gated):
            lines.append(
                f"[{m.config}] precision={m.precision:.4f} recall={m.recall:.4f} "
                f"neg_unchanged={m.negative_unchanged}/{m.negative_total} "
                f"({m.negative_unchanged_rate:.4f}) "
                f"TP={m.true_positives} FP={m.false_positives} FN={m.false_negatives}"
            )
        return lines


def _blocklist_token_set() -> frozenset[str]:
    """The lowercased former Block_List tokens (Req 11.1) for the 11.2 subset check."""
    # Local import keeps the module import graph flat and avoids a cycle.
    from tests.evaluation.blocklist_fixtures import former_blocklist_tokens

    return frozenset(t.lower() for t in former_blocklist_tokens())


def _measure_config(
    config_name: str,
    gate_context: GateContext,
    corpus: EvaluationCorpus,
    snapshot: DatasetSnapshot,
    blocklist_tokens: frozenset[str],
) -> ConfigMetrics:
    """Run every corpus Span through one config and aggregate the metrics."""
    metrics = ConfigMetrics(config=config_name)
    metrics.negative_total = len(corpus.negative_set)
    metrics.positive_total = len(corpus.positive_set)

    for span in corpus.negative_set:
        outcome = evaluate_span(span, gate_context, snapshot)
        if outcome.corrected:
            metrics.false_positives += 1
            metrics._bump_strategy(outcome.strategy)
            changed = ChangedNegative(
                original=span.text,
                replacement=outcome.replacement or "",
                config=config_name,
                strategy=outcome.strategy or "?",
            )
            metrics.changed_negatives.append(changed)
            if span.text.lower() in blocklist_tokens:
                metrics.changed_blocklist_tokens.append(changed)
        else:
            metrics.negative_unchanged += 1

    for span in corpus.positive_set:
        outcome = evaluate_span(span, gate_context, snapshot)
        if outcome.is_true_positive:
            metrics.true_positives += 1
            metrics._bump_strategy(outcome.strategy)
        else:
            if outcome.is_false_positive:
                # Corrected to the WRONG entity: counts against precision too.
                metrics.false_positives += 1
                metrics._bump_strategy(outcome.strategy)
            metrics.false_negatives += 1

    return metrics


def measure_precision_recall() -> MeasurementReport:
    """Measure precision/recall for Baseline and gated over the corpus (Req 10.6).

    Loads the Evaluation_Corpus and the fixture Dataset_Cache snapshot (empty
    Block_List, Req 11.2), then runs every Span through the real
    :func:`~app.correction.engine.correct_text` under the Baseline and gated
    configs in this one process (Req 10.10). Returns the assembled
    :class:`MeasurementReport`.
    """
    corpus = load_evaluation_corpus()
    snapshot = build_fixture_snapshot()
    blocklist_tokens = _blocklist_token_set()

    baseline = _measure_config(
        "baseline", baseline_gate_context(), corpus, snapshot, blocklist_tokens
    )
    gated = _measure_config(
        "gated", gated_gate_context(), corpus, snapshot, blocklist_tokens
    )
    return MeasurementReport(
        baseline=baseline, gated=gated, blocklist_tokens=blocklist_tokens
    )
