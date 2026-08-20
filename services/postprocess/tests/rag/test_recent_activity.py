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
    _PeriodParseError,
    _make_recent_activity_tool,
    _resolve_period,
)


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


def _make_failing_session_factory():
    """Build a fake session_factory whose execute() raises."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=context_manager)


_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


class TestResolvePeriod:
    def test_default_recent_is_last_7_days(self):
        start, end, label = _resolve_period("", now=_NOW)
        assert (end - start).days == 7
        assert end == _NOW
        assert label == "the last 7 days"

    def test_recent_keyword(self):
        start, end, label = _resolve_period("recent", now=_NOW)
        assert (end - start).days == 7

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


class TestFindRecentActivityTool:
    @pytest.mark.asyncio
    async def test_reports_matching_sittings_and_records(self):
        rows = [
            (
                "sitting",
                1,
                "3rd Sitting",
                None,
                datetime(2026, 8, 18, 10, 0, tzinfo=UTC),
                None,
            ),
            (
                "record",
                5,
                "Morning Session",
                "3rd Sitting",
                datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
                "audio.mp3",
            ),
        ]
        factory = _make_session_factory(rows)
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert "Sitting #1" in result
        assert "3rd Sitting" in result
        assert "Record #5" in result
        assert "audio: audio.mp3" in result
        assert "2 item(s)" in result

    @pytest.mark.asyncio
    async def test_empty_result_reports_nothing_added(self):
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "last month", "scope": "records"})

        assert "Nothing was added" in result
        assert "July 2026" in result

    @pytest.mark.asyncio
    async def test_month_query_resolves_to_correct_range(self):
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "July 2026", "scope": "all"})

        call_args = factory.session.execute.call_args
        params = call_args.args[1]
        assert params["start"] == datetime(2026, 7, 1, tzinfo=UTC)
        assert params["end"] == datetime(2026, 8, 1, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_uploads_scope_filters_to_records_with_audio(self):
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "uploads"})

        params = factory.session.execute.call_args.args[1]
        assert params["want_sittings"] is False
        assert params["want_records"] is True
        assert params["uploads_only"] is True

    @pytest.mark.asyncio
    async def test_sittings_scope_excludes_records(self):
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        await tool.ainvoke({"period": "recent", "scope": "sittings"})

        params = factory.session.execute.call_args.args[1]
        assert params["want_sittings"] is True
        assert params["want_records"] is False

    @pytest.mark.asyncio
    async def test_unknown_scope_returns_helpful_message(self):
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "bogus"})

        assert "Unknown scope" in result

    @pytest.mark.asyncio
    async def test_invalid_period_returns_error_text_not_exception(self):
        factory = _make_session_factory([])
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "sometime", "scope": "all"})

        assert "Could not parse period" in result

    @pytest.mark.asyncio
    async def test_db_failure_returns_friendly_message_not_exception(self):
        factory = _make_failing_session_factory()
        tool = _make_recent_activity_tool(factory, now_fn=lambda: _NOW)

        result = await tool.ainvoke({"period": "recent", "scope": "all"})

        assert "Could not look up" in result
