"""Correction_Evidence persistence — write one row per Correction_Entry.

Feature: transcript-evidence-navigation (task 4.1, Req 1.6, 8.5).

This module is the sole writer of the ``correction_evidence`` table. It takes a
built :class:`~app.models.evidence.CorrectionEvidence` (task 2.2) and persists
one row per :class:`~app.models.evidence.CorrectionEntry`, keyed to the
``(transcript_id, version)`` the evidence carries (migration 012 created the
table and its ``(transcript_id, version)`` index).

Write ownership (Req 1.6, 8.5, 8.6)
-----------------------------------
Only the Postprocess_Service writes correction tables; the Gateway never writes
this table and never computes evidence of its own. This module runs inside the
Python postprocess/ingestion flow (the flow already triggered with a
``transcript_id``), so it upholds that boundary.

Security (SQL injection prevention)
-----------------------------------
Every statement uses SQLAlchemy async with **parameterized** bound values
(``text(...)`` + a params mapping) — never string interpolation. This matches
the existing ``app/history/writer.py`` and ``app/rag/ingestion.py`` write
patterns. ``source_word_ids`` is a ``list[str]`` bound as a JSON string so it
round-trips into the ``jsonb`` column without an array-element coercion at the
boundary.

Additivity (Req 10.5, 10.6)
---------------------------
The write is additive: it inserts into a brand-new table and touches no existing
row or table. A transcript version that produces no entries writes no rows,
which the editor treats as Baseline "no evidence" (Req 10.5) — a not-yet-persisted
state is no-evidence, never an error (design Decision 2 / write-ownership
tradeoff). Persistence failures are logged and swallowed so evidence lagging or
failing never blocks the transcript from appearing or the ingestion flow from
completing.
"""

from __future__ import annotations

import json

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.evidence import CorrectionEvidence

logger = structlog.get_logger("correction.evidence_writer")

# Insert one Correction_Entry row. Parameterized (never interpolated) —
# government project SQL-injection rule. ``source_word_ids`` is bound as a JSON
# string for the jsonb column, mirroring how transcript.entities/word_timings
# round-trip. ``id`` is DB-assigned (BigInteger autoincrement).
_INSERT_ENTRY_SQL = (
    "INSERT INTO correction_evidence "
    "(transcript_id, version, source_span_id, source_word_ids, original, "
    "corrected, source_start, source_end, correction_stage, correction_outcome, "
    "confidence, threshold, entity_kind, entity_type, correlation_id, "
    "dataset_version, model_id) "
    "VALUES "
    "(:transcript_id, :version, :source_span_id, :source_word_ids, :original, "
    ":corrected, :source_start, :source_end, :correction_stage, "
    ":correction_outcome, :confidence, :threshold, :entity_kind, :entity_type, "
    ":correlation_id, :dataset_version, :model_id)"
)


async def persist_correction_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    evidence: CorrectionEvidence,
) -> int:
    """Persist a version's Correction_Evidence, one row per entry (Req 1.6, 8.5).

    Writes one ``correction_evidence`` row per :class:`CorrectionEntry` in
    *evidence*, keyed to ``evidence.transcript_id`` and ``evidence.version``. The
    whole batch is written in a single transaction so a version's evidence lands
    atomically. Enum values (``correction_stage``, ``correction_outcome``,
    ``mapping_confidence``) are ``StrEnum`` so they persist as their plain string
    values, matching the table's CHECK constraints.

    A ``vetoed`` entry is persisted with its original text and immutable timing
    unchanged (Req 1.9) — this writer never rewrites an entry's ``source_start``
    / ``source_end``; it stores exactly what the builder produced.

    Parameters
    ----------
    session_factory:
        The async session factory (``app.state.session_factory``). The sole DB
        access channel; identical to the history writer / ingestion worker.
    evidence:
        The built :class:`CorrectionEvidence` for one transcript version.

    Returns
    -------
    int
        The number of rows written. ``0`` when *evidence* carries no entries
        (Baseline no-evidence, Req 10.5) or when the write failed and was
        swallowed — the caller treats a not-yet-persisted state as no-evidence,
        never an error (design Decision 2).
    """
    entries = evidence.entries
    if not entries:
        # No Postprocessing_Change for this version — Baseline "no evidence"
        # by construction (Req 10.5). Nothing to write.
        return 0

    params = [
        {
            "transcript_id": evidence.transcript_id,
            "version": evidence.version,
            "source_span_id": entry.source_span_id,
            # Bind the list[str] as a JSON string for the jsonb column so it
            # round-trips without an array-element coercion at the boundary.
            "source_word_ids": json.dumps(entry.source_word_ids),
            "original": entry.original,
            "corrected": entry.corrected,
            "source_start": entry.source_start,
            "source_end": entry.source_end,
            # StrEnum -> its string value for the text column / CHECK constraint.
            "correction_stage": str(entry.correction_stage),
            "correction_outcome": str(entry.correction_outcome),
            "confidence": entry.confidence,
            "threshold": entry.threshold,
            "entity_kind": entry.entity_kind,
            "entity_type": entry.entity_type,
            "correlation_id": entry.correlation_id,
            "dataset_version": entry.dataset_version,
            "model_id": entry.model_id,
        }
        for entry in entries
    ]

    try:
        async with session_factory() as session, session.begin():
            await session.execute(text(_INSERT_ENTRY_SQL), params)
        logger.debug(
            "correction_evidence.persisted",
            transcript_id=evidence.transcript_id,
            version=evidence.version,
            rows=len(params),
        )
        return len(params)
    except Exception:
        # A failing write must never block the ingestion flow or the transcript
        # from appearing; a not-yet-persisted state is no-evidence (Baseline),
        # not an error (design Decision 2 / write-ownership tradeoff).
        logger.error(
            "correction_evidence.persist_failed",
            transcript_id=evidence.transcript_id,
            version=evidence.version,
            rows=len(params),
            exc_info=True,
        )
        return 0
