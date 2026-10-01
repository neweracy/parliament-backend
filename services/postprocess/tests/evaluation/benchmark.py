"""Deterministic Benchmark_Set generator and loader (Req 15.2).

The Benchmark_Set drives the Rule_Latency benchmark (task 6.4.3). Requirement
15.2 pins its shape precisely:

* at least 20 and at most 50 transcript fixtures;
* identical on every run;
* each carrying at least 2,000 and at most 20,000 Words;
* a Word_Confidence present on at least 90% of those Words;
* a Word_Confidence below High_Confidence_Threshold (0.90) on at least 25% of
  those Words.

Determinism (Req 15.2 "identical on every run") is guaranteed by construction,
not asserted after the fact:

* a compact committed *seed vocabulary* (ordinary parliamentary English plus a
  handful of entity-adjacent tokens) is the only source text — no external
  corpus, no network, no AWS (Req 10.8);
* expansion uses a :class:`random.Random` seeded with a FIXED per-fixture seed
  derived from the constant :data:`BENCHMARK_SEED` and the fixture index, so the
  same pseudo-random stream is replayed on every call;
* the loader is ``lru_cache``-d and returns the same immutable objects, so two
  successive calls return identical data.

The generated text is synthesized parliamentary-style filler with NO PII beyond
the public figure surnames already in the committed datasets.

This module defines the Benchmark_Set DATA and its loader only; the latency
benchmark that consumes it is task 6.4.3.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache

# Fixed master seed — the single knob that makes the whole Benchmark_Set
# reproducible. Changing it regenerates every fixture; leaving it fixed (the
# committed value) reproduces the identical set on every run (Req 15.2).
BENCHMARK_SEED: int = 20240117

# Benchmark_Set shape bounds (Req 15.2).
NUM_FIXTURES: int = 24  # in [20, 50]
MIN_WORDS: int = 2_000
MAX_WORDS: int = 20_000
# Fixed per-fixture word count, comfortably inside [2000, 20000]. Chosen small
# enough that the whole set builds fast yet large enough to exercise the
# rule-stage cost the benchmark measures.
_WORDS_PER_FIXTURE: int = 2_400

HIGH_CONFIDENCE_THRESHOLD: float = 0.90

# Confidence-distribution targets (Req 15.2). We place a confidence on >= 95%
# of Words (comfortably over the 90% floor) and put >= 30% of Words below the
# High_Confidence_Threshold (comfortably over the 25% floor), leaving margin so
# the fixtures satisfy the requirement without sitting on the boundary.
_CONFIDENCE_PRESENT_FRACTION: float = 0.95
_LOW_CONFIDENCE_FRACTION: float = 0.30

# ---------------------------------------------------------------------------
# Committed seed vocabulary (compact; the only source text)
# ---------------------------------------------------------------------------
#
# Ordinary parliamentary English plus a few entity-adjacent surnames drawn from
# the committed datasets. No PII beyond public-figure surnames.

_SEED_VOCAB: tuple[str, ...] = (
    "the", "honourable", "member", "for", "constituency", "raised", "matter",
    "before", "this", "house", "mister", "speaker", "on", "point", "of",
    "order", "committee", "reviewed", "report", "and", "moved", "motion",
    "government", "minority", "majority", "leader", "budget", "statement",
    "policy", "development", "infrastructure", "education", "health",
    "service", "community", "district", "region", "national", "assembly",
    "debate", "second", "reading", "amendment", "clause", "bill", "question",
    "answer", "response", "consideration", "recommendation", "proposal",
    "project", "programme", "sitting", "today", "yesterday", "morning",
    "afternoon", "recorded", "hansard", "chamber", "vote", "division",
    "quorum", "adjournment", "resumption", "prayers", "papers", "laid",
    "table", "referred", "select", "standing", "procedure", "regulation",
    "provision", "clarification", "assessment", "engagement", "commitment",
    "outcome", "objective", "framework", "governance", "authority",
    "administration", "constituents", "electorate", "citizens", "people",
    "Ghana", "Accra", "Kumasi", "Tamale", "Bagbin", "Mahama", "Rawlings",
    "Iddrisu", "Forson", "Ablakwa", "Nikpe", "Lartey", "Sege", "Bole",
)


@dataclass(frozen=True)
class BenchmarkTranscript:
    """One deterministic Benchmark_Set transcript fixture (Req 15.2)."""

    fixture_id: str
    transcript: str
    words: tuple[dict, ...]

    @property
    def word_count(self) -> int:
        return len(self.words)


def _generate_words(rng: random.Random, count: int) -> list[dict]:
    """Generate *count* Word dicts from the seed vocab with the RNG stream.

    The confidence distribution is placed deterministically: a shuffled subset
    of positions (derived from *rng*) receive a confidence, and within those a
    deterministic proportion are drawn below the High_Confidence_Threshold.
    Timings are a fixed function of position.
    """
    present_count = math.ceil(count * _CONFIDENCE_PRESENT_FRACTION)
    low_count = math.ceil(count * _LOW_CONFIDENCE_FRACTION)

    # Which positions carry a confidence, and which of those are "low". Both
    # sets are derived from the seeded RNG so they are reproducible.
    positions = list(range(count))
    rng.shuffle(positions)
    present_positions = set(positions[:present_count])
    # Low-confidence positions are a subset of the present positions.
    present_list = sorted(present_positions)
    rng.shuffle(present_list)
    low_positions = set(present_list[:low_count])

    words: list[dict] = []
    cursor = 0.0
    for i in range(count):
        token = _SEED_VOCAB[rng.randrange(len(_SEED_VOCAB))]
        w: dict = {
            "word": token,
            "start": round(cursor, 3),
            "end": round(cursor + 0.28, 3),
            "punctuated_word": token,
        }
        if i in present_positions:
            if i in low_positions:
                # Below High_Confidence_Threshold: [0.40, 0.89].
                w["confidence"] = round(0.40 + rng.random() * 0.49, 4)
            else:
                # At/above threshold: [0.90, 1.00].
                w["confidence"] = round(0.90 + rng.random() * 0.10, 4)
        cursor += 0.30
        words.append(w)
    return words


@lru_cache(maxsize=1)
def load_benchmark_set() -> tuple[BenchmarkTranscript, ...]:
    """Return the deterministic Benchmark_Set (Req 15.2).

    Builds ``NUM_FIXTURES`` transcript fixtures, each with ``_WORDS_PER_FIXTURE``
    Words expanded from the committed seed vocabulary using a
    :class:`random.Random` seeded with a FIXED per-fixture seed. The result is
    cached, so two successive calls return the identical tuple of the identical
    immutable transcripts — no random-per-run data.

    Each fixture satisfies Req 15.2: word count in [2000, 20000], a confidence
    present on >= 90% of Words, and a confidence below the
    High_Confidence_Threshold on >= 25% of Words.
    """
    fixtures: list[BenchmarkTranscript] = []
    for idx in range(NUM_FIXTURES):
        rng = random.Random(BENCHMARK_SEED + idx)
        words = _generate_words(rng, _WORDS_PER_FIXTURE)
        transcript = " ".join(w["word"] for w in words)
        fixtures.append(
            BenchmarkTranscript(
                fixture_id=f"benchmark-{idx:03d}",
                transcript=transcript,
                words=tuple(words),
            )
        )
    return tuple(fixtures)


def benchmark_confidence_stats(fixture: BenchmarkTranscript) -> tuple[float, float]:
    """Return ``(present_fraction, below_threshold_fraction)`` for a fixture.

    Helper for the benchmark and the determinism test to assert the Req 15.2
    confidence-distribution floors without duplicating the arithmetic.
    """
    total = len(fixture.words)
    if total == 0:
        return (0.0, 0.0)
    present = [w for w in fixture.words if isinstance(w.get("confidence"), (int, float))]
    present_fraction = len(present) / total
    below = sum(
        1 for w in present if float(w["confidence"]) < HIGH_CONFIDENCE_THRESHOLD
    )
    below_fraction = below / total
    return (present_fraction, below_fraction)
