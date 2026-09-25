"""Tests that the Evaluation_Harness fixtures are deterministic and spec-compliant.

These tests guard the DATA and its LOADERS created in task 6.4.1 — they do NOT
run the Correction_Engine (that is task 6.4.2) or measure latency (task 6.4.3).
They assert only that the fixtures satisfy the sizes, the confidence
distribution, and the determinism the later harness tasks depend on:

* Evaluation_Corpus sizes and disjointness (Req 10.1-10.3);
* former Block_List snapshot size (Req 11.1);
* every Positive_Set label present in the fixture snapshot (Req 10.3);
* Benchmark_Set shape and confidence distribution (Req 15.2);
* every loader returns identical data on two successive calls (Req 15.2
  "identical on every run").
"""

from __future__ import annotations

from tests.evaluation.benchmark import (
    HIGH_CONFIDENCE_THRESHOLD,
    MAX_WORDS,
    MIN_WORDS,
    benchmark_confidence_stats,
    load_benchmark_set,
)
from tests.evaluation.blocklist_fixtures import load_former_blocklist_fixtures
from tests.evaluation.corpus import load_evaluation_corpus
from tests.evaluation.dataset import (
    build_fixture_snapshot,
    fixture_canonicals,
    load_fixture_lexicon,
)

# ---------------------------------------------------------------------------
# Fixture Dataset_Cache snapshot
# ---------------------------------------------------------------------------


class TestFixtureSnapshot:
    def test_snapshot_builds_with_empty_block_list(self):
        # Req 11.2: the harness snapshot carries an EMPTY block list so gating
        # must handle the former block-list tokens without it.
        snapshot = build_fixture_snapshot()
        assert snapshot.block_list == frozenset()
        assert snapshot.record_count > 0
        # Derived gate indexes are built (same shape as production).
        assert snapshot.index.component_map
        assert snapshot.index.phonetic_fanout

    def test_snapshot_is_deterministic(self):
        # Two calls return the same cached, immutable object.
        assert build_fixture_snapshot() is build_fixture_snapshot()

    def test_lexicon_loads_and_is_active(self):
        # Req 2.1-2.4, 10.8: the bundled lexicon loads offline and is active.
        lexicon = load_fixture_lexicon()
        assert lexicon.loaded is True
        assert len(lexicon) >= 25_000

    def test_lexicon_loader_is_deterministic(self):
        assert load_fixture_lexicon() is load_fixture_lexicon()


# ---------------------------------------------------------------------------
# Former Block_List snapshot (Req 11.1)
# ---------------------------------------------------------------------------


class TestFormerBlocklistFixtures:
    def test_at_least_130_entries(self):
        # Req 11.1: a single Block_List snapshot of at least 130 entries.
        fixtures = load_former_blocklist_fixtures()
        assert len(fixtures) >= 130

    def test_tokens_are_unique(self):
        fixtures = load_former_blocklist_fixtures()
        tokens = [f.token.lower() for f in fixtures]
        assert len(tokens) == len(set(tokens))

    def test_each_entry_carries_token_reason_and_confidence(self):
        # Req 11.1: token, recorded reason (may be empty), evaluation confidence.
        for fx in load_former_blocklist_fixtures():
            assert fx.token
            assert isinstance(fx.reason, str)
            assert 0.0 <= fx.span_confidence <= 1.0

    def test_recorded_false_positives_present(self):
        # The five recorded false positives named in Req 16.13 must be covered.
        tokens = {f.token.lower() for f in load_former_blocklist_fixtures()}
        for word in ("thank", "sage", "district", "transportation", "later"):
            assert word in tokens

    def test_loader_is_deterministic(self):
        assert load_former_blocklist_fixtures() is load_former_blocklist_fixtures()


# ---------------------------------------------------------------------------
# Evaluation_Corpus (Req 10.1-10.3)
# ---------------------------------------------------------------------------


class TestEvaluationCorpus:
    def test_total_at_least_300_spans(self):
        # Req 10.1: at least 300 labelled Spans.
        corpus = load_evaluation_corpus()
        total = len(corpus.negative_set) + len(corpus.positive_set)
        assert total >= 300

    def test_negative_set_at_least_200(self):
        # Req 10.2.
        assert len(load_evaluation_corpus().negative_set) >= 200

    def test_positive_set_at_least_100(self):
        # Req 10.3.
        assert len(load_evaluation_corpus().positive_set) >= 100

    def test_negative_and_positive_disjoint_by_text(self):
        # Req 10.2: no Negative_Set Span also appears in the Positive_Set.
        corpus = load_evaluation_corpus()
        neg = {s.text.lower() for s in corpus.negative_set}
        pos = {s.text.lower() for s in corpus.positive_set}
        assert neg.isdisjoint(pos)

    def test_negatives_are_unlabelled_positives_are_labelled(self):
        corpus = load_evaluation_corpus()
        assert all(s.label is None for s in corpus.negative_set)
        assert all(s.label for s in corpus.positive_set)

    def test_every_positive_label_present_in_snapshot(self):
        # Req 10.3: every labelled canonical entity is present in the snapshot.
        canonicals = fixture_canonicals()
        for s in load_evaluation_corpus().positive_set:
            assert s.label in canonicals, f"label not in snapshot: {s.label}"

    def test_spans_carry_enclosing_text_and_words(self):
        # Req 10.1: each Span carries its enclosing transcript text and the
        # Word_Confidence / punctuated_word of covered Words where supplied.
        corpus = load_evaluation_corpus()
        for s in (*corpus.negative_set, *corpus.positive_set):
            assert s.enclosing_text
            assert s.words
            covered = s.words[s.span_start]
            assert "confidence" in covered
            assert "punctuated_word" in covered

    def test_former_blocklist_tokens_in_negative_set(self):
        # Req 11.1: the former Block_List tokens are carried as Negative_Set
        # fixtures (minus any that collide with a Positive_Set span text).
        corpus = load_evaluation_corpus()
        neg_texts = {s.text.lower() for s in corpus.negative_set}
        pos_texts = {s.text.lower() for s in corpus.positive_set}
        for fx in load_former_blocklist_fixtures():
            tok = fx.token.lower()
            assert tok in neg_texts or tok in pos_texts

    def test_loader_is_deterministic(self):
        # Req 15.2 spirit: identical data on repeated calls.
        assert load_evaluation_corpus() is load_evaluation_corpus()


# ---------------------------------------------------------------------------
# Benchmark_Set (Req 15.2)
# ---------------------------------------------------------------------------


class TestBenchmarkSet:
    def test_fixture_count_in_range(self):
        # Req 15.2: at least 20 and at most 50 transcript fixtures.
        fixtures = load_benchmark_set()
        assert 20 <= len(fixtures) <= 50

    def test_each_fixture_word_count_in_range(self):
        # Req 15.2: each carrying at least 2,000 and at most 20,000 Words.
        for fx in load_benchmark_set():
            assert MIN_WORDS <= fx.word_count <= MAX_WORDS

    def test_confidence_coverage_at_least_90_percent(self):
        # Req 15.2: a Word_Confidence present on at least 90% of Words.
        for fx in load_benchmark_set():
            present_fraction, _ = benchmark_confidence_stats(fx)
            assert present_fraction >= 0.90

    def test_low_confidence_at_least_25_percent(self):
        # Req 15.2: below High_Confidence_Threshold on at least 25% of Words.
        for fx in load_benchmark_set():
            _, below_fraction = benchmark_confidence_stats(fx)
            assert below_fraction >= 0.25

    def test_transcript_matches_words(self):
        for fx in load_benchmark_set():
            assert fx.transcript.split() == [w["word"] for w in fx.words]

    def test_confidence_values_are_clamped(self):
        for fx in load_benchmark_set():
            for w in fx.words:
                conf = w.get("confidence")
                if conf is not None:
                    assert 0.0 <= conf <= 1.0
                    if conf < HIGH_CONFIDENCE_THRESHOLD:
                        assert conf >= 0.40

    def test_loader_is_deterministic_identity(self):
        # Cached: same object on repeated calls.
        assert load_benchmark_set() is load_benchmark_set()

    def test_two_successive_calls_return_identical_data(self):
        # Req 15.2 "identical on every run": compare by value, not identity, so
        # the guarantee holds even if the cache were cleared between calls.
        first = load_benchmark_set.__wrapped__()  # bypass the cache
        second = load_benchmark_set.__wrapped__()
        assert len(first) == len(second)
        for a, b in zip(first, second, strict=True):
            assert a.fixture_id == b.fixture_id
            assert a.transcript == b.transcript
            assert a.words == b.words
