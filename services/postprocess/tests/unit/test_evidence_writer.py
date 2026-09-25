"""Unit tests for app/correction/evidence_writer.py — evidence persistence.

Feature: transcript-evidence-navigation (task 4.1, Req 1.6, 8.5).

Covers persisting a built CorrectionEvidence to the correction_evidence table:
one row per CorrectionEntry keyed to (transcript_id, version), parameterized
statements only (never string-interpolated SQL — the government-project
SQL-injection rule), an empty-entries no-op (Baseline no-evidence, Req 10.5),
and a swallowed write failure (design Decision 2 / write-ownership tradeoff).

The DB session is mocked — no network or AWS call is made. A boundary guard
asserts the writer touches nothing but the injected session factory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.correction.evidence_writer import (
    _INSERT_ENTRY_SQL,
    persist_correction_evidence,
)
from app.models.evidence import (
    CorrectionEntry,
    CorrectionEvidence,
    CorrectionOutcome,
    CorrectionStage,
)


def _mock_session_factory() -> tuple[MagicMock, AsyncMock]:
    """Build a mocked async session factory and return (factory, session).

    The session supports ``async with factory() as session:`` and
    ``async with session.begin():`` and records ``execute`` calls, matching the
    context-manager shape the history writer / ingestion worker use.
    """
    session = AsyncMock()
    session.execute = AsyncMock()

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)
    # session.begin() is a sync call returning an async context manager.
    session.begin = MagicMock(return_value=begin_ctx)

    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session_ctx)
    return factory, session


def _entry(
    span_id: str,
    original: str,
    corrected: str,
    *,
    word_ids: list[str] | None = None,
    stage: CorrectionStage = CorrectionStage.rule,
    outcome: CorrectionOutcome = CorrectionOutcome.applied,
    source_start: float | None = 1.0,
    source_end: float | None = 2.0,
) -> CorrectionEntry:
    return CorrectionEntry(
        source_span_id=span_id,
        source_word_ids=word_ids if word_ids is not None else ["w0"],
        original=original,
        corrected=corrected,
        source_start=source_start,
        source_end=source_end,
        correction_stage=stage,
        correction_outcome=outcome,
        confidence=0.9,
    )


class TestPersistCorrectionEvidence:
    @pytest.mark.asyncio
    async def test_writes_one_row_per_entry_keyed_to_transcript_version(self):
        """One row per CorrectionEntry, each keyed to (transcript_id, version)."""
        factory, session = _mock_session_factory()
        evidence = CorrectionEvidence(
            transcript_id=42,
            version=3,
            entries=[
                _entry("s:w0-w0", "Akra", "Accra", word_ids=["w0"]),
                _entry("s:w2-w3", "Kumase town", "Kumasi", word_ids=["w2", "w3"]),
            ],
        )

        written = await persist_correction_evidence(factory, evidence)

        assert written == 2
        session.execute.assert_awaited_once()
        stmt, params = session.execute.await_args.args
        # A single executemany with one param mapping per entry.
        assert isinstance(params, list)
        assert len(params) == 2
        for p in params:
            assert p["transcript_id"] == 42
            assert p["version"] == 3
        assert params[0]["source_span_id"] == "s:w0-w0"
        assert params[0]["original"] == "Akra"
        assert params[0]["corrected"] == "Accra"
        assert params[1]["source_span_id"] == "s:w2-w3"

    @pytest.mark.asyncio
    async def test_uses_parameterized_statement_no_interpolation(self):
        """SQL is a fixed parameterized statement; values arrive as bound params."""
        factory, session = _mock_session_factory()
        # A value that would break naive string interpolation must ride as a
        # bound parameter, never spliced into the SQL text.
        evidence = CorrectionEvidence(
            transcript_id=1,
            version=1,
            entries=[_entry("s:w0-w0", "O'Brien; DROP TABLE transcript;--", "O'Brien")],
        )

        await persist_correction_evidence(factory, evidence)

        stmt, params = session.execute.await_args.args
        sql = stmt.text
        # All placeholders are named binds; the injection-y text is only a value.
        assert ":transcript_id" in sql
        assert ":source_span_id" in sql
        assert "DROP TABLE" not in sql
        assert params[0]["original"] == "O'Brien; DROP TABLE transcript;--"

    @pytest.mark.asyncio
    async def test_source_word_ids_bound_as_json_string(self):
        """source_word_ids (list[str]) is bound as a JSON string for the jsonb column."""
        factory, session = _mock_session_factory()
        evidence = CorrectionEvidence(
            transcript_id=7,
            version=1,
            entries=[_entry("s:w0-w1", "a b", "ab", word_ids=["w0", "w1"])],
        )

        await persist_correction_evidence(factory, evidence)

        _, params = session.execute.await_args.args
        assert params[0]["source_word_ids"] == '["w0", "w1"]'

    @pytest.mark.asyncio
    async def test_enum_values_persist_as_plain_strings(self):
        """correction_stage / correction_outcome persist as their string values."""
        factory, session = _mock_session_factory()
        evidence = CorrectionEvidence(
            transcript_id=9,
            version=2,
            entries=[
                _entry(
                    "s:w0-w0",
                    "twenty twenty four",
                    "2024",
                    stage=CorrectionStage.year,
                    outcome=CorrectionOutcome.applied,
                ),
                _entry(
                    "s:w1-w1",
                    "Kofi",
                    "Kofi",
                    stage=CorrectionStage.llm,
                    outcome=CorrectionOutcome.vetoed,
                ),
            ],
        )

        await persist_correction_evidence(factory, evidence)

        _, params = session.execute.await_args.args
        assert params[0]["correction_stage"] == "year"
        assert params[0]["correction_outcome"] == "applied"
        assert params[1]["correction_stage"] == "llm"
        assert params[1]["correction_outcome"] == "vetoed"

    @pytest.mark.asyncio
    async def test_vetoed_entry_retains_original_and_timing(self):
        """A vetoed entry is written with its original text + timing unchanged (Req 1.9)."""
        factory, session = _mock_session_factory()
        evidence = CorrectionEvidence(
            transcript_id=5,
            version=1,
            entries=[
                _entry(
                    "s:w0-w0",
                    "Tema",
                    "Accra",
                    stage=CorrectionStage.llm,
                    outcome=CorrectionOutcome.vetoed,
                    source_start=4.0,
                    source_end=4.5,
                )
            ],
        )

        await persist_correction_evidence(factory, evidence)

        _, params = session.execute.await_args.args
        assert params[0]["original"] == "Tema"
        assert params[0]["source_start"] == 4.0
        assert params[0]["source_end"] == 4.5

    @pytest.mark.asyncio
    async def test_empty_entries_writes_nothing(self):
        """A version with no entries is Baseline no-evidence — no write (Req 10.5)."""
        factory, session = _mock_session_factory()
        evidence = CorrectionEvidence(transcript_id=1, version=1, entries=[])

        written = await persist_correction_evidence(factory, evidence)

        assert written == 0
        session.execute.assert_not_awaited()
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_failure_is_swallowed_and_returns_zero(self):
        """A failing write logs and returns 0, never raising (design Decision 2)."""
        factory, session = _mock_session_factory()
        session.execute.side_effect = RuntimeError("db down")
        evidence = CorrectionEvidence(
            transcript_id=1,
            version=1,
            entries=[_entry("s:w0-w0", "a", "b")],
        )

        written = await persist_correction_evidence(factory, evidence)

        assert written == 0

    @pytest.mark.asyncio
    async def test_no_network_or_aws_call(self, monkeypatch):
        """Boundary guard: the writer makes no network/AWS call (Property 10).

        Any attempt to open a socket fails the test. The writer must reach the
        DB only through the injected session factory.
        """
        import socket

        def _forbidden_socket(*_args, **_kwargs):  # pragma: no cover - guard
            raise AssertionError("network access is forbidden in this test")

        monkeypatch.setattr(socket, "socket", _forbidden_socket)

        factory, _ = _mock_session_factory()
        evidence = CorrectionEvidence(
            transcript_id=1,
            version=1,
            entries=[_entry("s:w0-w0", "a", "b")],
        )

        written = await persist_correction_evidence(factory, evidence)
        assert written == 1


def test_insert_sql_is_fully_parameterized():
    """The module's INSERT statement uses only named binds (no interpolation)."""
    # Every VALUES slot is a named placeholder; no f-string / % / .format markers.
    assert "%" not in _INSERT_ENTRY_SQL
    assert "{" not in _INSERT_ENTRY_SQL
    assert _INSERT_ENTRY_SQL.count(":") == 17  # one per column written
