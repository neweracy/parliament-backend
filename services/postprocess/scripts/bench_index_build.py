"""Benchmark the Match_Index build at the full Dataset_Source record count.

Outputs a single JSON object on stdout with the measurement results.
Uses median-of-5 protocol: 2 discarded warm-ups, 5 measured runs.

Usage:
    python3 services/postprocess/scripts/bench_index_build.py
"""

from __future__ import annotations

import json
import random
import sys
import time

# Add the service root to the path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.datasets.index import build_index  # noqa: E402
from app.models.entities import EntityKind, EntityRecord, EntityType  # noqa: E402

# ---------------------------------------------------------------------------
# Synthetic dataset generation (matches test_performance.py)
# ---------------------------------------------------------------------------

_RNG = random.Random(42)

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
    "Emmanuel", "Elizabeth", "Daniel", "Rebecca", "Joseph", "Sarah", "Isaac",
    "Martha", "Abraham", "Esther", "David", "Ruth", "Moses", "Hannah",
]

_LAST_NAMES = [
    "Mensah", "Asante", "Boateng", "Osei", "Agyeman", "Owusu", "Amponsah",
    "Appiah", "Ankrah", "Darko", "Bonsu", "Frimpong", "Gyasi", "Kumi",
    "Yeboah", "Amoako", "Baffour", "Danquah", "Sarpong", "Tetteh",
    "Amankwa", "Acheampong", "Adomako", "Afriyie", "Antwi", "Baah",
    "Baidoo", "Boakye", "Dufie", "Nyarko",
]

_LOCATION_PARTS = [
    "Ningo", "Prampram", "Assin", "Fosu", "Kwahu", "Afram", "Obuasi",
    "Techiman", "Sunyani", "Dormaa", "Ahanta", "Jomoro", "Ellembelle",
    "Tarkwa", "Nzema", "Ahafo", "Bono", "Savannah", "Oti", "Volta",
    "Krachi", "Nkwanta", "Kadjebi", "Jasikan", "Hohoe", "Kpando",
    "Akatsi", "Ketu", "Adaklu", "Agortime",
]


def _random_person_name() -> str:
    parts = _RNG.randint(2, 3)
    if parts == 2:
        return f"{_RNG.choice(_FIRST_NAMES)} {_RNG.choice(_LAST_NAMES)}"
    return f"{_RNG.choice(_FIRST_NAMES)} {_RNG.choice(_LAST_NAMES)}-{_RNG.choice(_LAST_NAMES)}"


def _random_location_name() -> str:
    parts = _RNG.randint(1, 2)
    if parts == 1:
        return _RNG.choice(_LOCATION_PARTS)
    return f"{_RNG.choice(_LOCATION_PARTS)}-{_RNG.choice(_LOCATION_PARTS)}"


def _random_party_name() -> str:
    prefixes = ["National", "United", "People's", "Progressive", "Democratic"]
    suffixes = ["Congress", "Party", "Alliance", "Movement", "Front"]
    return f"{_RNG.choice(prefixes)} {_RNG.choice(prefixes)} {_RNG.choice(suffixes)}"


def _generate_aliases(canonical: str) -> list[str]:
    """Generate aliases for a canonical name."""
    lower = canonical.lower()
    fused = lower.replace(" ", "").replace("-", "")
    aliases = [lower]
    if fused != lower:
        aliases.append(fused)
    parts = canonical.split()
    if len(parts) > 1:
        aliases.append(parts[-1].lower())
    return aliases


def _generate_dataset(record_count: int = 5000) -> list[EntityRecord]:
    """Generate a synthetic dataset matching the full Dataset_Source record count."""
    records: list[EntityRecord] = []

    for i in range(record_count):
        kind, etype = _ENTITY_TYPES[i % len(_ENTITY_TYPES)]
        source, source_rank = _SOURCE_MAP[kind]

        if kind == EntityKind.person:
            canonical = _random_person_name()
        elif kind == EntityKind.party:
            canonical = _random_party_name()
        else:
            canonical = _random_location_name()

        aliases = _generate_aliases(canonical)

        party_abbr = None
        if kind == EntityKind.party:
            words = canonical.split()
            party_abbr = "".join(w[0].upper() for w in words[:3])

        records.append(
            EntityRecord(
                canonical=canonical,
                entity_kind=kind,
                entity_type=etype,
                aliases=aliases,
                source=source,
                source_rank=source_rank,
                party=party_abbr,
            )
        )

    return records


def main() -> None:
    """Benchmark Match_Index build and output JSON."""
    records = _generate_dataset(record_count=5000)

    # Warm-up runs (discarded)
    for _ in range(2):
        build_index(records)

    # Measured runs
    runs_ms: list[float] = []
    for _ in range(5):
        start = time.perf_counter()
        build_index(records)
        elapsed_ms = (time.perf_counter() - start) * 1000
        runs_ms.append(round(elapsed_ms, 4))

    sorted_runs = sorted(runs_ms)
    median_ms = sorted_runs[2]  # Element 2 of sorted 5

    result = {
        "runs_ms": runs_ms,
        "median_ms": median_ms,
        "record_count": len(records),
    }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
