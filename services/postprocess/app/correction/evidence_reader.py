"""Correction_Evidence read path — load persisted evidence for a version.

Feature: transcript-evidence-navigation (task 5.1, Req 10.4, 10.5).

This module is the read-only sibling of ``evidence_writer.py``. It loads the
persisted ``correction_evidence`` rows for a ``(transcript_id, version)`` and
rebuilds a :class:`~app.models.evidence.CorrectionEvidence` for the read
endpoint to serialize.

Read-only / boundary (Req 8.5, 8.6)
-----------------------------------
The Postprocess_Service is the sole owner of the correction tables; this reader
issues only ``SELECT`` statements and never writes. The Gateway reads this
endpoint's output — it never queries the table itself.

Security (SQL injection prevention)
-----------------------------------
Every statement uses SQLAlchemy async with **parameterized** bound values
(``text(...)`` + a params mapping) — never string interpolation, matching the
existing RAG retriever / history writer read patterns.

No-evidence semantics (Req 10.5)
--------------------------------
A ``(transcript_id, version)`` that carries no rows resolves to a
:class:`CorrectionEvidence` with an empty ``entries`` list — the editor treats
that identically to Baseline "no evidence" (never an error).

Version resolution
------------------
When no explicit version is supplied, the latest version that carries evidence
for the transcript is used (``MAX(version)``). When the transcript has no
evidence at all, the resolved version is ``0`` and ``entries`` is empty.
"""

from __future__ import annotations

import json

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.evidence import (
    CorrectionEntry,
    CorrectionEvidence,
    CorrectionOutcome,
    CorrectionStage,
)

logger = structlog.get_logger("correction.evidence_reader")

# Read the newest version that carries evidence for the transcript. Parameterized
# (never interpolated) — government-project SQL-injection rule.
_LATEST_VERSION_SQL = (
    "SELECT MAX(version) AS version FROM correction_evidence "
    "WHERE transcript_id = :transcript_id"
)

# Read every entry for a (transcript_id, version), ordered deterministically so
# the response is stable across calls. The composite index
# (transcript_id, version) from migration 012 serves this read.
_SELECT_ENTRIES_SQL = (
    "SELECT source_span_id, source_word_ids, original, corrected, "
    "source_start, source_end, correction_stage, correction_outcome, "
    "confidence, threshold, entity_kind, entity_type, correlation_id, "
    "dataset_version, model_id "
    "FROM correction_evidence "
    "WHERE transcript_id = :transcript_id AND version = :version "
    "ORDER BY id"
)


def _coerce_word_ids(raw: object) -> list[str]:
    """Coerce a stored ``source_word_ids`` value into a ``list[str]``.

    The column is ``jsonb`` written from a JSON string, so a driver may return
    either an already-decoded ``list`` or the raw JSON ``str``. Both are
    normalized here; anything unexpected resolves to an empty list rather than
    raising, so one malformed row never fails the whole read.
    """
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    return []


async def load_correction_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    transcript_id: int,
    version: int | None = None,
) -> CorrectionEvidence:
    """Load the persisted Correction_Evidence for a transcript version.

    Parameters
    ----------
    session_factory:
        The async session factory (``app.state.session_factory``). The sole DB
        access channel; identical to the history writer / RAG retriever.
    transcript_id:
        The transcript whose evidence to read.
    version:
        The Transcript_Version to read. When ``None``, the latest version that
        carries evidence is resolved via ``MAX(version)``; a transcript with no
        evidence resolves to version ``0`` and empty ``entries`` (Req 10.5).

    Returns
    -------
    CorrectionEvidence
        The evidence keyed to ``(transcript_id, resolved_version)``. ``entries``
        is empty when the version carries none (Baseline no-evidence, Req 10.5).
    """
    async with session_factory() as session:
        resolved_version = version
        if resolved_version is None:
            latest = await session.execute(
                text(_LATEST_VERSION_SQL),
                {"transcript_id": transcript_id},
            )
            row = latest.fetchone()
            # MAX over no rows is NULL -> no evidence for the transcript.
            resolved_version = row[0] if row is not None and row[0] is not None else 0

        if resolved_version == 0 and version is None:
            # No evidence rows exist for this transcript at all (Req 10.5).
            return CorrectionEvidence(
                transcript_id=transcript_id, version=0, entries=[]
            )

        result = await session.execute(
            text(_SELECT_ENTRIES_SQL),
            {"transcript_id": transcript_id, "version": resolved_version},
        )
        rows = result.mappings().all()

    entries = [
        CorrectionEntry(
            source_span_id=row["source_span_id"],
            source_word_ids=_coerce_word_ids(row["source_word_ids"]),
            original=row["original"],
            corrected=row["corrected"],
            source_start=row["source_start"],
            source_end=row["source_end"],
            correction_stage=CorrectionStage(row["correction_stage"]),
            correction_outcome=CorrectionOutcome(row["correction_outcome"]),
            confidence=row["confidence"],
            threshold=row["threshold"],
            entity_kind=row["entity_kind"],
            entity_type=row["entity_type"],
            correlation_id=row["correlation_id"],
            dataset_version=row["dataset_version"],
            model_id=row["model_id"],
        )
        for row in rows
    ]

    return CorrectionEvidence(
        transcript_id=transcript_id,
        version=resolved_version,
        entries=entries,
    )
