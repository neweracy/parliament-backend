"""Unit tests for Sitting_Scope resolution in the pipeline (task 4.5, Req 8).

Covers the pipeline-side orchestration:

* Resolution at most once, before dispatching the Rule_Stage (Req 8.7).
* No resolution when the flag is off, the id is absent, or the id is
  whitespace-only (Req 8.2).
* At most one ``sitting_scope.unavailable`` debug event when the lookup yields
  no usable scope (Req 8.5).
* The resolved member set is threaded into ``_run_rule_stages`` in memory so the
  Rule_Stage issues no DB read of its own (Req 8.8).

All DB access is mocked; no test issues a network call or touches AWS.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.correction.engine import TextCorrectionResult, WordCorrectionResult
from app.correction.gates import GateTally
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import MatchIndex
from app.models.request import CorrectionRequest
from app.pipeline import _resolve_sitting_scope, run_pipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_snapshot() -> DatasetSnapshot:
    index = MatchIndex(
        canonical_map={},
        fused_map={},
        phonetic_map={},
        surname_map={},
        initial_surname_map={},
        entity_kind_map={},
        entity_type_map={},
        party_abbr_map={},
        alias_ordinal={},
        length_buckets={},
        bk_tree=None,
    )
    return DatasetSnapshot(
        version="2026-01-01T00:00:00+00:00",
        records=(),
        record_count=0,
        loaded_at=datetime(2026, 1, 1, tzinfo=UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )


def _make_cache(snapshot: DatasetSnapshot | None) -> DatasetCache:
    cache = MagicMock(spec=DatasetCache)
    cache.get_snapshot.return_value = snapshot
    return cache


def _settings(sitting_scope_enabled: bool) -> Settings:
    return Settings(
        service_token="t",
        database_url="postgresql://x",
        sitting_scope_enabled=sitting_scope_enabled,
    )


def _session_factory() -> MagicMock:
    """A callable returning a mock AsyncSession with an async close()."""
    session = MagicMock()
    session.close = AsyncMock()
    factory = MagicMock(return_value=session)
    return factory


# ---------------------------------------------------------------------------
# _resolve_sitting_scope (Req 8.2, 8.5, 8.7)
# ---------------------------------------------------------------------------


class TestResolveSittingScopeHelper:
    @pytest.mark.asyncio
    async def test_flag_off_no_lookup(self):
        req = CorrectionRequest(transcript="hi", sittingId="42")
        with patch(
            "app.pipeline.resolve_sitting_scope", new=AsyncMock()
        ) as resolver:
            scope = await _resolve_sitting_scope(
                req, _settings(False), _session_factory()
            )
        assert scope is None
        resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_settings_no_lookup(self):
        req = CorrectionRequest(transcript="hi", sittingId="42")
        with patch(
            "app.pipeline.resolve_sitting_scope", new=AsyncMock()
        ) as resolver:
            scope = await _resolve_sitting_scope(req, None, _session_factory())
        assert scope is None
        resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_absent_id_no_lookup(self):
        req = CorrectionRequest(transcript="hi")
        with patch(
            "app.pipeline.resolve_sitting_scope", new=AsyncMock()
        ) as resolver:
            scope = await _resolve_sitting_scope(
                req, _settings(True), _session_factory()
            )
        assert scope is None
        resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_whitespace_only_id_no_lookup(self):
        req = CorrectionRequest(transcript="hi", sittingId="   ")
        with patch(
            "app.pipeline.resolve_sitting_scope", new=AsyncMock()
        ) as resolver:
            scope = await _resolve_sitting_scope(
                req, _settings(True), _session_factory()
            )
        assert scope is None
        resolver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_session_logs_unavailable(self):
        req = CorrectionRequest(transcript="hi", sittingId="42")
        with patch("app.pipeline.logger") as logger:
            scope = await _resolve_sitting_scope(req, _settings(True), None)
        assert scope is None
        logger.debug.assert_called_once()
        assert logger.debug.call_args.args[0] == "sitting_scope.unavailable"

    @pytest.mark.asyncio
    async def test_returns_scope_when_resolver_returns_members(self):
        req = CorrectionRequest(transcript="hi", sittingId="42")
        factory = _session_factory()
        members = frozenset({"Ken Ofori-Atta"})
        with patch(
            "app.pipeline.resolve_sitting_scope",
            new=AsyncMock(return_value=members),
        ) as resolver:
            scope = await _resolve_sitting_scope(req, _settings(True), factory)
        assert scope == members
        resolver.assert_awaited_once()
        # The sitting id is passed stripped.
        assert resolver.await_args.args[1] == "42"
        # The session is always closed.
        factory.return_value.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_scope_logs_unavailable_and_returns_none(self):
        req = CorrectionRequest(transcript="hi", sittingId="42")
        with (
            patch(
                "app.pipeline.resolve_sitting_scope",
                new=AsyncMock(return_value=frozenset()),
            ),
            patch("app.pipeline.logger") as logger,
        ):
            scope = await _resolve_sitting_scope(
                req, _settings(True), _session_factory()
            )
        assert scope is None
        logger.debug.assert_called_once()
        assert logger.debug.call_args.args[0] == "sitting_scope.unavailable"

    @pytest.mark.asyncio
    async def test_resolver_error_returns_none(self):
        # A resolver that raises → unavailable, None (Req 8.10).
        req = CorrectionRequest(transcript="hi", sittingId="42")
        with patch(
            "app.pipeline.resolve_sitting_scope",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            scope = await _resolve_sitting_scope(
                req, _settings(True), _session_factory()
            )
        assert scope is None


# ---------------------------------------------------------------------------
# run_pipeline threads the resolved scope and resolves at most once
# (Req 8.7, 8.8)
# ---------------------------------------------------------------------------


class TestRunPipelineSittingScope:
    @pytest.mark.asyncio
    async def test_scope_resolved_once_and_threaded_into_rule_stage(self):
        req = CorrectionRequest(
            transcript="hello", sittingId="42", correlationId="c1"
        )
        cache = _make_cache(_make_snapshot())
        members = frozenset({"Ken Ofori-Atta"})

        with (
            patch(
                "app.pipeline._resolve_sitting_scope",
                new=AsyncMock(return_value=members),
            ) as resolve,
            patch("app.pipeline._run_rule_stages") as run_rule,
        ):
            # _run_rule_stages is synchronous; give it a minimal return tuple.
            run_rule.return_value = (
                TextCorrectionResult(
                    text="hello", corrections=[], entities_found=[]
                ),
                WordCorrectionResult(words=[], corrections=[], entities_found=[]),
                "hello",
                [],
                0,
                GateTally(),
            )
            await run_pipeline(req, cache, settings=_settings(True))

        # Resolved exactly once (Req 8.7).
        resolve.assert_awaited_once()
        # The resolved member set was passed positionally into _run_rule_stages
        # in memory (Req 8.8):
        # (request, snapshot, settings, lexicon, sitting_scope).
        assert run_rule.call_args.args[4] == members
