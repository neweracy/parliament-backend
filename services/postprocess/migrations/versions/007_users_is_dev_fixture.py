"""Add is_dev_fixture column to users table.

Revision ID: 007
Revises: 006
Create Date: 2026-08-20

scripts/seed_users.py upserts development fixture accounts (Admin, Chief
Editor, Supervisor, Editor, Viewer) and marks each row with
is_dev_fixture=true so seeded accounts can be identified and cleaned up
separately from real accounts. The column was referenced by the seed script
but never added by a migration, so a freshly migrated database fails the
seed with "relation users has no column is_dev_fixture" (surfaced by the
script only as a generic rollback message).

Defaults to false so existing/real accounts are never mistaken for fixtures.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_dev_fixture",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_dev_fixture")
