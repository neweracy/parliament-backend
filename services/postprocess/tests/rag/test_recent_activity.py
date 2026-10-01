"""Tests for the find_recent_activity tool in app/rag/agent.py.

Covers period parsing (_resolve_period) and the tool's SQL-facing behaviour
via a fake session_factory, following the FakeRetriever pattern used in
test_agent.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.agent import (
    _EPOCH,
    _MAX_RECENT_ITEMS,
    _RECENT_ACTIVITY_SQL,
    _make_recent_activity_tool,
    _PeriodParseError,
    _resolve_period,
)
from app.rag.recommendations import RegistryReference


def _make_session_factory(rows: list[tuple]):
    """Build a fake async session_factory returning `rows` from execute()."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: rows))

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=context_manager)
    factory.session = session  # exposed for assertions on execute() calls
    return factory


def _make_sequenced_session_factory(*row_sets: list[tuple]):
    """Fake session_factory whose successive execute() calls return each row set."""
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[MagicMock(fetchall=(lambda rs=rs: rs)) for rs in row_sets]
    )

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=context_manager)
    factory.session = session
    return factory


def _make_failing_session_factory():
    """Build a fake session_factory whose execute() raises."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=context_manager)


_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _sitting_row(
    item_id: int = 1,
    title: str = "3rd Sitting",
    created_at: datetime | None = None,
    held_on: str = "2026-08-18",
    status: str = "Active",
    scope_total: int = 1,
) -> tuple:
    """One `_RECENT_ACTIVITY_SQL` sitting row, in SELECT column order."""
    created = created_at or datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    return (
        "sitting",
        item_id,
        title,
        None,
        created,
        None,
        item_id,
        None,
        held_on,
        status,
        scope_total,
    )


def _record_row(
    item_id: int = 5,
    title: str = "Morning Session",
    sitting_title: str = "3rd Sitting",
    created_at: datetime | None = None,
    audio: str | None = "audio.mp3",
    sitting_id: int = 1,
    held_on: str = "2026-08-17",
    status: str = "Draft",
    scope_total: int = 1,
) -> tuple:
    """One `_RECENT_ACTIVITY_SQL` record row, in SELECT column order."""
    created = created_at or datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    return (
        "record",
        item_id,
        title,
        sitting_title,
        created,
        audio,
        sitting_id,
        item_id,
        held_on,
        status,
        scope_total,
    )


class TestResolvePeriod:
    def test_default_recent_is_last_7_days(self):
        start, end, label = _resolve_period("", now=_NOW)
        assert (end - start).days == 7
        assert end == _NOW
        assert label == "the last 7 days"

    def test_recent_keyword(self):
        start, end, label = _resolve_period("recent", now=_NOW)
        assert (end - start).days == 7

    @pytest.mark.parametrize("period", ["all", "All Time", "everything", "ever"])
    def test_all_time_is_unbounded(self, period):
        start, end, label = _resolve_period(period, now=_NOW)
        assert start == _EPOCH
        assert end > _NOW
        assert label == "the whole registry"

    def test_today(self):
        start, end, label = _resolve_period("today", now=_NOW)
        assert start == datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC)
        assert end == _NOW
        assert label == "today"

    def test_this_month(self):
        start, end, label = _resolve_period("this month", now=_NOW)
        assert start == datetime(2026, 8, 1, tzinfo=UTC)
        assert end == _NOW
        assert label == "August 2026"

    def test_last_month(self):
        start, end, label = _resolve_period("last month", now=_NOW)
        assert start == datetime(2026, 7, 1, tzinfo=UTC)
        assert end == datetime(2026, 8, 1, tzinfo=UTC)
        assert label == "July 2026"

    def test_last_month_across_year_boundary(self):
        now = datetime(2026, 1, 15, tzinfo=UTC)
        start, end, label = _resolve_period("last month", now=now)
        assert start == datetime(2025, 12, 1, tzinfo=UTC)
        assert end == datetime(2026, 1, 1, tzinfo=UTC)
        assert label == "December 2025"

    def test_last_n_days(self):
        start, end, label = _resolve_period("last 14 days", now=_NOW)
        assert (end - start).days == 14
        assert label == "the last 14 days"

    def test_iso_month(self):
        start, end, label = _resolve_period("2026-07", now=_NOW)
        assert start == datetime(2026, 7, 1, tzinfo=UTC)
        assert end == datetime(2026, 8, 1, tzinfo=UTC)
        assert label == "July 2026"

    def test_iso_month_future_is_clamped_to_now(self):
        start, end, label = _resolve_period("2026-08", now=_NOW)
        assert end == _NOW

    def test_date_range(self):
        start, end, label = _resolve_period("2026-07-01..2026-07-15", now=_NOW)
        assert start == datetime(2026, 7, 1, tzinfo=UTC)
        assert end == datetime(2026, 7, 16, tzinfo=UTC)

    def test_month_name_defaults_to_current_year(self):
        start, end, label = _resolve_period("july", now=_NOW)
        assert start == datetime(2026, 7, 1, tzinfo=UTC)
        assert end == datetime(2026, 8, 1, tzinfo=UTC)
        assert label == "July 2026"

    def test_month_name_with_explicit_year(self):
        start, end, label = _resolve_period("July 2025", now=_NOW)
        assert start == datetime(2025, 7, 1, tzinfo=UTC)
        assert label == "July 2025"

    def test_month_abbreviation(self):
        start, end, label = _resolve_period("Jul", now=_NOW)
        assert start.month == 7

    def test_invalid_period_raises(self):
        with pytest.raises(_PeriodParseError):
            _resolve_period("whenever", now=_NOW)

    def test_invalid_iso_month_raises(self):
        with pytest.raises(_PeriodParseError):
            _resolve_period("2026-13", now=_NOW)


class TestRecentActivitySql:
    """Static checks on the SQL shape — the per-scope LIMIT is the fix."""

    def test_limit_applies_per_scope_not_to_the_union(self):
        # One LIMIT inside each parenthesised branch; none after the final ORDER BY.
        assert _RECENT_ACTIVITY_SQL.count("LIMIT :limit") == 2
        tail = _RECENT_ACTIVITY_SQL.rsplit(")", 1)[1]
        assert "LIMIT" not in tail

    def test_reports_pre_limit_scope_totals(self):
        assert _RECENT_ACTIVITY_SQL.count("COUNT(*) OVER () AS scope_total") == 2

    def test_ordering_has_a_deterministic_tie_break(self):
        assert "s.created_at DESC, s.id DESC" in _RECENT_ACTIVITY_SQL
        assert "hr.created_at DESC, hr.id DESC" in _RECENT_ACTIVITY_SQL

    def test_selects_parliamentary_date_and_status(self):
        assert "held_on" in _RECENT_ACTIVITY_SQL
        assert "s.status" in _RECENT_ACTIVITY_SQL
        assert "hr.status" in _RECENT_ACTIVITY_SQL


class TestFindRecentActivityTool:
    @pytest.mark.asyncio
    async def test_reports_matching_sittings_and_records(self):
        rows = [_sitting_row(), _record_row()]
        factory = _make_session_factory(rows)
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert "Sitting #1" in result
        assert "3rd Sitting" in result
        assert "Record #5" in result
        assert "audio: audio.mp3" in result
        assert "2 item(s)" in result

    @pytest.mark.asyncio
    async def test_reports_parliamentary_date_and_status(self):
        rows = [
            _sitting_row(held_on="2026-08-18 to 2026-08-19", status="Active"),
            _record_row(held_on="2026-08-17", status="Transcribing"),
        ]
        factory = _make_session_factory(rows)
        tool = _make_recent_activity_tool(factory, [], now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert "held 2026-08-18 to 2026-08-19" in result
        assert "status: Active" in result
        assert "held 2026-08-17" in result
        assert "status: Transcribing" in result

    @pytest.mark.asyncio
    async def test_reports_per_scope_totals_when_complete(self):
        rows = [_sitting_row(scope_total=1), _record_row(scope_total=1)]
        factory = _make_session_factory(rows)
        tool = _make_recent_activity_tool(factory, [], now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "all", "scope": "all"})

        assert "Sittings: showing all 1." in result
        assert "Records: showing all 1." in result

    @pytest.mark.asyncio
    async def test_reports_truncation_against_the_pre_limit_total(self):
        rows = [_record_row(item_id=i, scope_total=40) for i in range(1, 4)]
        factory = _make_session_factory(rows)
        tool = _make_recent_activity_tool(factory, [], now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "all", "scope": "records"})

        assert "showing the 3 most recent of 40" in result
        assert "truncated" in result

    @pytest.mark.asyncio
    async def test_all_period_queries_the_whole_registry(self):
        factory = _make_session_factory([_sitting_row()])
        tool = _make_recent_activity_tool(factory, [], now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "all", "scope": "all"})

        params = factory.session.execute.call_args.args[1]
        assert params["start"] == _EPOCH
        assert params["end"] > _NOW
        assert params["limit"] == _MAX_RECENT_ITEMS
        assert "the whole registry" in result

    @pytest.mark.asyncio
    async def test_empty_recent_window_falls_back_to_latest_overall(self):
        """Regression: a quiet week used to answer "what are the recent sittings" with nothing."""
        factory = _make_sequenced_session_factory([], [_sitting_row(), _record_row()])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert factory.session.execute.await_count == 2
        fallback_params = factory.session.execute.call_args_list[1].args[1]
        assert fallback_params["start"] == _EPOCH
        assert "Nothing was added in the last 7 days" in result
        assert "Most recent additions overall" in result
        assert "Sitting #1" in result
        assert "Record #5" in result
        assert len(collector) == 2

    @pytest.mark.asyncio
    async def test_explicit_period_does_not_fall_back(self):
        factory = _make_sequenced_session_factory([], [_sitting_row()])
        tool = _make_recent_activity_tool(factory, [], now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "last month", "scope": "all"})

        assert factory.session.execute.await_count == 1
        assert "Nothing was added" in result
        assert "July 2026" in result

    @pytest.mark.asyncio
    async def test_collects_navigable_registry_references(self):
        rows = [_sitting_row(), _record_row()]
        factory = _make_session_factory(rows)
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "all"})

        assert len(collector) == 2
        sitting_ref, record_ref = collector

        assert sitting_ref.kind == "sitting"
        assert sitting_ref.id == 1
        assert sitting_ref.title == "3rd Sitting"
        assert sitting_ref.sitting_id == 1
        assert sitting_ref.record_id is None
        assert sitting_ref.created_at == datetime(2026, 8, 18, 10, 0, tzinfo=UTC).isoformat()

        assert record_ref.kind == "record"
        assert record_ref.id == 5
        assert record_ref.title == "Morning Session"
        assert record_ref.sitting_id == 1
        assert record_ref.record_id == 5

    @pytest.mark.asyncio
    async def test_repeated_calls_do_not_duplicate_collected_references(self):
        rows = [_sitting_row()]
        factory = _make_session_factory(rows)
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "all"})
        await tool.ainvoke({"period": "this month", "scope": "all"})

        assert len(collector) == 1

    @pytest.mark.asyncio
    async def test_no_rows_collects_nothing(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "all"})

        assert collector == []

    @pytest.mark.asyncio
    async def test_empty_registry_reports_nothing_added(self):
        """Recent window and the whole-registry fallback both empty."""
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, [], now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert "Nothing was added" in result

    @pytest.mark.asyncio
    async def test_empty_result_reports_nothing_added(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "last month", "scope": "records"})

        assert "Nothing was added" in result
        assert "July 2026" in result

    @pytest.mark.asyncio
    async def test_month_query_resolves_to_correct_range(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "July 2026", "scope": "all"})

        call_args = factory.session.execute.call_args
        params = call_args.args[1]
        assert params["start"] == datetime(2026, 7, 1, tzinfo=UTC)
        assert params["end"] == datetime(2026, 8, 1, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_uploads_scope_filters_to_records_with_audio(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "uploads"})

        params = factory.session.execute.call_args.args[1]
        assert params["want_sittings"] is False
        assert params["want_records"] is True
        assert params["uploads_only"] is True

    @pytest.mark.asyncio
    async def test_sittings_scope_excludes_records(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "sittings"})

        params = factory.session.execute.call_args.args[1]
        assert params["want_sittings"] is True
        assert params["want_records"] is False

    @pytest.mark.asyncio
    async def test_unknown_scope_returns_helpful_message(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "bogus"})

        assert "Unknown scope" in result

    @pytest.mark.asyncio
    async def test_invalid_period_returns_error_text_not_exception(self):
        factory = _make_session_factory([])
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "sometime", "scope": "all"})

        assert "Could not parse period" in result

    @pytest.mark.asyncio
    async def test_db_failure_returns_friendly_message_not_exception(self):
        factory = _make_failing_session_factory()
        collector: list[RegistryReference] = []
        tool = _make_recent_activity_tool(factory, collector, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert "Could not look up" in result
