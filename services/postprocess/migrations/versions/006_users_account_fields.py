"""Add self-service account fields to users: phone and preferences.

Revision ID: 006
Revises: 005
Create Date: 2026-08-04

The Account page lets a user maintain their own contact number and UI
preferences. Both live on the users row rather than a side table because they
are strictly 1:1 with a user and are always read together with the profile.

preferences is jsonb with a '{}' default so an existing row needs no backfill
and the gateway can merge partial objects over its defaults without a null
check on every read.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.Text, nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "preferences",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "preferences")
    op.drop_column("users", "phone")
