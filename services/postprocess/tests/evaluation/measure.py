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

Per-provider reporting (task 2.12.5)
------------------------------------
Because the gate-rejection metric dimensions stay closed (task 2.12.4 keeps
``provider`` out of the metric per Req 13.3), provider-level precision/recall
visibility comes through the harness REPORT. :func:`measure_by_provider` measures
the SAME corpus under each of ``deepgram``, ``khaya``, and ``hybrid`` — each with
its own gate profile (``provider_profiles_enabled=True``) and its own realistic
per-word-confidence condition (``deepgram`` keeps confidence; ``khaya``/``hybrid``
strip it so Span_Confidence is Unknown) — so the ``khaya``/``hybrid`` calibration
(task 2.12.3) is validated separately from ``deepgram``. This is report-only; no
metric dimension is added.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.config import Settings, clamp_ranges, provider_profiles
from app.correction.engine import TextCorrection, correct_text
from app.correction.gates import GateContext
from app.correction.provider_profiles import SUPPORTED_PROVIDERS
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


# ---------------------------------------------------------------------------
# Per-provider reporting (task 2.12.5)
# ---------------------------------------------------------------------------
#
# The gate-rejection metric dimensions stay CLOSED (task 2.12.4 keeps ``provider``
# OUT of the metric per Req 13.3), so provider-level precision/recall visibility
# has to come through the HARNESS REPORT, not through a new metric dimension.
# This section measures the SAME corpus under each provider's gate profile and
# reports precision/recall per provider, so the ``khaya``/``hybrid`` calibration
# (task 2.12.3) can be validated separately from ``deepgram``.
#
# Two things differ between providers, and the harness reproduces BOTH so each
# provider is measured under its own realistic condition:
#
# 1. **Gate profile.** With ``provider_profiles_enabled=True`` (task 2.12.2), each
#    provider resolves a distinct :class:`ProviderGateProfile`: ``khaya``/``hybrid``
#    set ``lexicon_gate_reject_unknown=False`` (task 2.12.3), while ``deepgram``
#    keeps the Req 2.6 reject policy. With the flag OFF every provider would
#    resolve the ``deepgram`` profile, so all three would be identical — hence the
#    per-provider contexts are built from a Settings that enables the flag.
#
# 2. **Confidence condition.** Khaya returns text only (no per-word confidence),
#    so on a ``khaya``/``hybrid`` transcript Span_Confidence is Unknown for every
#    Span (Req 1.5, 1.10). To validate the Unknown-confidence calibration end to
#    end, the harness measures each provider under that provider's realistic
#    condition: ``deepgram`` keeps the corpus Words as-is (confidence present);
#    ``khaya``/``hybrid`` evaluate the SAME corpus Spans with per-word confidence
#    STRIPPED, so Span_Confidence is Unknown.


def provider_gate_context(provider: str) -> GateContext:
    """Build the gated context for *provider* with per-provider profiles ON.

    Enables the three Phase-1 gate flags exactly like :func:`gated_gate_context`,
    but additionally turns on ``provider_profiles_enabled`` so that
    ``provider_profiles(settings)`` resolves a DISTINCT profile per provider
    (task 2.12.2): otherwise ``khaya``/``hybrid`` would collapse to the
    ``deepgram`` profile and the per-provider report would be meaningless. The
    provider's profile — including its ``lexicon_gate_reject_unknown`` policy
    (task 2.12.3) — rides on the returned :class:`GateContext`.

    For ``provider="deepgram"`` the resolved profile reproduces the task 1.1
    defaults, so a ``deepgram`` measurement under this context matches the
    existing :func:`gated_gate_context` measurement (same profile, same gates).
    """
    settings = clamp_ranges(
        Settings(
            lexicon_gate_enabled=True,
            evidence_confidence_enabled=True,
            context_gate_enabled=True,
            sitting_scope_enabled=False,
            llm_veto_enabled=False,
            provider_profiles_enabled=True,
        )
    )
    profile = provider_profiles(settings).get(provider)
    return GateContext.from_settings(
        settings,
        lexicon=load_fixture_lexicon(),
        provider=provider,
        profile=profile,
    )


def _strip_word_confidence(words: tuple[dict, ...]) -> tuple[dict, ...]:
    """Return copies of *words* with any ``confidence`` field removed.

    Reflects the Khaya/hybrid real-world condition: the provider returns text
    only, so no per-word confidence is present and Span_Confidence is Unknown
    for every Span (Req 1.5). Every other field (``word``, ``start``, ``end``,
    ``punctuated_word``) is preserved so the Span still aligns to the enclosing
    text and the character-offset lookup is unchanged.
    """
    stripped: list[dict] = []
    for w in words:
        copy = {k: v for k, v in w.items() if k != "confidence"}
        stripped.append(copy)
    return tuple(stripped)


def prepare_corpus_span_for_provider(span: CorpusSpan, provider: str) -> CorpusSpan:
    """Adapt *span* to *provider*'s realistic per-word-confidence condition.

    * ``deepgram`` — the Words are kept as-is (confidence present), matching a
      provider that supplies per-word confidence.
    * ``khaya`` / ``hybrid`` — the Words have their ``confidence`` stripped so
      Span_Confidence is Unknown (Req 1.5, 1.10), matching a text-only provider.

    Returns a copy tagged with the provider; the original ``enclosing_text``,
    ``span_start``, ``label`` and token text are untouched, so the same Span is
    measured under each provider's condition — only the confidence signal (and
    the ``provider`` tag) changes.
    """
    if provider == "deepgram":
        return replace(span, provider=provider)
    return replace(
        span, words=_strip_word_confidence(span.words), provider=provider
    )


@dataclass(frozen=True)
class ProviderReport:
    """Per-provider precision/recall report (task 2.12.5, Req 10.6, 10.7).

    Maps each supported provider to the :class:`ConfigMetrics` measured for it
    under that provider's gate profile and confidence condition. This is a
    REPORT-ONLY structure: it adds no metric dimension (Req 13.3 unchanged) — it
    exists so the operator can see provider-level precision/recall that the
    closed gate-rejection metric cannot carry.
    """

    by_provider: dict[str, ConfigMetrics]
    blocklist_tokens: frozenset[str]

    def summary_lines(self) -> list[str]:
        """One human-readable line per provider for failure diagnostics."""
        lines: list[str] = []
        for provider in SUPPORTED_PROVIDERS:
            m = self.by_provider[provider]
            lines.append(
                f"[{m.config}] precision={m.precision:.4f} recall={m.recall:.4f} "
                f"neg_unchanged={m.negative_unchanged}/{m.negative_total} "
                f"({m.negative_unchanged_rate:.4f}) "
                f"TP={m.true_positives} FP={m.false_positives} "
                f"FN={m.false_negatives}"
            )
        return lines


def _measure_provider(
    provider: str,
    corpus: EvaluationCorpus,
    snapshot: DatasetSnapshot,
    blocklist_tokens: frozenset[str],
) -> ConfigMetrics:
    """Run every corpus Span through *provider*'s context and aggregate metrics.

    Each Span is first adapted to the provider's confidence condition
    (:func:`prepare_corpus_span_for_provider`) before it is evaluated under the
    provider's gated context (:func:`provider_gate_context`). Reuses the same
    ``ConfigMetrics`` aggregation the Baseline-vs-gated path uses, so the
    per-gate/per-strategy counting is identical — only the config name, gate
    profile, and confidence condition differ per provider.
    """
    gate_context = provider_gate_context(provider)
    metrics = ConfigMetrics(config=provider)
    metrics.negative_total = len(corpus.negative_set)
    metrics.positive_total = len(corpus.positive_set)

    for span in corpus.negative_set:
        prepared = prepare_corpus_span_for_provider(span, provider)
        outcome = evaluate_span(prepared, gate_context, snapshot)
        if outcome.corrected:
            metrics.false_positives += 1
            metrics._bump_strategy(outcome.strategy)
            changed = ChangedNegative(
                original=span.text,
                replacement=outcome.replacement or "",
                config=provider,
                strategy=outcome.strategy or "?",
            )
            metrics.changed_negatives.append(changed)
            if span.text.lower() in blocklist_tokens:
                metrics.changed_blocklist_tokens.append(changed)
        else:
            metrics.negative_unchanged += 1

    for span in corpus.positive_set:
        prepared = prepare_corpus_span_for_provider(span, provider)
        outcome = evaluate_span(prepared, gate_context, snapshot)
        if outcome.is_true_positive:
            metrics.true_positives += 1
            metrics._bump_strategy(outcome.strategy)
        else:
            if outcome.is_false_positive:
                metrics.false_positives += 1
                metrics._bump_strategy(outcome.strategy)
            metrics.false_negatives += 1

    return metrics


def measure_by_provider() -> ProviderReport:
    """Measure precision/recall for each ASR provider (task 2.12.5, Req 10.6).

    Measures the SAME Evaluation_Corpus under each of ``deepgram``, ``khaya``,
    and ``hybrid`` — each with its own gate profile
    (``provider_profiles_enabled=True``) and its own realistic per-word
    confidence condition (``deepgram`` keeps confidence; ``khaya``/``hybrid``
    strip it so Span_Confidence is Unknown). Runs the real
    :func:`~app.correction.engine.correct_text` in this one process against the
    fixture Dataset_Cache snapshot (empty Block_List, Req 11.2). Everything is
    offline and deterministic: no network, no AWS, no LLM (Req 10.8).

    Returns a :class:`ProviderReport` carrying one :class:`ConfigMetrics` per
    provider. This is report-only — no metric dimension is added (Req 13.3).
    """
    corpus = load_evaluation_corpus()
    snapshot = build_fixture_snapshot()
    blocklist_tokens = _blocklist_token_set()

    by_provider = {
        provider: _measure_provider(provider, corpus, snapshot, blocklist_tokens)
        for provider in SUPPORTED_PROVIDERS
    }
    return ProviderReport(
        by_provider=by_provider, blocklist_tokens=blocklist_tokens
    )
