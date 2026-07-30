#!/usr/bin/env python3
"""Migrate the JavaScript correction datasets into the Dataset_Store.

The JS modules under ``lib/location-correction/`` stay the single source of
truth for Ghana entities. This script shells out to
``scripts/export_js_datasets.js``, which ``require``s each module and prints one
JSON document, so no record is ever hand-transcribed into Python.

Properties, in the order they are enforced:

1. **Guarded** — the sub-array cross-check (``sum(sub-arrays) == len(ALL_PERSONS)``)
   is asserted before any write. A failure means ``persons-dataset.js`` has been
   restructured and migrating would double-load ministers, so the script stops.
2. **Transactional** — one transaction per source, so a partial failure leaves no
   half-migrated source behind.
3. **Idempotent** — ``ON CONFLICT ... DO UPDATE`` for ``entity_record``,
   ``ON CONFLICT ... DO NOTHING`` for ``entity_alias``. Running the script twice
   yields the same contents as running it once.
4. **Count-verifying** — after loading, per-kind and per-source counts are read
   back from the database, compared against the counts the JS modules reported,
   printed as a table, and any mismatch exits non-zero.

Source ranks (stored in ``entity_record.source_rank``) reproduce the
``buildDataset()`` insertion order the design fixes as
regions → cities → supplementary → persons → MPs → parties::

    regions                  10
    cities                   20
    supplementary            30
    persons_presidents       40
    persons_vice_presidents  41
    persons_speakers         42
    ministers                43
    persons_other_notables   44
    persons_cultural_figures 45
    mps                      50
    parties                  60

The ordering is load-bearing, not cosmetic. ``SUPPLEMENTARY_LOCATIONS`` and
``ALL_PERSONS`` aliases are **last-wins** in ``buildDataset()`` while ``ALL_MPS``
and party aliases are **first-wins**, so supplementary and person aliases must be
written *before* MPs and parties for a later source to correctly yield the key.
The minister rank sits between ``SPEAKERS`` and ``OTHER_NOTABLES`` because
``persons-dataset.js`` spreads ``...MINISTERS`` into ``ALL_PERSONS`` at exactly
that position.

Block_List seeding runs in the same script and in the same style: ``COMMON_BLOCK``
→ ``block``, ``STOPWORDS`` → ``stopword``, the ``wordStopwords`` Set from
``server.js`` → ``word_stopword``, ``TITLE_PREFIXES`` → ``title``, each with a
``reason`` string on the tokens that trace to a fixed defect.

Usage::

    cd services/postprocess
    python scripts/migrate_js_datasets.py
    python scripts/migrate_js_datasets.py --database-url postgresql://...
    python scripts/migrate_js_datasets.py --dry-run   # export + checks, no writes

Requirements: 9.1, 9.3, 9.4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text

# Script lives at services/postprocess/scripts/migrate_js_datasets.py.
# Paths are resolved from the script location, never from the CWD.
SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
EXPORTER = SCRIPT_DIR / "export_js_datasets.js"

# Node-free fallback: a committed snapshot of the exporter's stdout. Written by
# `node scripts/export_js_datasets.js > datasets_export_raw.json` and used when
# Node or the lib/ tree is unavailable (e.g. inside the service container, which
# ships neither). Regenerate it whenever the JS datasets change.
RAW_EXPORT_FALLBACK = SERVICE_ROOT / "datasets_export_raw.json"

NODE_TIMEOUT_SECONDS = 120

UPSERT_RECORD_SQL = text(
    """
    INSERT INTO entity_record
        (canonical, entity_kind, entity_type, region, constituency,
         party, role, active, source, source_rank, updated_at)
    VALUES
        (:canonical, :entity_kind, :entity_type, :region, :constituency,
         :party, :role, :active, :source, :source_rank, now())
    ON CONFLICT (canonical, entity_kind, source)
    DO UPDATE SET
        entity_type   = EXCLUDED.entity_type,
        region        = EXCLUDED.region,
        constituency  = EXCLUDED.constituency,
        party         = EXCLUDED.party,
        role          = EXCLUDED.role,
        active        = EXCLUDED.active,
        source_rank   = EXCLUDED.source_rank,
        updated_at    = now()
    RETURNING id
    """
)

INSERT_ALIAS_SQL = text(
    """
    INSERT INTO entity_alias (entity_record_id, alias, ordinal)
    VALUES (:entity_record_id, :alias, :ordinal)
    ON CONFLICT (entity_record_id, alias) DO NOTHING
    """
)

INSERT_BLOCK_LIST_SQL = text(
    """
    INSERT INTO block_list (token, list_kind, reason)
    VALUES (:token, :list_kind, :reason)
    ON CONFLICT (token, list_kind) DO NOTHING
    """
)

# The four suppression sets, in the order they are seeded.
BLOCK_LIST_KINDS = ("block", "stopword", "word_stopword", "title")


# ---------------------------------------------------------------------------
# Node export
# ---------------------------------------------------------------------------


def export_js_datasets() -> dict[str, Any]:
    """Return the exporter output, preferring live Node over the committed snapshot.

    The JS modules under ``lib/location-correction/`` are the single source of
    truth, so a live ``node`` run is authoritative and used whenever Node and
    the exporter script are both present. When they are not — most importantly
    inside the service container, which ships neither Node nor the ``lib/``
    tree — the committed ``datasets_export_raw.json`` snapshot is used instead.
    Exits non-zero only when neither source is available.
    """
    if EXPORTER.is_file() and _node_available():
        return _export_via_node()

    if RAW_EXPORT_FALLBACK.is_file():
        print(f"note: Node unavailable — reading committed export {RAW_EXPORT_FALLBACK.name}\n")
        try:
            return json.loads(RAW_EXPORT_FALLBACK.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _fail(f"{RAW_EXPORT_FALLBACK.name} is not valid JSON: {exc}")

    _fail(
        "cannot read the JS datasets: `node` is unavailable and no committed "
        f"{RAW_EXPORT_FALLBACK.name} was found. Regenerate it with "
        "`node scripts/export_js_datasets.js > datasets_export_raw.json`."
    )
    raise AssertionError("unreachable")  # pragma: no cover


def _node_available() -> bool:
    """Return True when a `node` executable is resolvable on PATH."""
    from shutil import which

    return which("node") is not None


def _export_via_node() -> dict[str, Any]:
    """Run the Node exporter and parse its JSON stdout.

    Exits non-zero if the exporter fails or its output is not parseable JSON.
    """
    try:
        proc = subprocess.run(
            ["node", str(EXPORTER)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=NODE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _fail(f"the Node exporter did not finish within {NODE_TIMEOUT_SECONDS}s")

    if proc.returncode != 0:
        _fail(
            "the Node exporter exited "
            f"{proc.returncode}:\n{proc.stderr.strip() or '(no stderr)'}"
        )

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"the Node exporter did not emit valid JSON: {exc}")
    raise AssertionError("unreachable")  # pragma: no cover


def assert_sub_array_check(export: dict[str, Any]) -> None:
    """Assert the ALL_PERSONS cross-check before anything is written.

    ``persons-dataset.js`` spreads ``...MINISTERS`` (which *is*
    ``ALL_MINISTERS``) into ``ALL_PERSONS``. Migrating the sub-arrays and
    ``ALL_MINISTERS`` is therefore only safe while their lengths still sum to
    ``len(ALL_PERSONS)``. If they do not, the datasets have been restructured
    and continuing would load ministers twice.
    """
    check = export.get("subArrayCheck") or {}
    sub_arrays: dict[str, int] = check.get("subArrays") or {}

    print("ALL_PERSONS cross-check")
    for name, count in sub_arrays.items():
        print(f"  {name:<20} {count:>6}")
    print(f"  {'sum':<20} {check.get('sum'):>6}")
    print(f"  {'len(ALL_PERSONS)':<20} {check.get('allPersons'):>6}")

    if not check.get("match"):
        _fail(
            "ALL_PERSONS cross-check FAILED: "
            f"sub-array sum {check.get('sum')} != len(ALL_PERSONS) "
            f"{check.get('allPersons')}. The JavaScript datasets have been "
            "restructured; migrating now would double-load ministers. "
            "Nothing was written."
        )

    print("  ok — sub-array sum equals len(ALL_PERSONS)\n")


# ---------------------------------------------------------------------------
# Expected counts derived from the export
# ---------------------------------------------------------------------------


def expected_counts(export: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    """Return ``(records_by_source, aliases_by_source)`` from the JS export.

    Alias counts are of *distinct* aliases per record, because
    ``entity_alias`` is unique on ``(entity_record_id, alias)`` and a repeated
    alias inside one JS record is absorbed by ``DO NOTHING``.
    """
    records: dict[str, int] = {}
    aliases: dict[str, int] = {}

    for key, source in sorted_sources(export):
        rows = source["records"]
        records[key] = len(rows)
        aliases[key] = sum(len(set(row["aliases"])) for row in rows)

    return records, aliases


def expected_kind_counts(export: dict[str, Any]) -> dict[str, int]:
    """Return record counts per ``entity_kind`` from the JS export."""
    counts: dict[str, int] = {}
    for _key, source in sorted_sources(export):
        for row in source["records"]:
            counts[row["entity_kind"]] = counts.get(row["entity_kind"], 0) + 1
    return counts


def sorted_sources(export: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(key, source)`` pairs in ``source_rank`` order.

    Load order is the write-order policy: a supplementary or person alias must
    be able to overwrite an earlier claim while an MP or party alias yields to
    it, which only holds if sources are written in ``buildDataset()`` order.
    """
    return sorted(export["sources"].items(), key=lambda kv: kv[1]["rank"])


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def upsert_source(engine: sa.Engine, records: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert one source's records and aliases inside a single transaction.

    Returns the number of records upserted and aliases actually inserted.
    """
    aliases_inserted = 0

    with engine.begin() as conn:
        for rec in records:
            record_id = conn.execute(
                UPSERT_RECORD_SQL,
                {
                    "canonical": rec["canonical"],
                    "entity_kind": rec["entity_kind"],
                    "entity_type": rec["entity_type"],
                    "region": rec["region"],
                    "constituency": rec["constituency"],
                    "party": rec["party"],
                    "role": rec["role"],
                    "active": rec["active"],
                    "source": rec["source"],
                    "source_rank": rec["source_rank"],
                },
            ).scalar_one()

            for ordinal, alias in enumerate(rec["aliases"]):
                result = conn.execute(
                    INSERT_ALIAS_SQL,
                    {
                        "entity_record_id": record_id,
                        "alias": alias,
                        "ordinal": ordinal,
                    },
                )
                aliases_inserted += result.rowcount or 0

    return {"records": len(records), "aliases": aliases_inserted}


def seed_block_lists(
    engine: sa.Engine, block_lists: dict[str, list[dict[str, Any]]]
) -> dict[str, int]:
    """Seed ``block_list`` from the exported suppression sets.

    One transaction per ``list_kind``, so a partial failure leaves no
    half-seeded list. ``ON CONFLICT (token, list_kind) DO NOTHING`` makes a
    re-run a no-op, which is what keeps the whole migration idempotent — a
    ``DO UPDATE`` here would silently clear a ``reason`` that a later curator
    had added by hand.

    Returns the number of rows actually inserted per ``list_kind``.
    """
    inserted: dict[str, int] = {}

    for list_kind in BLOCK_LIST_KINDS:
        entries = block_lists.get(list_kind) or []
        count = 0
        with engine.begin() as conn:
            for entry in entries:
                result = conn.execute(
                    INSERT_BLOCK_LIST_SQL,
                    {
                        "token": entry["token"],
                        "list_kind": list_kind,
                        "reason": entry.get("reason"),
                    },
                )
                count += result.rowcount or 0
        inserted[list_kind] = count

    return inserted


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def expected_block_list_counts(export: dict[str, Any]) -> dict[str, int]:
    """Return the distinct-token count per ``list_kind`` from the JS export."""
    block_lists: dict[str, list[dict[str, Any]]] = export.get("blockLists") or {}
    return {
        kind: len({entry["token"] for entry in (block_lists.get(kind) or [])})
        for kind in BLOCK_LIST_KINDS
    }


def db_block_list_counts(engine: sa.Engine) -> dict[str, int]:
    """Read the row count per ``list_kind`` back from ``block_list``."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT list_kind, COUNT(*) FROM block_list GROUP BY list_kind")
        ).all()
    return {row[0]: row[1] for row in rows}


def db_counts(engine: sa.Engine) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Read per-kind record counts, per-source record counts, and per-source
    alias counts back from the database."""
    with engine.connect() as conn:
        kind_rows = conn.execute(
            text(
                "SELECT entity_kind, COUNT(*) FROM entity_record "
                "GROUP BY entity_kind"
            )
        ).all()
        source_rows = conn.execute(
            text("SELECT source, COUNT(*) FROM entity_record GROUP BY source")
        ).all()
        alias_rows = conn.execute(
            text(
                "SELECT r.source, COUNT(a.id) "
                "FROM entity_record r "
                "LEFT JOIN entity_alias a ON a.entity_record_id = r.id "
                "GROUP BY r.source"
            )
        ).all()

    return (
        {row[0]: row[1] for row in kind_rows},
        {row[0]: row[1] for row in source_rows},
        {row[0]: row[1] for row in alias_rows},
    )


def report(js: dict[str, dict[str, int]], db: dict[str, dict[str, int]]) -> bool:
    """Print the per-source, per-kind, and per-list_kind comparison tables.

    Both arguments carry the keys ``records``, ``aliases``, ``kinds``, and
    ``block_lists``. Returns ``True`` when every JS count equals its database
    count.
    """
    ok = True

    js_records, js_aliases, js_kinds = js["records"], js["aliases"], js["kinds"]
    db_records, db_aliases, db_kinds = db["records"], db["aliases"], db["kinds"]
    js_blocks, db_blocks = js["block_lists"], db["block_lists"]

    print(f"{'source':<26} {'js_count':>9} {'db_count':>9} {'js_alias':>9} {'db_alias':>9}  status")
    for key in js_records:
        jr, dr = js_records[key], db_records.get(key, 0)
        ja, da = js_aliases[key], db_aliases.get(key, 0)
        row_ok = jr == dr and ja == da
        ok = ok and row_ok
        print(
            f"{key:<26} {jr:>9} {dr:>9} {ja:>9} {da:>9}  "
            f"{'ok' if row_ok else 'MISMATCH'}"
        )

    extra_sources = sorted(set(db_records) - set(js_records))
    for key in extra_sources:
        ok = False
        print(
            f"{key:<26} {0:>9} {db_records[key]:>9} "
            f"{0:>9} {db_aliases.get(key, 0):>9}  UNEXPECTED"
        )

    print(
        f"{'TOTAL':<26} {sum(js_records.values()):>9} "
        f"{sum(db_records.values()):>9} {sum(js_aliases.values()):>9} "
        f"{sum(db_aliases.values()):>9}"
    )

    print("\nentity_kind    js_count  db_count  status")
    for kind in sorted(set(js_kinds) | set(db_kinds)):
        jc, dc = js_kinds.get(kind, 0), db_kinds.get(kind, 0)
        row_ok = jc == dc
        ok = ok and row_ok
        print(f"{kind:<14} {jc:>8} {dc:>8}  {'ok' if row_ok else 'MISMATCH'}")

    print("\nlist_kind      js_count  db_count  status")
    for list_kind in BLOCK_LIST_KINDS:
        jc, dc = js_blocks.get(list_kind, 0), db_blocks.get(list_kind, 0)
        row_ok = jc == dc
        ok = ok and row_ok
        print(f"{list_kind:<14} {jc:>8} {dc:>8}  {'ok' if row_ok else 'MISMATCH'}")
    print(
        f"{'TOTAL':<14} {sum(js_blocks.values()):>8} "
        f"{sum(db_blocks.get(k, 0) for k in BLOCK_LIST_KINDS):>8}"
    )

    print()
    if ok:
        print("all counts match")
    else:
        print("COUNT MISMATCH — the Dataset_Store does not match the JS datasets")
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def normalize_database_url(url: str) -> str:
    """Force the psycopg 3 driver, which is the pinned dependency."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _fail(message: str) -> None:
    """Print an error and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate the JavaScript correction datasets into the Dataset_Store."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL connection URL (defaults to $DATABASE_URL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Export the datasets and run the cross-check without writing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    print(f"repo root: {REPO_ROOT}")
    print(f"exporter:  {EXPORTER}\n")

    export = export_js_datasets()
    assert_sub_array_check(export)

    js_records, js_aliases = expected_counts(export)
    js_kinds = expected_kind_counts(export)
    js_blocks = expected_block_list_counts(export)

    merged = {k: v for k, v in (export.get("mergedCounts") or {}).items() if v}
    if merged:
        print(
            "note: sources with records merged on (canonical, entity_kind) — "
            "the same person appears under more than one portfolio:"
        )
        for key, count in merged.items():
            print(f"  {key}: {count} merged")
        print()

    if args.dry_run:
        print("dry run — nothing written\n")
        print(f"{'source':<26} {'js_count':>9} {'js_alias':>9}")
        for key, record_count in js_records.items():
            print(f"{key:<26} {record_count:>9} {js_aliases[key]:>9}")
        print(
            f"{'TOTAL':<26} {sum(js_records.values()):>9} "
            f"{sum(js_aliases.values()):>9}"
        )
        print(f"\n{'list_kind':<26} {'js_count':>9}")
        for list_kind in BLOCK_LIST_KINDS:
            print(f"{list_kind:<26} {js_blocks.get(list_kind, 0):>9}")
        print(f"{'TOTAL':<26} {sum(js_blocks.values()):>9}")
        return 0

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        _fail("DATABASE_URL is not set; pass --database-url or export it")

    engine = sa.create_engine(normalize_database_url(database_url), future=True)
    try:
        for key, source in sorted_sources(export):
            stats = upsert_source(engine, source["records"])
            print(
                f"loaded {key:<22} rank={source['rank']:<3} "
                f"records={stats['records']:<5} aliases_inserted={stats['aliases']}"
            )
        seeded = seed_block_lists(engine, export.get("blockLists") or {})
        for list_kind in BLOCK_LIST_KINDS:
            print(
                f"seeded block_list      list_kind={list_kind:<14} "
                f"tokens={js_blocks.get(list_kind, 0):<5} "
                f"inserted={seeded.get(list_kind, 0)}"
            )
        print()

        db_kinds, db_records, db_aliases = db_counts(engine)
        db_blocks = db_block_list_counts(engine)
    finally:
        engine.dispose()

    ok = report(
        {
            "records": js_records,
            "aliases": js_aliases,
            "kinds": js_kinds,
            "block_lists": js_blocks,
        },
        {
            "records": db_records,
            "aliases": db_aliases,
            "kinds": db_kinds,
            "block_lists": db_blocks,
        },
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
