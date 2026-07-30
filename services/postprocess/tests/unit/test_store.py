"""Unit tests for app/datasets/store.py — Dataset_Store access layer.

Tests verify:
- Inactive records are excluded from load_active_records (Requirement 9.11)
- Load order is stable and deterministic across repeated loads (Requirement 3.12)
- Block-list rows group correctly by list_kind in load_all_block_lists
- load_block_list returns entries for the specified kind only
- similarity_search respects the threshold parameter
- Empty inputs are handled gracefully

Requirements: 9.11, 3.12, 15.1
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.datasets.store import (
    BlockListEntry,
    SimilarityResult,
    load_active_records,
    load_all_block_lists,
    load_block_list,
    make_engine,
    make_session_factory,
    similarity_search,
    similarity_search_multi,
)
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session():
    """Create a mock AsyncSession with an execute method returning mock results."""
    session = AsyncMock()
    return session


def _make_record_row(
    record_id: int,
    canonical: str,
    entity_kind: str = "person",
    entity_type: str = "minister",
    region: str | None = None,
    constituency: str | None = None,
    party: str | None = None,
    role: str | None = None,
    active: bool = True,
    source: str = "persons",
    source_rank: int = 1,
):
    """Create a tuple mimicking a row from the entity_record SELECT."""
    return (
        record_id, canonical, entity_kind, entity_type,
        region, constituency, party, role,
        active, source, source_rank,
    )


def _make_alias_row(entity_record_id: int, alias: str, ordinal: int):
    """Create a tuple mimicking a row from the entity_alias SELECT."""
    return (entity_record_id, alias, ordinal)


def _make_block_list_row(token: str, list_kind: str, reason: str | None = None):
    """Create a tuple mimicking a row from the block_list SELECT."""
    return (token, list_kind, reason)


# ---------------------------------------------------------------------------
# Test: Inactive records excluded (Requirement 9.11)
# ---------------------------------------------------------------------------


class TestLoadActiveRecordsExcludesInactive:
    """Inactive records are excluded from load_active_records."""

    async def test_only_active_records_returned(self):
        """The SQL WHERE clause filters on active=true, so inactive records
        never appear in the result. We verify the function processes only
        what the query returns (which already excludes inactive)."""
        session = _mock_session()

        # Simulate DB returning only active records (inactive filtered by SQL)
        active_rows = [
            _make_record_row(1, "Kumasi", "location", "city", source_rank=1),
            _make_record_row(2, "Ken Ofori-Atta", "person", "minister", source_rank=2),
        ]

        alias_rows = [
            _make_alias_row(1, "Kumase", 0),
            _make_alias_row(2, "K. Ofori-Atta", 0),
        ]

        # First execute call returns records, second returns aliases
        records_result = MagicMock()
        records_result.fetchall.return_value = active_rows

        aliases_result = MagicMock()
        aliases_result.fetchall.return_value = alias_rows

        session.execute = AsyncMock(side_effect=[records_result, aliases_result])

        result = await load_active_records(session)

        assert len(result) == 2
        assert all(r.active is True for r in result)
        assert result[0].canonical == "Kumasi"
        assert result[1].canonical == "Ken Ofori-Atta"

    async def test_inactive_not_in_result_when_db_excludes_them(self):
        """When the DB query returns no rows (all inactive), the function
        returns an empty list."""
        session = _mock_session()

        records_result = MagicMock()
        records_result.fetchall.return_value = []

        session.execute = AsyncMock(return_value=records_result)

        result = await load_active_records(session)

        assert result == []

    async def test_sql_contains_active_true_filter(self):
        """Verify the SQL text passed to execute contains the active=true filter."""
        session = _mock_session()

        records_result = MagicMock()
        records_result.fetchall.return_value = []

        session.execute = AsyncMock(return_value=records_result)

        await load_active_records(session)

        # Inspect the SQL text of the first call
        call_args = session.execute.call_args_list[0]
        sql_text = str(call_args[0][0].text)
        assert "active = true" in sql_text.lower().replace("  ", " ")


# ---------------------------------------------------------------------------
# Test: Load order determinism (Requirement 3.12)
# ---------------------------------------------------------------------------


class TestLoadOrderStability:
    """Load order is stable across repeated loads — ordered by
    (source_rank, entity_kind, canonical, id)."""

    async def test_records_returned_in_deterministic_order(self):
        """Records come back in the exact order the DB returns them (which
        is guaranteed by the ORDER BY clause)."""
        session = _mock_session()

        # Rows already in ORDER BY source_rank, entity_kind, canonical, id
        rows = [
            _make_record_row(10, "Accra", "location", "city", source_rank=1),
            _make_record_row(20, "Kumasi", "location", "city", source_rank=1),
            _make_record_row(5, "Ama Ata Aidoo", "person", "minister", source_rank=2),
            _make_record_row(15, "Ken Ofori-Atta", "person", "minister", source_rank=2),
        ]

        records_result = MagicMock()
        records_result.fetchall.return_value = rows

        aliases_result = MagicMock()
        aliases_result.fetchall.return_value = []

        session.execute = AsyncMock(side_effect=[records_result, aliases_result])

        result = await load_active_records(session)

        canonicals = [r.canonical for r in result]
        assert canonicals == ["Accra", "Kumasi", "Ama Ata Aidoo", "Ken Ofori-Atta"]

    async def test_order_by_clause_is_correct(self):
        """The SQL ORDER BY matches the documented index order."""
        session = _mock_session()

        records_result = MagicMock()
        records_result.fetchall.return_value = []
        session.execute = AsyncMock(return_value=records_result)

        await load_active_records(session)

        call_args = session.execute.call_args_list[0]
        sql_text = str(call_args[0][0].text)
        assert "order by source_rank, entity_kind, canonical, id" in sql_text.lower()

    async def test_repeated_loads_produce_same_order(self):
        """Calling load_active_records twice with the same data yields identical results."""
        rows = [
            _make_record_row(1, "Tamale", "location", "city", source_rank=1),
            _make_record_row(2, "Wa", "location", "city", source_rank=1),
            _make_record_row(3, "Nana Akufo-Addo", "person", "president", source_rank=2),
        ]

        alias_rows = [
            _make_alias_row(3, "Nana Addo", 0),
        ]

        results = []
        for _ in range(2):
            session = _mock_session()
            records_result = MagicMock()
            records_result.fetchall.return_value = rows
            aliases_result = MagicMock()
            aliases_result.fetchall.return_value = alias_rows
            session.execute = AsyncMock(side_effect=[records_result, aliases_result])

            result = await load_active_records(session)
            results.append([r.canonical for r in result])

        assert results[0] == results[1]


# ---------------------------------------------------------------------------
# Test: Block-list grouping by kind
# ---------------------------------------------------------------------------


class TestLoadAllBlockLists:
    """Block-list rows group correctly by list_kind."""

    async def test_groups_by_list_kind(self):
        """Entries are grouped into their respective list_kind keys."""
        session = _mock_session()

        rows = [
            _make_block_list_row("general", "block", "common word"),
            _make_block_list_row("nation", "block", "common word"),
            _make_block_list_row("a", "stopword", None),
            _make_block_list_row("the", "stopword", None),
            _make_block_list_row("Mr", "title", None),
            _make_block_list_row("Mrs", "title", None),
        ]

        result_mock = MagicMock()
        result_mock.fetchall.return_value = rows
        session.execute = AsyncMock(return_value=result_mock)

        grouped = await load_all_block_lists(session)

        assert "block" in grouped
        assert "stopword" in grouped
        assert "title" in grouped
        assert len(grouped["block"]) == 2
        assert len(grouped["stopword"]) == 2
        assert len(grouped["title"]) == 2

    async def test_entries_have_correct_tokens(self):
        """Each BlockListEntry in a group has the correct token value."""
        session = _mock_session()

        rows = [
            _make_block_list_row("page", "block", "fixed defect"),
            _make_block_list_row("of", "word_stopword", None),
        ]

        result_mock = MagicMock()
        result_mock.fetchall.return_value = rows
        session.execute = AsyncMock(return_value=result_mock)

        grouped = await load_all_block_lists(session)

        assert grouped["block"][0].token == "page"
        assert grouped["block"][0].reason == "fixed defect"
        assert grouped["word_stopword"][0].token == "of"

    async def test_empty_table_returns_empty_dict(self):
        """When the block_list table is empty, return an empty dict."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        grouped = await load_all_block_lists(session)

        assert grouped == {}


# ---------------------------------------------------------------------------
# Test: load_block_list for specific kind
# ---------------------------------------------------------------------------


class TestLoadBlockList:
    """load_block_list returns entries for the specified kind only."""

    async def test_returns_entries_for_specified_kind(self):
        """Only entries matching the given list_kind are returned."""
        session = _mock_session()

        rows = [
            _make_block_list_row("general", "block", "common word"),
            _make_block_list_row("page", "block", "fixed defect"),
        ]

        result_mock = MagicMock()
        result_mock.fetchall.return_value = rows
        session.execute = AsyncMock(return_value=result_mock)

        entries = await load_block_list(session, "block")

        assert len(entries) == 2
        assert all(e.list_kind == "block" for e in entries)
        assert entries[0].token == "general"
        assert entries[1].token == "page"

    async def test_passes_list_kind_parameter_to_query(self):
        """The list_kind parameter is properly bound in the SQL query."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        await load_block_list(session, "stopword")

        call_args = session.execute.call_args_list[0]
        params = call_args[0][1]
        assert params == {"list_kind": "stopword"}

    async def test_empty_result_for_nonexistent_kind(self):
        """Returns empty list when no entries match the kind."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        entries = await load_block_list(session, "nonexistent")

        assert entries == []


# ---------------------------------------------------------------------------
# Test: similarity_search respects threshold
# ---------------------------------------------------------------------------


class TestSimilaritySearch:
    """similarity_search respects the threshold parameter."""

    async def test_threshold_passed_to_query(self):
        """The threshold value is correctly passed as a parameter to the SQL query."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        await similarity_search(session, "Kumasi", threshold=0.5)

        call_args = session.execute.call_args_list[0]
        params = call_args[0][1]
        assert params["threshold"] == 0.5
        assert params["query"] == "Kumasi"
        assert params["limit"] == 50

    async def test_custom_limit_passed_to_query(self):
        """A custom limit is respected."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        await similarity_search(session, "Accra", limit=10, threshold=0.4)

        call_args = session.execute.call_args_list[0]
        params = call_args[0][1]
        assert params["limit"] == 10
        assert params["threshold"] == 0.4

    async def test_results_include_aliases(self):
        """When results are found, aliases are fetched and attached."""
        session = _mock_session()

        # First query returns matching records
        similarity_rows = [
            (1, "Kumasi", "location", "city", 0.85),
            (2, "Tamale", "location", "city", 0.72),
        ]
        sim_result = MagicMock()
        sim_result.fetchall.return_value = similarity_rows

        # Second query returns aliases for matched records
        alias_rows = [
            (1, "Kumase"),
            (1, "Ksi"),
            (2, "Tamali"),
        ]
        alias_result = MagicMock()
        alias_result.fetchall.return_value = alias_rows

        session.execute = AsyncMock(side_effect=[sim_result, alias_result])

        results = await similarity_search(session, "Kumasi", threshold=0.3)

        assert len(results) == 2
        assert results[0].canonical == "Kumasi"
        assert results[0].similarity == 0.85
        assert results[0].aliases == ["Kumase", "Ksi"]
        assert results[1].canonical == "Tamale"
        assert results[1].aliases == ["Tamali"]

    async def test_empty_query_returns_empty_results(self):
        """When no records match, an empty list is returned."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        results = await similarity_search(session, "xyznonexistent", threshold=0.3)

        assert results == []

    async def test_default_threshold_is_03(self):
        """The default threshold value is 0.3."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        await similarity_search(session, "test")

        call_args = session.execute.call_args_list[0]
        params = call_args[0][1]
        assert params["threshold"] == 0.3


# ---------------------------------------------------------------------------
# Test: Empty inputs handled gracefully
# ---------------------------------------------------------------------------


class TestEmptyInputs:
    """Empty inputs are handled gracefully without errors."""

    async def test_load_active_records_empty_db(self):
        """load_active_records returns empty list when no records exist."""
        session = _mock_session()

        records_result = MagicMock()
        records_result.fetchall.return_value = []
        session.execute = AsyncMock(return_value=records_result)

        result = await load_active_records(session)

        assert result == []

    async def test_similarity_search_multi_empty_list(self):
        """similarity_search_multi with empty query_texts returns empty list."""
        session = _mock_session()

        result = await similarity_search_multi(session, [])

        assert result == []
        # No DB call should have been made
        session.execute.assert_not_called()

    async def test_similarity_search_multi_short_queries_skipped(self):
        """similarity_search_multi skips queries shorter than 3 chars."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        result = await similarity_search_multi(session, ["ab", "x", "  "])

        assert result == []
        # No execute calls because all queries are too short
        session.execute.assert_not_called()

    async def test_load_block_list_empty_table(self):
        """load_block_list returns empty list when table has no entries for the kind."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        entries = await load_block_list(session, "block")

        assert entries == []

    async def test_load_all_block_lists_empty_table(self):
        """load_all_block_lists returns empty dict when table is empty."""
        session = _mock_session()

        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        grouped = await load_all_block_lists(session)

        assert grouped == {}


# ---------------------------------------------------------------------------
# Test: Aliases correctly populated on EntityRecords
# ---------------------------------------------------------------------------


class TestAliasPopulation:
    """Aliases are correctly grouped and attached to their parent records."""

    async def test_aliases_attached_to_correct_records(self):
        """Each record gets only its own aliases."""
        session = _mock_session()

        rows = [
            _make_record_row(1, "Accra", "location", "city", source_rank=1),
            _make_record_row(2, "Nana Akufo-Addo", "person", "president", source_rank=2),
        ]

        alias_rows = [
            _make_alias_row(1, "Acra", 0),
            _make_alias_row(1, "Akra", 1),
            _make_alias_row(2, "Nana Addo", 0),
            _make_alias_row(2, "Akufo-Addo", 1),
            _make_alias_row(2, "Akuffo Addo", 2),
        ]

        records_result = MagicMock()
        records_result.fetchall.return_value = rows
        aliases_result = MagicMock()
        aliases_result.fetchall.return_value = alias_rows

        session.execute = AsyncMock(side_effect=[records_result, aliases_result])

        result = await load_active_records(session)

        assert result[0].aliases == ["Acra", "Akra"]
        assert result[1].aliases == ["Nana Addo", "Akufo-Addo", "Akuffo Addo"]

    async def test_record_with_no_aliases_gets_empty_list(self):
        """A record with no aliases gets an empty alias list."""
        session = _mock_session()

        rows = [
            _make_record_row(1, "Wa", "location", "city", source_rank=1),
        ]

        records_result = MagicMock()
        records_result.fetchall.return_value = rows
        aliases_result = MagicMock()
        aliases_result.fetchall.return_value = []

        session.execute = AsyncMock(side_effect=[records_result, aliases_result])

        result = await load_active_records(session)

        assert result[0].canonical == "Wa"
        assert result[0].aliases == []


# ---------------------------------------------------------------------------
# Test: make_engine URL conversion
# ---------------------------------------------------------------------------


class TestMakeEngine:
    """make_engine converts database URLs correctly."""

    def test_converts_postgresql_to_psycopg(self):
        """postgresql:// is converted to postgresql+psycopg://."""
        engine = make_engine("postgresql://user:pass@localhost/db")
        assert "+psycopg" in str(engine.url)

    def test_converts_postgres_to_psycopg(self):
        """postgres:// (short form) is converted to postgresql+psycopg://."""
        engine = make_engine("postgres://user:pass@localhost/db")
        assert "postgresql+psycopg" in str(engine.url)

    def test_already_async_url_unchanged(self):
        """postgresql+psycopg:// stays unchanged."""
        engine = make_engine("postgresql+psycopg://user:pass@localhost/db")
        assert "postgresql+psycopg" in str(engine.url)
