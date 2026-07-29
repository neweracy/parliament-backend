"""Baseline Harness — Python Record Mode.

Records the pre-refactor output of the Python correction engine for every
fixture in the Golden Corpus. Invoked as:

    python services/postprocess/scripts/record_baseline.py [--force]

This file intentionally lives under ``scripts/``, outside
``testpaths = ["tests"]``, so pytest cannot execute it.

Outputs:
    fixtures/golden-corpus/recorded/py/<fixture-id>.json  — one per fixture
    fixtures/golden-corpus/recorded/MANIFEST-py.json      — sha256 map

Requirements: 1.2, 1.10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# Resolve from the script location → repo root
_SCRIPT_DIR = Path(__file__).resolve().parent
_SERVICE_ROOT = _SCRIPT_DIR.parent  # services/postprocess/
_REPO_ROOT = _SERVICE_ROOT.parent.parent  # repo root

_CORPUS_PATH = _REPO_ROOT / "fixtures" / "golden-corpus" / "corpus.json"
_RECORDED_DIR = _REPO_ROOT / "fixtures" / "golden-corpus" / "recorded" / "py"
_MANIFEST_PATH = _REPO_ROOT / "fixtures" / "golden-corpus" / "recorded" / "MANIFEST-py.json"

# Ensure the service root is on sys.path so we can import app modules
sys.path.insert(0, str(_SERVICE_ROOT))

from app.correction.engine import correct_text, correct_words  # noqa: E402
from app.datasets.cache import DatasetSnapshot  # noqa: E402
from app.datasets.index import MatchIndex, build_index  # noqa: E402
from app.models.entities import EntityKind, EntityRecord, EntityType  # noqa: E402
from app.years.corrector import correct_years, correct_years_in_text  # noqa: E402

# ---------------------------------------------------------------------------
# Dataset loading (same logic as tests/baseline/conftest.py)
# ---------------------------------------------------------------------------

_DATASETS_DIR = _SERVICE_ROOT / "datasets"


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
    """Build a DatasetSnapshot from the generated dataset JSON files."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    """Compute sha256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_entity_summary(
    word_corrections: list[tuple[str, str, str, float, str, str]],
    entities_found: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Build an entity summary from corrections and identity-matched entities.

    Returns a list of {name, kind, type, mentions} dicts.
    """
    entity_map: dict[str, dict[str, Any]] = {}

    # Count from entities_found (includes identity matches)
    for canonical, kind, etype in entities_found:
        key = f"{canonical}|{kind}|{etype}"
        if key not in entity_map:
            entity_map[key] = {
                "name": canonical,
                "kind": kind,
                "type": etype,
                "mentions": 0,
            }
        entity_map[key]["mentions"] += 1

    return list(entity_map.values())


def _build_corrections_list(
    text_corrections: list[Any],
    year_text_count: int,
    year_word_count: int,
    text_result_text: str,
    original_text: str,
    corrected_text: str,
) -> list[dict[str, Any]]:
    """Build a deduplicated corrections summary.

    Returns a list of {original, corrected, strategy, confidence} dicts.
    """
    corrections: list[dict[str, Any]] = []
    seen: set[str] = set()

    # From text-level entity corrections
    for tc in text_corrections:
        key = f"{tc.original}→{tc.replacement}"
        if key not in seen:
            seen.add(key)
            corrections.append({
                "original": tc.original,
                "corrected": tc.replacement,
                "strategy": tc.strategy,
                "confidence": tc.confidence,
            })

    return corrections


# ---------------------------------------------------------------------------
# Main recording logic
# ---------------------------------------------------------------------------


def record(*, force: bool = False) -> None:
    """Record the Python baseline for all Golden Corpus fixtures."""
    # Load corpus
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        corpus = json.load(f)

    corpus_version: str = corpus["version"]
    cases: list[dict[str, Any]] = corpus["cases"]

    print(f"Recording Python baseline for corpus v{corpus_version} ({len(cases)} fixtures)")

    # Check for existing files unless --force
    if not force:
        existing_ids: list[str] = []
        for fixture in cases:
            file_path = _RECORDED_DIR / f"{fixture['id']}.json"
            if file_path.exists():
                try:
                    existing = json.loads(file_path.read_text(encoding="utf-8"))
                    if existing.get("corpus_version") == corpus_version:
                        existing_ids.append(fixture["id"])
                except (json.JSONDecodeError, KeyError):
                    pass  # Malformed file — treat as needing overwrite

        if existing_ids:
            print(f"\nERROR: Recorded files already exist for corpus v{corpus_version}.", file=sys.stderr)
            print("Use --force to overwrite.\n", file=sys.stderr)
            print("Fixture IDs that would be overwritten:", file=sys.stderr)
            for fid in existing_ids:
                print(f"  - {fid}", file=sys.stderr)
            sys.exit(1)

    # Ensure output directory exists
    _RECORDED_DIR.mkdir(parents=True, exist_ok=True)

    # Build dataset snapshot
    snapshot = _build_dataset_snapshot()
    index: MatchIndex = snapshot.index

    manifest_entries: dict[str, str] = {}
    recorded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for fixture in cases:
        fixture_id: str = fixture["id"]
        input_transcript: str = fixture["input_transcript"]
        input_words: list[dict[str, Any]] = fixture.get("input_words", [])

        # Synthesise input_words if missing (same logic as the JS loader)
        if not input_words:
            tokens = input_transcript.split()
            pos = 0.0
            for token in tokens:
                input_words.append({
                    "word": token,
                    "start": round(pos, 3),
                    "end": round(pos + 0.3, 3),
                    "confidence": 0.95,
                })
                pos += 0.3

        # 1. Text-level correction
        text_result = correct_text(input_transcript, index, snapshot)

        # 2. Word-level correction
        words_input = [dict(w) for w in input_words]
        word_result = correct_words(words_input, index, snapshot)

        # 3. Year corrections on transcript text
        year_corrected_text, year_text_count = correct_years_in_text(text_result.text)

        # 4. Year corrections on word array
        year_corrected_words, year_word_count = correct_years(word_result.words)

        # 5. Final transcript
        final_transcript = year_corrected_text

        # 6. Final words
        final_words = year_corrected_words

        # 7. Build entity summary from word-level corrections
        entities = _build_entity_summary(
            word_result.corrections,
            word_result.entities_found,
        )

        # 8. Build corrections summary
        corrections = _build_corrections_list(
            text_result.corrections,
            year_text_count,
            year_word_count,
            text_result.text,
            input_transcript,
            year_corrected_text,
        )

        # 9. Assemble output
        output: dict[str, Any] = {
            "fixture_id": fixture_id,
            "engine": "py",
            "corpus_version": corpus_version,
            "recorded_at": recorded_at,
            "transcript": final_transcript,
            "words": final_words,
            "entities": entities,
            "corrections": corrections,
        }

        # 10. Write to file
        content = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
        file_path = _RECORDED_DIR / f"{fixture_id}.json"
        file_path.write_text(content, encoding="utf-8")

        # 11. Track sha256 for manifest
        manifest_entries[fixture_id] = _sha256(content)

        print(f"  ✓ {fixture_id}")

    # Write MANIFEST-py.json
    manifest: dict[str, Any] = {
        "corpus_version": corpus_version,
        "recorded_at": recorded_at,
        "entries": manifest_entries,
    }
    manifest_content = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    _MANIFEST_PATH.write_text(manifest_content, encoding="utf-8")

    print(f"\nDone. Recorded {len(cases)} fixtures.")
    print(f"Manifest: {_MANIFEST_PATH.relative_to(_REPO_ROOT)}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI args and run recording."""
    parser = argparse.ArgumentParser(
        description="Record Python baseline outputs for the Golden Corpus.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing recorded files without prompting.",
    )
    args = parser.parse_args()
    record(force=args.force)


if __name__ == "__main__":
    main()
