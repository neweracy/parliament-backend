"""Baseline Harness — Python Compare Mode.

Verifies that the current PY_Correction_Engine output is Behaviour_Equivalent
to the recorded baseline for every fixture in the Golden Corpus.

This file is read-only: it imports nothing from scripts/record_baseline.py and
contains no filesystem write path. It runs under ``pytest`` via the
``services/postprocess/tests/baseline/`` collection.

Validates: Requirements 1.4, 1.5, 1.9
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.correction.engine import correct_text, correct_words
from app.datasets.cache import DatasetSnapshot
from app.years.corrector import correct_years, correct_years_in_text

from .conftest import behaviour_equivalent

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CORPUS_PATH = _REPO_ROOT / "fixtures" / "golden-corpus" / "corpus.json"
_MANIFEST_PATH = _REPO_ROOT / "fixtures" / "golden-corpus" / "recorded" / "MANIFEST-py.json"
_RECORDED_DIR = _REPO_ROOT / "fixtures" / "golden-corpus" / "recorded" / "py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    """Compute sha256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_entity_summary(
    entities_found: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Build an entity summary from entities_found.

    Returns a list of {name, kind, type, mentions} dicts.
    """
    entity_map: dict[str, dict[str, Any]] = {}

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


# ---------------------------------------------------------------------------
# Load corpus and build parametrize list
# ---------------------------------------------------------------------------


def _load_corpus() -> tuple[str, list[dict[str, Any]]]:
    """Load the golden corpus and return (version, cases)."""
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    corpus_version: str = data["version"]
    cases: list[dict[str, Any]] = data["cases"]

    # Synthesise input_words where missing (same logic as the JS loader)
    for case in cases:
        if not case.get("input_words"):
            tokens = case["input_transcript"].split()
            pos = 0.0
            words: list[dict[str, Any]] = []
            for token in tokens:
                words.append({
                    "word": token,
                    "start": round(pos, 3),
                    "end": round(pos + 0.3, 3),
                    "confidence": 0.95,
                })
                pos += 0.3
            case["input_words"] = words

    return corpus_version, cases


_corpus_version, corpus_cases = _load_corpus()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", corpus_cases, ids=[c["id"] for c in corpus_cases])
def test_fixture_matches_baseline(fixture: dict[str, Any], dataset_snapshot: DatasetSnapshot) -> None:
    """Verify current Python engine output matches recorded baseline for a fixture."""
    fixture_id: str = fixture["id"]

    # --- Check manifest exists ---
    if not _MANIFEST_PATH.exists():
        pytest.fail(
            "No Python baseline recorded. Run: python scripts/record_baseline.py"
        )

    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

    # --- Verify corpus version matches manifest ---
    if manifest["corpus_version"] != _corpus_version:
        pytest.fail(
            f"Recorded baseline for v{manifest['corpus_version']} but corpus is "
            f"v{_corpus_version}. Re-record: python scripts/record_baseline.py --force"
        )

    # --- Check recorded file exists ---
    recorded_path = _RECORDED_DIR / f"{fixture_id}.json"
    if not recorded_path.exists():
        pytest.fail(
            f"No recorded baseline for fixture '{fixture_id}'. "
            "Run: python scripts/record_baseline.py"
        )

    # --- Read and verify integrity (sha256) ---
    content = recorded_path.read_text(encoding="utf-8")
    actual_hash = _sha256(content)
    expected_hash = manifest["entries"].get(fixture_id)

    if actual_hash != expected_hash:
        pytest.fail(
            f"Integrity check failed for fixture '{fixture_id}' — "
            "modified outside record mode"
        )

    recorded = json.loads(content)

    # --- Verify corpus version in the recorded file ---
    if recorded.get("corpus_version") != _corpus_version:
        pytest.fail(
            f"Recorded baseline for v{recorded.get('corpus_version')} but corpus is "
            f"v{_corpus_version}. Re-record: python scripts/record_baseline.py --force"
        )

    # --- Run current Python correction engine ---
    input_transcript: str = fixture["input_transcript"]
    input_words: list[dict[str, Any]] = fixture["input_words"]

    index = dataset_snapshot.index

    # 1. Text-level correction
    text_result = correct_text(input_transcript, index, dataset_snapshot)

    # 2. Word-level correction
    words_input = [dict(w) for w in input_words]
    word_result = correct_words(words_input, index, dataset_snapshot)

    # 3. Year corrections on transcript text
    year_corrected_text, _year_text_count = correct_years_in_text(text_result.text)

    # 4. Year corrections on word array
    year_corrected_words, _year_word_count = correct_years(word_result.words)

    # 5. Final transcript and words
    final_transcript = year_corrected_text
    final_words = year_corrected_words

    # 6. Build entity summary from word-level output
    entities = _build_entity_summary(word_result.entities_found)

    # --- Compare against recorded baseline ---
    current: dict[str, Any] = {
        "transcript": final_transcript,
        "words": final_words,
        "entities": entities,
    }

    baseline: dict[str, Any] = {
        "transcript": recorded["transcript"],
        "words": recorded["words"],
        "entities": recorded["entities"],
    }

    is_equivalent, differences = behaviour_equivalent(current, baseline)

    if not is_equivalent:
        diff_report = "\n".join(
            f"  field: {d.field}\n"
            f"    baseline: {json.dumps(d.baseline, ensure_ascii=False)}\n"
            f"    current:  {json.dumps(d.actual, ensure_ascii=False)}"
            for d in differences
        )
        pytest.fail(
            f"Fixture '{fixture_id}' — behaviour differs from recorded baseline:\n"
            f"{diff_report}"
        )
