"""Benchmark the PY_Correction_Engine at 100/1000/10000 words.

Outputs a single JSON object on stdout with the measurement results.
Uses the same seeded 70/25/5 token distribution as the JS harness.

Usage:
    python3 services/postprocess/scripts/bench_rule_stage.py
"""

from __future__ import annotations

import json
import random
import sys
import time

# Add the service root to the path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.correction.engine import correct_text, correct_words  # noqa: E402
from app.datasets.index import build_index  # noqa: E402
from app.models.entities import EntityKind, EntityRecord, EntityType  # noqa: E402
from app.datasets.cache import DatasetSnapshot  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

# ---------------------------------------------------------------------------
# Seeded transcript generator (mirrors bench/harness.js)
# ---------------------------------------------------------------------------

_RNG = random.Random(42)

SHORT_WORDS = [
    "the", "of", "and", "to", "in", "a", "is", "it", "was", "for",
    "on", "are", "be", "at", "one", "or", "had", "not", "but", "all",
    "we", "do", "how", "so", "an", "if", "no", "up", "out", "its",
    "my", "he", "as", "by", "go", "mr", "us", "has", "him", "her",
    "may", "did", "you", "our", "she", "can", "sir", "two", "new",
    "now", "say", "get", "let", "see", "per", "set", "own", "put",
]

MEDIUM_WORDS = [
    "this", "from", "that", "with", "have", "been", "were", "will",
    "also", "more", "very", "some", "when", "them", "than", "made",
    "time", "year", "what", "much", "like", "said", "just", "only",
    "most", "such", "take", "many", "must", "over", "make", "each",
    "come", "last", "long", "same", "well", "back", "even", "good",
    "give", "work", "call", "need", "want", "look", "help", "here",
]

ENTITY_ADJACENT = [
    "house", "chair", "right", "order", "point", "state", "local",
    "paper", "water", "issue", "floor", "voice", "clear", "place",
    "after", "under", "about", "above", "bring", "whole", "eight",
]

# Entity types to cycle through for dataset generation
_ENTITY_TYPES = [
    (EntityKind.location, EntityType.constituency),
    (EntityKind.location, EntityType.city),
    (EntityKind.location, EntityType.supplementary),
    (EntityKind.person, EntityType.mp),
    (EntityKind.person, EntityType.minister),
    (EntityKind.party, EntityType.party),
]

_SOURCE_MAP = {
    EntityKind.location: ("supplementary", 0),
    EntityKind.person: ("all_persons", 3),
    EntityKind.party: ("all_parties", 5),
}

_FIRST_NAMES = [
    "Kwame", "Ama", "Kofi", "Akua", "Kwesi", "Adjoa", "Yaw", "Aba",
    "Nana", "Abena", "John", "Grace", "Samuel", "Mary", "Francis", "Rita",
]

_LAST_NAMES = [
    "Mensah", "Asante", "Boateng", "Osei", "Agyeman", "Owusu", "Amponsah",
    "Appiah", "Ankrah", "Darko", "Bonsu", "Frimpong", "Gyasi", "Kumi",
]

_LOCATION_PARTS = [
    "Ningo", "Prampram", "Assin", "Fosu", "Kwahu", "Afram", "Obuasi",
    "Techiman", "Sunyani", "Dormaa", "Ahanta", "Jomoro", "Ellembelle",
    "Tarkwa", "Nzema", "Ahafo", "Bono", "Savannah", "Oti", "Volta",
]


def _generate_transcript(word_count: int, rng: random.Random) -> tuple[str, list[dict]]:
    """Generate a fixed-seed synthetic transcript."""
    words_list: list[str] = []
    for _ in range(word_count):
        r = rng.random()
        if r < 0.70:
            words_list.append(rng.choice(SHORT_WORDS))
        elif r < 0.95:
            words_list.append(rng.choice(MEDIUM_WORDS))
        else:
            words_list.append(rng.choice(ENTITY_ADJACENT))

    word_dicts: list[dict] = []
    pos = 0.0
    for w in words_list:
        duration = 0.2 + rng.random() * 0.3
        word_dicts.append({
            "word": w,
            "start": round(pos, 3),
            "end": round(pos + duration, 3),
            "confidence": round(0.5 + rng.random() * 0.5, 3),
        })
        pos += duration + 0.05

    transcript = " ".join(words_list)
    return transcript, word_dicts


def _generate_dataset(record_count: int = 500) -> list[EntityRecord]:
    """Generate a synthetic dataset for benchmarking."""
    rng = random.Random(42)
    records: list[EntityRecord] = []

    for i in range(record_count):
        kind, etype = _ENTITY_TYPES[i % len(_ENTITY_TYPES)]
        source, source_rank = _SOURCE_MAP[kind]

        if kind == EntityKind.person:
            canonical = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
        elif kind == EntityKind.party:
            canonical = f"National {rng.choice(_FIRST_NAMES)} Party"
        else:
            canonical = f"{rng.choice(_LOCATION_PARTS)}-{rng.choice(_LOCATION_PARTS)}"

        aliases = [canonical.lower(), canonical.lower().replace(" ", "").replace("-", "")]

        records.append(
            EntityRecord(
                canonical=canonical,
                entity_kind=kind,
                entity_type=etype,
                aliases=aliases,
                source=source,
                source_rank=source_rank,
            )
        )

    return records


def _build_snapshot(records: list[EntityRecord]) -> DatasetSnapshot:
    """Build a DatasetSnapshot from records."""
    index = build_index(records)
    return DatasetSnapshot(
        version="bench-v1",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(timezone.utc),
        index=index,
        block_list=frozenset(["general", "page", "nation", "national"]),
        stopwords=frozenset(["the", "a", "an", "is", "of", "and", "in", "to"]),
        word_stopwords=frozenset([
            "a", "an", "the", "in", "on", "at", "to", "of", "is", "are",
            "was", "were", "be", "and", "or", "but", "for", "by", "with",
        ]),
        title_prefixes=frozenset([
            "honorable", "honourable", "hon", "minister", "mr", "mrs", "dr",
        ]),
    )


def _median_of_5(fn) -> dict:
    """Run 2 warm-ups + 5 measured runs; return runs and median."""
    # Warm-ups
    for _ in range(2):
        fn()

    # Measured runs
    runs_ms: list[float] = []
    for _ in range(5):
        start = time.perf_counter()
        fn()
        elapsed_ms = (time.perf_counter() - start) * 1000
        runs_ms.append(round(elapsed_ms, 4))

    sorted_runs = sorted(runs_ms)
    median_ms = sorted_runs[2]  # Element 2 of sorted 5

    return {"runs_ms": runs_ms, "median_ms": median_ms}


def main() -> None:
    """Benchmark the PY_Correction_Engine and output JSON."""
    # Build dataset and snapshot
    records = _generate_dataset(record_count=500)
    snapshot = _build_snapshot(records)

    word_counts = [100, 1000, 10000]
    results: dict = {}

    for wc in word_counts:
        # Use a fresh RNG per word count for reproducibility
        rng = random.Random(42 + wc)
        transcript, word_dicts = _generate_transcript(wc, rng)

        measurement = _median_of_5(
            lambda t=transcript, s=snapshot: correct_text(t, s.index, s)
        )
        results[str(wc)] = measurement

    print(json.dumps(results))


if __name__ == "__main__":
    main()
