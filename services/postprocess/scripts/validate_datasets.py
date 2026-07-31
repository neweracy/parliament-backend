#!/usr/bin/env python3
"""Validate the generated dataset exports under ``datasets/``.

These files are build artifacts produced by ``generate_dataset_exports.py`` from
the JS datasets. Nothing at runtime reads them — the Dataset_Store is seeded by
``migrate_js_datasets.py`` from ``datasets_export_raw.json`` — but they are
committed, reviewed, and used by ``record_baseline.py``, so silent corruption in
them is still a real problem.

Checks are split by severity, because not every anomaly is a defect:

ERRORS (exit non-zero) — these are always wrong:
  * Mojibake. Text that was decoded with the wrong codec, e.g. an en-dash
    (U+2013) surfacing as ``â€“``. Caused by decoding UTF-8 output as cp1252.
  * Natural-key collisions on ``(canonical, entity_kind, source)``. This is the
    Dataset_Store's UNIQUE constraint, so a collision means the seeder would
    silently merge or overwrite records.
  * Missing or blank ``canonical``.
  * Blank aliases, or aliases/canonicals with leading, trailing, or doubled
    internal whitespace — these never match spoken input and only pad the index.
  * Exact duplicate aliases within one record.
  * Control characters.

WARNINGS (reported, exit zero) — legitimate in this data:
  * Repeated ``canonical`` across different sources. Real: a person can be Vice
    President and later President; a town can appear in both the city list and
    the supplementary list. The natural key keeps them distinct.
  * An alias equal to its canonical. Redundant but harmless, and the exact-match
    strategy relies on the canonical being present in the index.

Usage::

    python scripts/validate_datasets.py
    python scripts/validate_datasets.py --strict   # treat warnings as errors
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import unicodedata
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = SCRIPT_DIR.parent
DATASETS_DIR = SERVICE_ROOT / "datasets"

# Substrings that only occur when UTF-8 bytes were decoded as cp1252/latin-1.
# "â€" covers the en-dash, em-dash, and curly-quote families in one check.
MOJIBAKE_MARKERS = (
    "â€",
    "Ã©",
    "Ã¡",
    "Ã­",
    "Ã³",
    "Ãº",
    "Ã±",
    "Ã¨",
    "Ã¼",
    "â‚¬",
    "Â ",
)

# entity_kind is implied by the file, since the exports do not carry the column.
FILE_ENTITY_KIND = {
    "persons": "person",
    "locations": "location",
    "parties": "party",
    "mps": "person",
}


class Report:
    """Collects errors and warnings across all dataset files."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def _has_mojibake(text: str) -> bool:
    return any(marker in text for marker in MOJIBAKE_MARKERS)


def _has_control_chars(text: str) -> bool:
    """True if text contains C0/C1 control characters (tabs and newlines included).

    Category "Cc" covers them; none belong in an entity name or alias.
    """
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def _whitespace_anomaly(text: str) -> bool:
    return text != text.strip() or "  " in text


def validate_file(name: str, report: Report) -> int:
    """Validate one dataset file. Returns the record count."""
    path = DATASETS_DIR / f"{name}.json"

    if not path.exists():
        report.error(f"{name}.json: file is missing")
        return 0

    raw = path.read_text(encoding="utf-8")

    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        report.error(f"{name}.json: invalid JSON — {exc}")
        return 0

    if not isinstance(records, list):
        report.error(f"{name}.json: top level must be a list, got {type(records).__name__}")
        return 0

    if not records:
        report.error(f"{name}.json: contains no records")
        return 0

    entity_kind = FILE_ENTITY_KIND[name]

    # --- whole-file encoding check -------------------------------------
    if _has_mojibake(raw):
        # Count and sample so the failure is actionable rather than a bare flag.
        total = sum(raw.count(m) for m in MOJIBAKE_MARKERS)
        samples: list[str] = []
        for rec in records:
            for field in ("canonical", "role", "region", "constituency", "party"):
                value = rec.get(field) or ""
                if isinstance(value, str) and _has_mojibake(value):
                    samples.append(f"{rec.get('canonical', '?')}.{field}={value!r}")
                    break
            if len(samples) >= 3:
                break
        report.error(
            f"{name}.json: {total} mojibake sequence(s) — text was decoded with the "
            f"wrong codec. Examples: {'; '.join(samples) or 'n/a'}. "
            "Regenerate with generate_dataset_exports.py, which pins UTF-8."
        )

    # --- per-record checks ---------------------------------------------
    natural_keys: collections.Counter[tuple[str, str, str]] = collections.Counter()
    canonical_sources: dict[str, set[str]] = collections.defaultdict(set)
    self_alias_count = 0

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            report.error(f"{name}.json[{idx}]: record is not an object")
            continue

        canonical = rec.get("canonical")
        if not isinstance(canonical, str) or not canonical.strip():
            report.error(f"{name}.json[{idx}]: missing or blank 'canonical'")
            continue

        if _whitespace_anomaly(canonical):
            report.error(
                f"{name}.json[{idx}]: canonical {canonical!r} has leading, trailing, "
                "or doubled whitespace"
            )
        if _has_control_chars(canonical):
            report.error(f"{name}.json[{idx}]: canonical {canonical!r} contains control characters")

        # 'parties' and 'mps' exports carry no explicit source column.
        source = rec.get("source") or name
        natural_keys[(canonical, entity_kind, source)] += 1
        canonical_sources[canonical].add(source)

        aliases = rec.get("aliases", [])
        if not isinstance(aliases, list):
            report.error(f"{name}.json[{idx}]: 'aliases' must be a list")
            continue

        seen: collections.Counter[str] = collections.Counter()
        for alias in aliases:
            if not isinstance(alias, str):
                report.error(f"{name}.json[{idx}]: non-string alias {alias!r} for {canonical!r}")
                continue
            if not alias.strip():
                report.error(f"{name}.json[{idx}]: blank alias for {canonical!r}")
                continue
            if _whitespace_anomaly(alias):
                report.error(
                    f"{name}.json[{idx}]: alias {alias!r} for {canonical!r} has leading, "
                    "trailing, or doubled whitespace"
                )
            if _has_control_chars(alias):
                report.error(
                    f"{name}.json[{idx}]: alias {alias!r} for {canonical!r} contains "
                    "control characters"
                )
            seen[alias] += 1
            if alias.strip().lower() == canonical.strip().lower():
                self_alias_count += 1

        for alias, count in seen.items():
            if count > 1:
                report.error(
                    f"{name}.json[{idx}]: alias {alias!r} repeated {count}x for {canonical!r}"
                )

        # Case-insensitive duplicates are a real index inefficiency: matching is
        # case-folded, so two spellings differing only in case are one entry.
        folded = collections.Counter(a.strip().lower() for a in aliases if isinstance(a, str))
        for alias_lower, count in folded.items():
            if count > 1 and seen.get(alias_lower, 0) != count:
                report.error(
                    f"{name}.json[{idx}]: aliases for {canonical!r} collide "
                    f"case-insensitively on {alias_lower!r} ({count}x)"
                )

    # --- natural key integrity -----------------------------------------
    # This mirrors the Dataset_Store UNIQUE (canonical, entity_kind, source).
    collisions = {k: v for k, v in natural_keys.items() if v > 1}
    for (canonical, kind, source), count in list(collisions.items())[:10]:
        report.error(
            f"{name}.json: natural-key collision ({canonical!r}, {kind}, {source}) "
            f"appears {count}x — the Dataset_Store UNIQUE constraint would merge these"
        )

    # --- expected, legitimate repetition --------------------------------
    multi_source = {c: s for c, s in canonical_sources.items() if len(s) > 1}
    if multi_source:
        example = next(iter(multi_source.items()))
        report.warn(
            f"{name}.json: {len(multi_source)} canonical(s) appear under multiple sources "
            f"(expected — e.g. {example[0]!r} in {sorted(example[1])}); "
            "distinct under the natural key"
        )

    if self_alias_count:
        report.warn(
            f"{name}.json: {self_alias_count} alias(es) duplicate their canonical "
            "(harmless; exact-match relies on the canonical being indexed)"
        )

    return len(records)


def validate_csv_parity(name: str, report: Report) -> None:
    """Check the CSV row count matches its JSON counterpart.

    The CSV is a flattened view of the same records, so a divergence means one of
    the pair was written from stale data.
    """
    json_path = DATASETS_DIR / f"{name}.json"
    csv_path = DATASETS_DIR / f"{name}.csv"

    if not csv_path.exists():
        report.error(f"{name}.csv: file is missing")
        return
    if not json_path.exists():
        return

    raw_csv = csv_path.read_text(encoding="utf-8")
    if _has_mojibake(raw_csv):
        total = sum(raw_csv.count(m) for m in MOJIBAKE_MARKERS)
        report.error(f"{name}.csv: {total} mojibake sequence(s) — regenerate the exports")

    import csv as csv_module

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv_module.DictReader(handle))

    try:
        records = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    if len(rows) != len(records):
        report.error(
            f"{name}: CSV has {len(rows)} rows but JSON has {len(records)} records — "
            "regenerate both from the same source"
        )
        return

    # alias_count must agree with the JSON alias list length.
    for idx, (row, rec) in enumerate(zip(rows, records)):
        declared = row.get("alias_count")
        if declared is None:
            break
        try:
            declared_int = int(declared)
        except (TypeError, ValueError):
            report.error(f"{name}.csv[{idx}]: alias_count {declared!r} is not an integer")
            continue
        actual = len(rec.get("aliases", []))
        if declared_int != actual:
            report.error(
                f"{name}.csv[{idx}]: alias_count={declared_int} for "
                f"{row.get('canonical')!r} but JSON lists {actual} aliases"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the generated dataset export files."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    args = parser.parse_args(argv)

    report = Report()
    counts: dict[str, int] = {}

    for name in ("persons", "locations", "parties", "mps"):
        counts[name] = validate_file(name, report)
        validate_csv_parity(name, report)

    print("Dataset validation")
    print("==================")
    for name, count in counts.items():
        print(f"  {name:<10} {count:>5} records")
    print()

    if report.warnings:
        print(f"Warnings ({len(report.warnings)}):")
        for msg in report.warnings:
            print(f"  ! {msg}")
        print()

    if report.errors:
        print(f"Errors ({len(report.errors)}):")
        for msg in report.errors[:40]:
            print(f"  x {msg}")
        if len(report.errors) > 40:
            print(f"  ... and {len(report.errors) - 40} more")
        print()
        print("FAILED")
        return 1

    if args.strict and report.warnings:
        print("FAILED (strict: warnings present)")
        return 1

    print("All checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
