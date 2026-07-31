"""Tests for the generated dataset exports and their validator.

The files under ``datasets/`` are build artifacts, but they are committed and
consumed by ``record_baseline.py``, so corruption in them is a real defect. These
tests are the regression guard for the two bugs found in them:

1. Mojibake from decoding the Node exporter's UTF-8 stdout with the platform
   locale encoding (cp1252 on Windows), which turned every en-dash into ``â€“``.
2. Case-only duplicate aliases, which inflate the Match_Index because alias
   matching is case-folded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
DATASETS_DIR = SERVICE_ROOT / "datasets"
DATASET_NAMES = ("persons", "locations", "parties", "mps")

# Import the helpers under test from the generator script.
import sys

sys.path.insert(0, str(SERVICE_ROOT / "scripts"))

from generate_dataset_exports import clean_aliases, clean_text  # noqa: E402
from validate_datasets import (  # noqa: E402
    MOJIBAKE_MARKERS,
    Report,
    _has_control_chars,
    _whitespace_anomaly,
    validate_file,
)


# ---------------------------------------------------------------------------
# clean_aliases — normalization behaviour
# ---------------------------------------------------------------------------


def test_clean_aliases_removes_case_only_duplicates() -> None:
    """Matching is case-folded, so these are one key, not two."""
    out = clean_aliases(["BB Kabu", "bb kabu", "BB Kabo"])
    assert out == ["BB Kabu", "BB Kabo"]


def test_clean_aliases_preserves_first_spelling() -> None:
    """First wins: alias ordinal order is load-bearing for the Match_Index."""
    assert clean_aliases(["bb carboo", "BB Carboo"]) == ["bb carboo"]


def test_clean_aliases_preserves_order() -> None:
    """Must be a stable filter, never a sort."""
    src = ["Zebra", "Apple", "Mango"]
    assert clean_aliases(src) == src


def test_clean_aliases_keeps_genuinely_distinct_spellings() -> None:
    """Punctuation differences are real variants and must survive."""
    out = clean_aliases(["BB Carboo", "b.b. carboo", "Carboo"])
    assert out == ["BB Carboo", "b.b. carboo", "Carboo"]


def test_clean_aliases_trims_and_collapses_whitespace() -> None:
    assert clean_aliases(["  Kofi   Annan  "]) == ["Kofi Annan"]


def test_clean_aliases_drops_blank_and_nonstring() -> None:
    assert clean_aliases(["", "   ", None, 42, "Valid"]) == ["Valid"]


def test_clean_aliases_empty_input() -> None:
    assert clean_aliases([]) == []


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("  President   (1960–1966) ") == "President (1960–1966)"


def test_clean_text_handles_non_string() -> None:
    assert clean_text(None) == ""
    assert clean_text(123) == ""


# ---------------------------------------------------------------------------
# Validator helpers
# ---------------------------------------------------------------------------


def test_whitespace_anomaly_detection() -> None:
    assert _whitespace_anomaly(" leading")
    assert _whitespace_anomaly("trailing ")
    assert _whitespace_anomaly("double  space")
    assert not _whitespace_anomaly("clean name")


def test_control_char_detection() -> None:
    assert _has_control_chars("bad\tname")
    assert _has_control_chars("bad\nname")
    assert not _has_control_chars("Kofi Annan")


def test_mojibake_markers_match_real_corruption() -> None:
    """The marker set must catch the exact corruption that was found."""
    corrupted = "President (1960â€“1966)"
    assert any(m in corrupted for m in MOJIBAKE_MARKERS)


def test_mojibake_markers_do_not_flag_clean_text() -> None:
    """Correct en-dashes and accents must not be flagged."""
    for clean in ("President (1960–1966)", "Côte d'Ivoire", "Kofi Annan", "Ejisu"):
        assert not any(m in clean for m in MOJIBAKE_MARKERS), clean


# ---------------------------------------------------------------------------
# The committed dataset files must be clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_dataset_file_is_valid_utf8_json(name: str) -> None:
    path = DATASETS_DIR / f"{name}.json"
    assert path.exists(), f"{name}.json is missing"
    data = json.loads(path.read_bytes().decode("utf-8"))
    assert isinstance(data, list) and data


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_dataset_file_has_no_mojibake(name: str) -> None:
    """Regression guard for the cp1252 decode bug."""
    for suffix in ("json", "csv"):
        text = (DATASETS_DIR / f"{name}.{suffix}").read_text(encoding="utf-8")
        found = [m for m in MOJIBAKE_MARKERS if m in text]
        assert not found, f"{name}.{suffix} contains mojibake: {found}"


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_dataset_file_has_no_duplicate_aliases(name: str) -> None:
    """No record may carry the same alias twice, case-insensitively."""
    data = json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    for rec in data:
        aliases = rec.get("aliases", [])
        folded = [a.casefold() for a in aliases]
        assert len(folded) == len(set(folded)), (
            f"{name}.json: {rec.get('canonical')!r} has duplicate aliases: {aliases}"
        )


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_dataset_file_passes_validator(name: str) -> None:
    """The committed files must pass validation with zero errors."""
    report = Report()
    count = validate_file(name, report)
    assert count > 0
    assert report.errors == [], f"{name}.json validation errors: {report.errors}"


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_no_whitespace_or_control_anomalies(name: str) -> None:
    data = json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    for rec in data:
        canonical = rec["canonical"]
        assert not _whitespace_anomaly(canonical), canonical
        assert not _has_control_chars(canonical), canonical
        for alias in rec.get("aliases", []):
            assert alias.strip(), f"blank alias in {canonical!r}"
            assert not _whitespace_anomaly(alias), f"{canonical}: {alias!r}"
            assert not _has_control_chars(alias), f"{canonical}: {alias!r}"


def test_natural_key_uniqueness_matches_db_constraint() -> None:
    """(canonical, entity_kind, source) must be unique — it's the DB's UNIQUE key.

    Repeated canonicals across different sources are legitimate (a VP who later
    became President; a town in both the city and supplementary lists), so the
    assertion is on the full natural key, not the name alone.
    """
    import collections

    for name, kind in (("persons", "person"), ("locations", "location")):
        data = json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
        keys = collections.Counter((r["canonical"], kind, r["source"]) for r in data)
        collisions = {k: v for k, v in keys.items() if v > 1}
        assert not collisions, f"{name}.json natural-key collisions: {collisions}"


def test_legitimate_cross_source_duplicates_are_preserved() -> None:
    """Guard against an over-eager 'dedupe' deleting real records.

    Atta Mills was Vice President and later President; both records must exist.
    """
    data = json.loads((DATASETS_DIR / "persons.json").read_text(encoding="utf-8"))
    mills = [r for r in data if r["canonical"] == "John Evans Atta Mills"]
    assert len(mills) == 2, "expected both the VP and President records"
    assert {r["source"] for r in mills} == {
        "persons_presidents",
        "persons_vice_presidents",
    }


def test_csv_alias_count_matches_json() -> None:
    """The CSV alias_count column must agree with the JSON alias list."""
    import csv

    for name in DATASET_NAMES:
        with (DATASETS_DIR / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        data = json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert len(rows) == len(data), f"{name}: CSV/JSON row count mismatch"
        for row, rec in zip(rows, data):
            assert int(row["alias_count"]) == len(rec["aliases"]), (
                f"{name}: {rec['canonical']!r} alias_count mismatch"
            )
