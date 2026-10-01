"""Unit tests for Sitting_Scope resolution and the sitting_id request field.

Covers task 4.5 (Requirement 8):

* The optional ``sittingId`` / ``sitting_id`` request field (Req 8.1): alias,
  length bounds, whitespace handling.
* ``resolve_sitting_scope`` (Req 8.3, 8.5, 8.10): a lookup that returns a scope,
  a lookup that returns no scope, a lookup that errors, and a lookup that times
  out — all with a mocked async session, never a real database.

All database access is mocked; no test issues a network call or touches AWS.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.datasets.sittings import resolve_sitting_scope
from app.models.request import CorrectionRequest

# ---------------------------------------------------------------------------
# Request field: sittingId / sitting_id (Req 8.1)
# ---------------------------------------------------------------------------


class TestSittingIdField:
    """The optional Sitting_Scope identifier on CorrectionRequest (Req 8.1)."""

    def test_absent_defaults_to_none(self):
        req = CorrectionRequest(transcript="hello")
        assert req.sitting_id is None

    def test_explicit_null_is_none(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "sittingId": None}
        )
        assert req.sitting_id is None

    def test_camelcase_alias_accepted(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "sittingId": "sitting-42"}
        )
        assert req.sitting_id == "sitting-42"

    def test_snake_case_field_accepted(self):
        # populate_by_name=True allows the Python field name too.
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "sitting_id": "sitting-42"}
        )
        assert req.sitting_id == "sitting-42"

    def test_min_length_one_accepted(self):
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "sittingId": "x"}
        )
        assert req.sitting_id == "x"

    def test_max_length_128_accepted(self):
        value = "a" * 128
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "sittingId": value}
        )
        assert req.sitting_id == value

    def test_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            CorrectionRequest.model_validate(
                {"transcript": "hello", "sittingId": "a" * 129}
            )

    def test_empty_string_rejected_by_min_length(self):
        # An empty string violates min_length=1 (whitespace-only strings that
        # are non-empty validate here and are treated as absent by the pipeline).
        with pytest.raises(ValidationError):
            CorrectionRequest.model_validate(
                {"transcript": "hello", "sittingId": ""}
            )

    def test_whitespace_only_validates_as_string(self):
        # A whitespace-only value (length >= 1) passes field validation; the
        # pipeline treats it as absent (Req 8.2), not the model.
        req = CorrectionRequest.model_validate(
            {"transcript": "hello", "sittingId": "   "}
        )
        assert req.sitting_id == "   "

    def test_field_is_additive_backward_compatible(self):
        # A body that omits sittingId still validates and behaves as before.
        req = CorrectionRequest.model_validate({"transcript": "hello"})
        assert req.sitting_id is None
        assert req.transcript == "hello"


# ---------------------------------------------------------------------------
# Sitting_Scope resolver (Req 8.3, 8.5, 8.10)
# ---------------------------------------------------------------------------


def _session_returning(rows: list[tuple[str]]) -> MagicMock:
    """Build a mock AsyncSession whose execute returns *rows*."""
    result = MagicMock()
    result.fetchall.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


class TestResolveSittingScope:
    """resolve_sitting_scope maps DB outcomes to a member frozenset (Req 8)."""

    @pytest.mark.asyncio
    async def test_returns_scope_when_rows_present(self):
        session = _session_returning(
            [("Ken Ofori-Atta",), ("John Mahama",), ("Accra",)]
        )
        scope = await resolve_sitting_scope(session, "42")
        assert scope == frozenset(
            {"Ken Ofori-Atta", "John Mahama", "Accra"}
        )
        # A parameterised query is issued (Req: no interpolation).
        session.execute.assert_awaited_once()
        _text_arg, params = session.execute.await_args.args
        assert params == {"sitting_id": "42"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self):
        # Identifier matches no sitting / empty member set (Req 8.5).
        session = _session_returning([])
        scope = await resolve_sitting_scope(session, "999")
        assert scope == frozenset()

    @pytest.mark.asyncio
    async def test_filters_falsy_names(self):
        session = _session_returning([("Ken Ofori-Atta",), (None,), ("",)])
        scope = await resolve_sitting_scope(session, "42")
        assert scope == frozenset({"Ken Ofori-Atta"})

    @pytest.mark.asyncio
    async def test_returns_empty_on_query_error(self):
        # Lookup error → unavailable, empty (Req 8.5, 8.10). Simulates a missing
        # table or a connection failure.
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=RuntimeError("relation does not exist")
        )
        scope = await resolve_sitting_scope(session, "42")
        assert scope == frozenset()

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        # Lookup exceeds the timeout → unavailable, empty (Req 8.5, 8.10).
        async def _slow_execute(*_args, **_kwargs):
            await asyncio.sleep(1.0)
            result = MagicMock()
            result.fetchall.return_value = [("Ken Ofori-Atta",)]
            return result

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_slow_execute)
        scope = await resolve_sitting_scope(session, "42", timeout_s=0.01)
        assert scope == frozenset()

    @pytest.mark.asyncio
    async def test_never_raises_on_common_errors(self):
        # A common error mode yields an empty frozenset, never an exception
        # (Req 8.10).
        session = MagicMock()
        session.execute = AsyncMock(side_effect=ValueError("boom"))
        scope = await resolve_sitting_scope(session, "42")
        assert scope == frozenset()
