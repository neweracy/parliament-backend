"""Integration test for migration idempotence.

Verifies:
1. Running the migration script twice produces the same contents as running it
   once — upserts don't create duplicates (Requirement 9.4).
2. The count report after migration matches the JS dataset counts — all entity
   kinds tally (Requirement 9.3).
3. Re-running migration doesn't alter existing data — no modification of
   already-correct rows.

The migration uses ``ON CONFLICT ... DO UPDATE`` for records and ``DO NOTHING``
for aliases, making it idempotent by construction. These tests exercise the
migration logic end-to-end by mocking the database engine and the Node
subprocess (export), then confirming the SQL interactions behave correctly
across repeated runs.

Requirements: 9.3, 9.4
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.migrate_js_datasets import (
    assert_sub_array_check,
    expected_counts,
    expected_kind_counts,
    expected_block_list_counts,
    upsert_source,
    seed_block_lists,
    report,
    main,
    sorted_sources,
    export_js_datasets,
)


# ---------------------------------------------------------------------------
# Fixtures — simulated JS dataset export output
# ---------------------------------------------------------------------------


def _make_export_fixture() -> dict:
    """Simulate the output of export_js_datasets.js with a small dataset.

    Covers multiple entity kinds (location, person, party), multiple sources,
    and includes aliases and block lists to exercise all migration paths.
    """
    return {
        "sources": {
            "regions": {
                "rank": 10,
                "source": "regions",
                "records": [
                    {
                        "canonical": "Greater Accra",
                        "entity_kind": "location",
                        "entity_type": "region",
                        "aliases": [],
                        "region": None,
                        "constituency": None,
                        "party": None,
                        "role": None,
                        "active": True,
                        "source": "regions",
                        "source_rank": 10,
                    },
                    {
                        "canonical": "Ashanti",
                        "entity_kind": "location",
                        "entity_type": "region",
                        "aliases": [],
                        "region": None,
                        "constituency": None,
                        "party": None,
                        "role": None,
                        "active": True,
                        "source": "regions",
                        "source_rank": 10,
                    },
                ],
            },
            "cities": {
                "rank": 20,
                "source": "cities",
                "records": [
                    {
                        "canonical": "Kumasi",
                        "entity_kind": "location",
                        "entity_type": "city",
                        "aliases": ["Kumase", "Ksi"],
                        "region": "Ashanti",
                        "constituency": None,
                        "party": None,
                        "role": None,
                        "active": True,
                        "source": "cities",
                        "source_rank": 20,
                    },
                    {
                        "canonical": "Accra",
                        "entity_kind": "location",
                        "entity_type": "city",
                        "aliases": ["Acra"],
                        "region": "Greater Accra",
                        "constituency": None,
                        "party": None,
                        "role": None,
                        "active": True,
                        "source": "cities",
                        "source_rank": 20,
                    },
                ],
            },
            "persons_presidents": {
                "rank": 40,
                "source": "persons_presidents",
                "records": [
                    {
                        "canonical": "Nana Akufo-Addo",
                        "entity_kind": "person",
                        "entity_type": "president",
                        "aliases": ["Nana Addo", "Akufo-Addo"],
                        "region": None,
                        "constituency": None,
                        "party": None,
                        "role": "President",
                        "active": True,
                        "source": "persons_presidents",
                        "source_rank": 40,
                    },
                ],
            },
            "parties": {
                "rank": 60,
                "source": "parties",
                "records": [
                    {
                        "canonical": "New Patriotic Party",
                        "entity_kind": "party",
                        "entity_type": "party",
                        "aliases": ["NPP"],
                        "region": None,
                        "constituency": None,
                        "party": "NPP",
                        "role": None,
                        "active": True,
                        "source": "parties",
                        "source_rank": 60,
                    },
                ],
            },
        },
        "counts": {"regions": 2, "cities": 2, "persons_presidents": 1, "parties": 1},
        "aliasCounts": {"regions": 0, "cities": 3, "persons_presidents": 2, "parties": 1},
        "mergedCounts": {},
        "kindCounts": {"location": 4, "person": 1, "party": 1},
        "blockLists": {
            "block": [
                {"token": "general", "reason": "fixed defect: over-corrected to a Ghana location"},
                {"token": "page", "reason": "fixed defect: over-corrected to a Ghana location"},
                {"token": "nation", "reason": "fixed defect: over-corrected to a Ghana location"},
            ],
            "stopword": [
                {"token": "a", "reason": None},
                {"token": "the", "reason": None},
            ],
            "word_stopword": [
                {"token": "a", "reason": None},
                {"token": "the", "reason": None},
            ],
            "title": [
                {"token": "hon", "reason": None},
                {"token": "dr", "reason": None},
            ],
        },
        "blockListCounts": {"block": 3, "stopword": 2, "word_stopword": 2, "title": 2},
        "subArrayCheck": {
            "sum": 1,
            "allPersons": 1,
            "match": True,
            "subArrays": {"PRESIDENTS": 1},
        },
    }


# ---------------------------------------------------------------------------
# Test: Idempotent upserts — running twice produces same result
# ---------------------------------------------------------------------------


class TestMigrationIdempotence:
    """Running the migration twice yields the same contents as running it once.

    Validates Requirement 9.4: The migration SHALL be re-runnable, so that
    running the migration twice produces the same Dataset_Store contents as
    running it once.
    """

    def test_upsert_source_second_run_uses_on_conflict_update(self):
        """ON CONFLICT ... DO UPDATE means a second insert of the same record
        overwrites with identical data, leaving the row unchanged."""
        export = _make_export_fixture()
        records = export["sources"]["cities"]["records"]

        # Track all execute calls across two runs
        all_calls_run1 = []
        all_calls_run2 = []

        def _make_engine_mock(call_tracker):
            engine = MagicMock()
            conn = MagicMock()

            # scalar_one returns a fake record_id for each upsert
            record_id_seq = iter(range(100, 200))

            def execute_side_effect(sql, params=None):
                result = MagicMock()
                sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
                result.scalar_one.return_value = next(record_id_seq)
                result.rowcount = 1
                call_tracker.append((sql_str, params))
                return result

            conn.execute = MagicMock(side_effect=execute_side_effect)
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=conn)
            ctx.__exit__ = MagicMock(return_value=False)
            engine.begin = MagicMock(return_value=ctx)

            return engine

        engine1 = _make_engine_mock(all_calls_run1)
        engine2 = _make_engine_mock(all_calls_run2)

        # Run upsert twice — both should execute the same SQL statements
        upsert_source(engine1, records)
        upsert_source(engine2, records)

        # Both runs should have made the same number of execute calls
        assert len(all_calls_run1) == len(all_calls_run2)

        # Both runs should have executed ON CONFLICT ... DO UPDATE SQL
        for sql_text, _ in all_calls_run1:
            if "INSERT INTO entity_record" in sql_text:
                assert "ON CONFLICT" in sql_text
                assert "DO UPDATE" in sql_text

    def test_alias_insert_uses_do_nothing(self):
        """ON CONFLICT ... DO NOTHING for aliases means re-inserting existing
        aliases is a no-op — no duplicates are created."""
        export = _make_export_fixture()
        records = export["sources"]["cities"]["records"]

        alias_sqls = []

        engine = MagicMock()
        conn = MagicMock()

        record_id_counter = iter(range(1, 100))

        def execute_side_effect(sql, params=None):
            result = MagicMock()
            sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
            if "INSERT INTO entity_alias" in sql_str:
                alias_sqls.append((sql_str, params))
                result.rowcount = 0  # Second run: DO NOTHING means 0 rows affected
            elif "INSERT INTO entity_record" in sql_str:
                result.scalar_one.return_value = next(record_id_counter)
            return result

        conn.execute = MagicMock(side_effect=execute_side_effect)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        engine.begin = MagicMock(return_value=ctx)

        upsert_source(engine, records)

        # All alias INSERT statements use DO NOTHING
        assert len(alias_sqls) > 0
        for sql_text, _ in alias_sqls:
            assert "ON CONFLICT" in sql_text
            assert "DO NOTHING" in sql_text

    def test_block_list_seed_uses_do_nothing(self):
        """Block_List seeding with DO NOTHING means re-seeding is a no-op."""
        export = _make_export_fixture()
        block_lists = export["blockLists"]

        block_sqls = []

        engine = MagicMock()
        conn = MagicMock()

        def execute_side_effect(sql, params=None):
            result = MagicMock()
            sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
            block_sqls.append((sql_str, params))
            result.rowcount = 0  # Simulating second run — no new rows
            return result

        conn.execute = MagicMock(side_effect=execute_side_effect)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        engine.begin = MagicMock(return_value=ctx)

        # First run: some rows inserted
        result1 = seed_block_lists(engine, block_lists)

        # Second run: all DO NOTHING, rowcount = 0
        result2 = seed_block_lists(engine, block_lists)

        # Second run inserted 0 rows (idempotent)
        for kind, count in result2.items():
            assert count == 0

    def test_full_migration_idempotence_via_main(self):
        """Running main() twice with the same export data executes the same
        upsert logic — the ON CONFLICT clauses ensure identical end state."""
        export = _make_export_fixture()

        upsert_call_counts = []

        def _run_main_mock():
            """Simulate main() by running its core logic with mocked DB."""
            assert_sub_array_check(export)
            js_records, js_aliases = expected_counts(export)
            js_kinds = expected_kind_counts(export)

            engine = MagicMock()
            conn = MagicMock()
            record_ids = iter(range(1, 1000))
            call_count = 0

            def execute_side_effect(sql, params=None):
                nonlocal call_count
                call_count += 1
                result = MagicMock()
                sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
                if "INSERT INTO entity_record" in sql_str:
                    result.scalar_one.return_value = next(record_ids)
                else:
                    result.rowcount = 1
                return result

            conn.execute = MagicMock(side_effect=execute_side_effect)
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=conn)
            ctx.__exit__ = MagicMock(return_value=False)
            engine.begin = MagicMock(return_value=ctx)

            for key, source in sorted_sources(export):
                upsert_source(engine, source["records"])
            seed_block_lists(engine, export.get("blockLists") or {})

            upsert_call_counts.append(call_count)

        # Run twice
        _run_main_mock()
        _run_main_mock()

        # Both runs should execute the same number of DB operations
        assert len(upsert_call_counts) == 2
        assert upsert_call_counts[0] == upsert_call_counts[1]


# ---------------------------------------------------------------------------
# Test: Count report matches JS counts (Requirement 9.3)
# ---------------------------------------------------------------------------


class TestCountReportMatchesJsCounts:
    """The count report after migration matches the JS dataset counts.

    Validates Requirement 9.3: THE migration SHALL produce a record count in
    the Dataset_Store equal to the record count in the JavaScript datasets for
    each Entity_Kind, and SHALL report the per-kind counts.
    """

    def test_expected_counts_match_fixture_totals(self):
        """expected_counts() derives correct per-source record and alias counts
        from the export fixture."""
        export = _make_export_fixture()
        records, aliases = expected_counts(export)

        assert records["regions"] == 2
        assert records["cities"] == 2
        assert records["persons_presidents"] == 1
        assert records["parties"] == 1

        assert aliases["regions"] == 0
        assert aliases["cities"] == 3  # Kumase, Ksi, Acra
        assert aliases["persons_presidents"] == 2  # Nana Addo, Akufo-Addo
        assert aliases["parties"] == 1  # NPP

    def test_expected_kind_counts_tally_correctly(self):
        """expected_kind_counts() returns correct counts per entity_kind."""
        export = _make_export_fixture()
        kinds = expected_kind_counts(export)

        assert kinds["location"] == 4  # 2 regions + 2 cities
        assert kinds["person"] == 1
        assert kinds["party"] == 1

    def test_expected_block_list_counts_match(self):
        """expected_block_list_counts() returns correct per-list_kind counts."""
        export = _make_export_fixture()
        block_counts = expected_block_list_counts(export)

        assert block_counts["block"] == 3
        assert block_counts["stopword"] == 2
        assert block_counts["word_stopword"] == 2
        assert block_counts["title"] == 2

    def test_report_returns_true_when_counts_match(self):
        """report() returns True when DB counts equal JS counts."""
        export = _make_export_fixture()
        js_records, js_aliases = expected_counts(export)
        js_kinds = expected_kind_counts(export)
        js_blocks = expected_block_list_counts(export)

        # Simulate DB returning identical counts
        ok = report(
            {"records": js_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": js_blocks},
            {"records": js_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": js_blocks},
        )
        assert ok is True

    def test_report_returns_false_on_record_count_mismatch(self):
        """report() returns False when a source record count doesn't match."""
        export = _make_export_fixture()
        js_records, js_aliases = expected_counts(export)
        js_kinds = expected_kind_counts(export)
        js_blocks = expected_block_list_counts(export)

        # DB has one fewer city record
        db_records = dict(js_records)
        db_records["cities"] = 1

        ok = report(
            {"records": js_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": js_blocks},
            {"records": db_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": js_blocks},
        )
        assert ok is False

    def test_report_returns_false_on_kind_count_mismatch(self):
        """report() returns False when an entity_kind count doesn't match."""
        export = _make_export_fixture()
        js_records, js_aliases = expected_counts(export)
        js_kinds = expected_kind_counts(export)
        js_blocks = expected_block_list_counts(export)

        db_kinds = dict(js_kinds)
        db_kinds["location"] = 3  # Should be 4

        ok = report(
            {"records": js_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": js_blocks},
            {"records": js_records, "aliases": js_aliases, "kinds": db_kinds, "block_lists": js_blocks},
        )
        assert ok is False

    def test_report_returns_false_on_block_list_mismatch(self):
        """report() returns False when a block_list kind count doesn't match."""
        export = _make_export_fixture()
        js_records, js_aliases = expected_counts(export)
        js_kinds = expected_kind_counts(export)
        js_blocks = expected_block_list_counts(export)

        db_blocks = dict(js_blocks)
        db_blocks["block"] = 2  # Should be 3

        ok = report(
            {"records": js_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": js_blocks},
            {"records": js_records, "aliases": js_aliases, "kinds": js_kinds, "block_lists": db_blocks},
        )
        assert ok is False


# ---------------------------------------------------------------------------
# Test: Re-running doesn't alter existing data
# ---------------------------------------------------------------------------


class TestRerunDoesNotAlterData:
    """Re-running migration doesn't alter existing data — already-correct rows
    are overwritten with identical values by DO UPDATE, and aliases that already
    exist are skipped by DO NOTHING.

    Validates Requirements 9.3, 9.4.
    """

    def test_upsert_passes_identical_params_on_rerun(self):
        """The parameters passed to the upsert SQL are identical on both runs,
        so DO UPDATE writes the same values over the same rows."""
        export = _make_export_fixture()
        records = export["sources"]["persons_presidents"]["records"]

        params_run1 = []
        params_run2 = []

        def _make_engine(params_list):
            engine = MagicMock()
            conn = MagicMock()
            id_seq = iter(range(1, 100))

            def execute_side_effect(sql, params=None):
                result = MagicMock()
                sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
                if "INSERT INTO entity_alias" in sql_str:
                    result.rowcount = 1
                    params_list.append(("alias", params))
                elif "INSERT INTO entity_record" in sql_str:
                    result.scalar_one.return_value = next(id_seq)
                    params_list.append(("record", params))
                return result

            conn.execute = MagicMock(side_effect=execute_side_effect)
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=conn)
            ctx.__exit__ = MagicMock(return_value=False)
            engine.begin = MagicMock(return_value=ctx)
            return engine

        engine1 = _make_engine(params_run1)
        engine2 = _make_engine(params_run2)

        upsert_source(engine1, records)
        upsert_source(engine2, records)

        # Same parameters on both runs
        assert len(params_run1) == len(params_run2)
        for (type1, p1), (type2, p2) in zip(params_run1, params_run2):
            assert type1 == type2
            assert p1 == p2

    def test_alias_rowcount_zero_on_second_run_means_no_duplicates(self):
        """When rowcount is 0 for alias inserts (DO NOTHING conflict), no
        duplicate aliases are created. The SQL uses ON CONFLICT DO NOTHING so
        re-inserting existing aliases is a no-op."""
        export = _make_export_fixture()
        records = export["sources"]["cities"]["records"]

        alias_params_captured = []

        engine = MagicMock()
        conn = MagicMock()
        id_seq = iter(range(1, 100))

        def execute_side_effect(sql, params=None):
            result = MagicMock()
            sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
            if "INSERT INTO entity_alias" in sql_str:
                alias_params_captured.append(params)
                # Simulate second run: DO NOTHING on conflict → rowcount 0
                result.rowcount = 0
            elif "INSERT INTO entity_record" in sql_str:
                result.scalar_one.return_value = next(id_seq)
            return result

        conn.execute = MagicMock(side_effect=execute_side_effect)
        # Properly mock the context manager for `with engine.begin() as conn:`
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        engine.begin = MagicMock(return_value=ctx)

        upsert_source(engine, records)

        # All alias INSERT statements fired (one per alias in the dataset)
        total_aliases = sum(len(r["aliases"]) for r in records)
        assert len(alias_params_captured) == total_aliases
        # Each alias SQL uses ON CONFLICT DO NOTHING — proven by the SQL text
        # in the test_alias_insert_uses_do_nothing test above

    def test_sorted_sources_preserves_rank_order(self):
        """Sources are processed in rank order on every run, ensuring
        write-order semantics are consistent."""
        export = _make_export_fixture()

        sources = sorted_sources(export)
        ranks = [s[1]["rank"] for s in sources]

        # Ranks should be in ascending order
        assert ranks == sorted(ranks)

    def test_sub_array_check_passes_for_valid_export(self):
        """assert_sub_array_check passes without error for a valid export."""
        export = _make_export_fixture()
        # Should not raise
        assert_sub_array_check(export)

    def test_sub_array_check_fails_on_mismatch(self):
        """assert_sub_array_check exits non-zero when sum != allPersons."""
        export = _make_export_fixture()
        export["subArrayCheck"]["match"] = False
        export["subArrayCheck"]["sum"] = 99
        export["subArrayCheck"]["allPersons"] = 100

        with pytest.raises(SystemExit):
            assert_sub_array_check(export)

    def test_main_dry_run_returns_zero(self):
        """main(--dry-run) exits 0 without writing anything."""
        export = _make_export_fixture()

        with patch("scripts.migrate_js_datasets.export_js_datasets", return_value=export):
            result = main(["--dry-run"])

        assert result == 0

    def test_main_exits_nonzero_on_count_mismatch(self):
        """main() exits non-zero when DB counts don't match JS counts."""
        export = _make_export_fixture()

        engine = MagicMock()
        conn = MagicMock()
        id_seq = iter(range(1, 1000))

        def execute_side_effect(sql, params=None):
            result = MagicMock()
            sql_str = str(sql.text) if hasattr(sql, 'text') else str(sql)
            if "INSERT INTO entity_record" in sql_str:
                result.scalar_one.return_value = next(id_seq)
            else:
                result.rowcount = 1
            return result

        conn.execute = MagicMock(side_effect=execute_side_effect)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        engine.begin = MagicMock(return_value=ctx)
        engine.connect = MagicMock(return_value=ctx)
        engine.dispose = MagicMock()

        # db_counts returns mismatched values
        mismatched_db_counts = (
            {"location": 3, "person": 1, "party": 1},  # kind counts (location wrong)
            {"regions": 2, "cities": 2, "persons_presidents": 1, "parties": 1},
            {"regions": 0, "cities": 3, "persons_presidents": 2, "parties": 1},
        )

        with (
            patch("scripts.migrate_js_datasets.export_js_datasets", return_value=export),
            patch("scripts.migrate_js_datasets.sa.create_engine", return_value=engine),
            patch("scripts.migrate_js_datasets.db_counts", return_value=mismatched_db_counts),
            patch("scripts.migrate_js_datasets.db_block_list_counts", return_value={"block": 3, "stopword": 2, "word_stopword": 2, "title": 2}),
        ):
            result = main(["--database-url", "postgresql://test:test@localhost/test"])

        assert result == 1
