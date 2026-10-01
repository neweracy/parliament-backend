"""Correction_History outcome marker.

Adds an ``outcome`` column to ``correction_history`` recording the disposition
of each persisted correction: ``applied`` for a correction the pipeline kept,
or ``vetoed`` for a rule correction the LLM_Refiner rejected and restored (the
veto path is wired in a later task; this migration only provisions the column).

The change is additive and backward compatible:
- the column is added with a server default of ``'applied'`` so existing rows
  and any in-flight INSERT that omits ``outcome`` remain valid;
- every pre-existing row is back-filled to ``'applied'`` (Req 9.6);
- a CHECK constraint restricts values to the closed set ``applied`` | ``vetoed``.

No existing migration is modified — this file chains onto the current head.

Revision ID: 011
Revises: 010
Create Date: 2026-09-24

Requirements: 9.6, 11.7, 13.8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the column with a server default so the ALTER back-fills every
    # existing row to 'applied' in one statement and any INSERT that omits
    # 'outcome' stays valid during a rolling deploy.
    op.add_column(
        "correction_history",
        sa.Column(
            "outcome",
            sa.Text,
            nullable=False,
            server_default=sa.text("'applied'"),
        ),
    )

    # Belt-and-braces: ensure any row that somehow predates the default is set
    # to 'applied' (Req 9.6). Harmless when the server_default already covered
    # every row.
    op.execute("UPDATE correction_history SET outcome = 'applied' WHERE outcome IS NULL")

    # Restrict the marker to the closed set {applied, vetoed}.
    op.create_check_constraint(
        "correction_history_outcome_check",
        "correction_history",
        "outcome IN ('applied', 'vetoed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "correction_history_outcome_check",
        "correction_history",
        type_="check",
    )
    op.drop_column("correction_history", "outcome")
