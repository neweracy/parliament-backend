"""Correction_Evidence: per-transcript-version audit of postprocessing changes.

Creates ``correction_evidence``, the persisted, per-transcript-version record of
what the correction pipeline changed and where, addressable back to the source
words it changed. One row is written per Correction_Entry, keyed to a transcript
id + version, and read back into the editor so every automated edit to the
official record is reviewable (transcript-evidence-navigation feature).

The change is purely additive and backward compatible:
- it introduces a brand-new table and touches no existing table or column;
- there is no backfill — a pre-feature transcript version simply has no rows
  here, which the editor treats as Baseline "no evidence" (Req 10.6);
- the only write path is the Postprocess_Service; the Gateway never writes it.

No existing migration is modified — this file chains onto the current head (011).
Confirm the current revision (``alembic current``) before applying in any
environment.

Revision ID: 012
Revises: 011
Create Date: 2026-09-30

Requirements: 1.6, 1.10, 8.8, 10.6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One row per Correction_Entry. The evidence is keyed to (transcript_id,
    # version): a transcript version accumulates zero or more entries, and a
    # version with none is Baseline "no evidence" by construction (no backfill).
    op.create_table(
        "correction_evidence",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "transcript_id",
            sa.BigInteger,
            sa.ForeignKey("transcript.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Part of the evidence key alongside transcript_id. The stable
        # Source_Word_Ids are meaningful only within a single transcript
        # version, so the version is carried explicitly rather than inferred.
        sa.Column("version", sa.Integer, nullable=False),
        # Stable range identity assigned before corrections run.
        sa.Column("source_span_id", sa.Text, nullable=False),
        # The covered Source_Word_Ids. Stored as jsonb (like transcript.entities
        # / transcript.word_timings) so the list[str] round-trips without an
        # array-element type coercion at the boundary.
        sa.Column(
            "source_word_ids",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("original", sa.Text, nullable=False),
        sa.Column("corrected", sa.Text, nullable=False),
        # Immutable ASR timing (seconds). Nullable because a Source_Word may omit
        # timing; the ASR-supplied values are never rewritten by a correction.
        sa.Column("source_start", sa.Float, nullable=True),
        sa.Column("source_end", sa.Float, nullable=True),
        # Closed set {rule, year, llm}.
        sa.Column("correction_stage", sa.Text, nullable=False),
        # Closed set {applied, vetoed}.
        sa.Column("correction_outcome", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        # The acceptance threshold the confidence was compared against; omitted
        # (NULL) where the producing stage supplied none.
        sa.Column("threshold", sa.Float, nullable=True),
        # Entity classification, present only where the correction resolved to an
        # entity.
        sa.Column("entity_kind", sa.Text, nullable=True),
        sa.Column("entity_type", sa.Text, nullable=True),
        # Provenance, recorded only where the producing stage supplied it (no
        # placeholders).
        sa.Column("correlation_id", sa.Text, nullable=True),
        sa.Column("dataset_version", sa.Text, nullable=True),
        sa.Column("model_id", sa.Text, nullable=True),
        sa.CheckConstraint(
            "correction_stage IN ('rule', 'year', 'llm')",
            name="ck_correction_evidence_stage",
        ),
        sa.CheckConstraint(
            "correction_outcome IN ('applied', 'vetoed')",
            name="ck_correction_evidence_outcome",
        ),
    )

    # Evidence is always read by (transcript_id, version), so the composite
    # index serves the sole read path.
    op.create_index(
        "ix_correction_evidence_transcript_version",
        "correction_evidence",
        ["transcript_id", "version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_correction_evidence_transcript_version",
        table_name="correction_evidence",
    )
    op.drop_table("correction_evidence")
