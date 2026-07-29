"""Baseline test fixtures — Behaviour_Equivalent predicate and DatasetSnapshot.

Provides:
- Difference dataclass for structured comparison results
- COMPARED_WORD_FIELDS list matching the JS version
- behaviour_equivalent(actual, expected) -> tuple[bool, list[Difference]]
- dataset_snapshot pytest fixture built from generated dataset JSON files

The behaviour_equivalent predicate mirrors the JS implementation in
test/baseline/behaviour-equivalent.js with identical field list and
identical multiset semantics.

Normalisation rules (same as JS):
1. absent/None equals False for the three boolean flags
   (locationCorrected, entityKind, entityType)
2. Exact float comparison for start/end/confidence
3. Entity summary ordering compared as multiset (sort before comparing)

Requirements: 1.4, 1.5
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.datasets.cache import DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Difference dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Difference:
    """A single field-level difference between actual and expected outputs."""

    field: str
    baseline: Any
    actual: Any


# ---------------------------------------------------------------------------
# Compared word fields — identical to the JS COMPARED_WORD_FIELDS
# ---------------------------------------------------------------------------

COMPARED_WORD_FIELDS: list[str] = [
    "word",
    "start",
    "end",
    "confidence",
    "locationCorrected",
    "entityKind",
    "entityType",
]

"""The three boolean flags where absent (None/missing) is treated as False."""
_BOOLEAN_FLAGS: frozenset[str] = frozenset([
    "locationCorrected",
    "entityKind",
    "entityType",
])


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _normalise_flag_value(value: Any) -> Any:
    """Normalise a boolean-flag field value.

    absent / None / False all normalise to False.
    Any other value is returned as-is.
    """
    if value is None or value is False:
        return False
    return value


def _entity_key(entity: dict[str, Any]) -> tuple[str, str, str, int]:
    """Produce a stable comparison key from an entity dict.

    Returns (name, kind, type, mentions) for multiset comparison.
    """
    return (
        entity.get("name", ""),
        entity.get("kind", ""),
        entity.get("type", ""),
        entity.get("mentions", 0),
    )


# ---------------------------------------------------------------------------
# Behaviour_Equivalent predicate
# ---------------------------------------------------------------------------


def behaviour_equivalent(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[bool, list[Difference]]:
    """Compare two pipeline outputs for Behaviour_Equivalence.

    Two outputs are Behaviour_Equivalent when:
    - Their corrected transcript strings are byte-identical
    - Their corrected word arrays are equal element-wise on COMPARED_WORD_FIELDS
    - Their entity summaries are equal as multisets of (name, kind, type, mentions)

    Normalisation rules applied:
    1. absent/None equals False for locationCorrected, entityKind, entityType
    2. start/end/confidence compare exactly (no epsilon)
    3. Entity arrays compared as multisets (order-independent)

    Parameters
    ----------
    actual : dict
        Pipeline output with keys: transcript, words, entities
    expected : dict
        Baseline output with keys: transcript, words, entities

    Returns
    -------
    tuple[bool, list[Difference]]
        (is_equivalent, differences) — empty list means equivalent.
    """
    differences: list[Difference] = []

    # --- Compare transcripts byte-identically ---
    if actual.get("transcript") != expected.get("transcript"):
        differences.append(Difference(
            field="transcript",
            baseline=expected.get("transcript"),
            actual=actual.get("transcript"),
        ))

    # --- Compare word arrays element-wise ---
    actual_words: list[dict[str, Any]] = actual.get("words") or []
    expected_words: list[dict[str, Any]] = expected.get("words") or []

    if len(actual_words) != len(expected_words):
        differences.append(Difference(
            field="words.length",
            baseline=len(expected_words),
            actual=len(actual_words),
        ))

    # Compare element-wise up to the shorter length
    word_len = min(len(actual_words), len(expected_words))
    for i in range(word_len):
        actual_word = actual_words[i]
        expected_word = expected_words[i]

        for field in COMPARED_WORD_FIELDS:
            actual_val = actual_word.get(field)
            expected_val = expected_word.get(field)

            # Normalisation rule 1: absent equals False for boolean flags
            if field in _BOOLEAN_FLAGS:
                actual_val = _normalise_flag_value(actual_val)
                expected_val = _normalise_flag_value(expected_val)

            # Normalisation rule 2: exact float comparison (no epsilon)
            if actual_val != expected_val:
                differences.append(Difference(
                    field=f"words[{i}].{field}",
                    baseline=expected_val,
                    actual=actual_val,
                ))

    # --- Compare entities as multisets (normalisation rule 3) ---
    actual_entities: list[dict[str, Any]] = list(actual.get("entities") or [])
    expected_entities: list[dict[str, Any]] = list(expected.get("entities") or [])

    # Sort both arrays by (name, kind, type, mentions) for stable multiset comparison
    actual_entities.sort(key=_entity_key)
    expected_entities.sort(key=_entity_key)

    # Report length mismatch
    if len(actual_entities) != len(expected_entities):
        differences.append(Difference(
            field="entities.length",
            baseline=len(expected_entities),
            actual=len(actual_entities),
        ))

    # Compare element by element after sorting
    entity_len = max(len(actual_entities), len(expected_entities))
    for i in range(entity_len):
        a = actual_entities[i] if i < len(actual_entities) else None
        e = expected_entities[i] if i < len(expected_entities) else None

        if a is None and e is not None:
            differences.append(Difference(
                field=f"entities[{i}]",
                baseline=e,
                actual=None,
            ))
            continue

        if a is not None and e is None:
            differences.append(Difference(
                field=f"entities[{i}]",
                baseline=None,
                actual=a,
            ))
            continue

        if _entity_key(a) != _entity_key(e):  # type: ignore[arg-type]
            differences.append(Difference(
                field=f"entities[{i}]",
                baseline=e,
                actual=a,
            ))

    return (len(differences) == 0, differences)


# ---------------------------------------------------------------------------
# DatasetSnapshot fixture — built from generated dataset JSON files
# ---------------------------------------------------------------------------

_DATASETS_DIR = Path(__file__).parents[2] / "datasets"


def _entity_type_from_str(raw: str) -> EntityType:
    """Convert a raw entity_type string to the EntityType enum."""
    mapping: dict[str, EntityType] = {
        "region": EntityType.region,
        "city": EntityType.city,
        "constituency": EntityType.constituency,
        "district": EntityType.district,
        "president": EntityType.president,
        "minister": EntityType.minister,
        "mp": EntityType.mp,
        "party": EntityType.party,
        "supplementary": EntityType.supplementary,
    }
    return mapping.get(raw, EntityType.supplementary)


def _load_locations() -> list[EntityRecord]:
    """Load location records from locations.json."""
    path = _DATASETS_DIR / "locations.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    records: list[EntityRecord] = []
    for entry in data:
        records.append(EntityRecord(
            canonical=entry["canonical"],
            entity_kind=EntityKind.location,
            entity_type=_entity_type_from_str(entry.get("entity_type", "supplementary")),
            aliases=entry.get("aliases", []),
            region=entry.get("region") or None,
            source=entry.get("source", "locations"),
            source_rank=entry.get("source_rank", 0),
        ))
    return records


def _load_persons() -> list[EntityRecord]:
    """Load person records from persons.json."""
    path = _DATASETS_DIR / "persons.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    records: list[EntityRecord] = []
    for entry in data:
        records.append(EntityRecord(
            canonical=entry["canonical"],
            entity_kind=EntityKind.person,
            entity_type=_entity_type_from_str(entry.get("entity_type", "president")),
            aliases=entry.get("aliases", []),
            role=entry.get("role") or None,
            source=entry.get("source", "persons"),
            source_rank=entry.get("source_rank", 0),
        ))
    return records


def _load_mps() -> list[EntityRecord]:
    """Load MP records from mps.json."""
    path = _DATASETS_DIR / "mps.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    records: list[EntityRecord] = []
    for entry in data:
        records.append(EntityRecord(
            canonical=entry["canonical"],
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=entry.get("aliases", []),
            constituency=entry.get("constituency") or None,
            party=entry.get("party") or None,
            role=entry.get("role") or None,
            source=entry.get("source", "mps"),
            source_rank=entry.get("source_rank", 0),
        ))
    return records


def _load_parties() -> list[EntityRecord]:
    """Load party records from parties.json."""
    path = _DATASETS_DIR / "parties.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    records: list[EntityRecord] = []
    for entry in data:
        records.append(EntityRecord(
            canonical=entry["canonical"],
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=entry.get("aliases", []),
            party=entry.get("abbreviation") or None,
            source=entry.get("source", "parties"),
            source_rank=entry.get("source_rank", 0),
        ))
    return records


def _build_dataset_snapshot() -> DatasetSnapshot:
    """Build a DatasetSnapshot from the generated dataset JSON files.

    Loads from services/postprocess/datasets/{persons,locations,parties,mps}.json
    so both engines (JS and Python) resolve entities from the same origin.
    This avoids hand-built EntityRecords that can drift from the real datasets.
    """
    records: list[EntityRecord] = []
    records.extend(_load_locations())
    records.extend(_load_persons())
    records.extend(_load_mps())
    records.extend(_load_parties())

    index = build_index(records)

    return DatasetSnapshot(
        version="baseline-fixture-v1",
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


@pytest.fixture(scope="session")
def dataset_snapshot() -> DatasetSnapshot:
    """Session-scoped fixture providing a DatasetSnapshot from generated datasets.

    Built once per test session from the JSON files in
    services/postprocess/datasets/, ensuring both the JS and Python engines
    resolve entities from the same origin data.
    """
    return _build_dataset_snapshot()
