"""Audit trail schema: append-only event log and per-user notification watermark.

Creates two tables backing the Audit Trail feature:
- audit_log: append-only record of every notable action in the system. Nothing
  updates or deletes rows in normal operation, so there is no updated_at column.
- user_notification_state: how far each user has read the notifiable stream.

Revision ID: 010
Revises: 009
Create Date: 2026-09-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- audit_log ---
    # Append-only: rows are inserted on every audited action, and nothing in the
    # application updates or deletes them.
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # NULL actor_id means the event was system-originated rather than
        # triggered by a signed-in user.
        sa.Column("actor_id", sa.Text, nullable=True),
        sa.Column(
            "actor_name",
            sa.Text,
            nullable=False,
            server_default=sa.text("'System'"),
        ),
        sa.Column("actor_role", sa.Text, nullable=True),
        # Dotted verb describing what happened, e.g. 'record.edited'.
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=True),
        sa.Column("entity_id", sa.Text, nullable=True),
        sa.Column("entity_label", sa.Text, nullable=True),
        sa.Column("sitting_id", sa.BigInteger, nullable=True),
        sa.Column("sitting_title", sa.Text, nullable=True),
        # Human-readable one-liner rendered directly in the audit trail UI.
        sa.Column("summary", sa.Text, nullable=False),
        # NOTE: "metadata" is a reserved attribute name on SQLAlchemy's
        # declarative base (Base.metadata). It is safe as a raw column name here
        # because this is a Core-level op.create_table, but anyone later mapping
        # this table onto a declarative model MUST alias it, for example
        # event_metadata = Column("metadata", JSONB), or the mapping will fail.
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "severity",
            sa.Text,
            nullable=False,
            server_default=sa.text("'info'"),
        ),
        sa.Column(
            "notifiable",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Pre-redacted network prefix only. Full client addresses are never
        # written here.
        sa.Column("ip_prefix", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "category IN ('record', 'sitting', 'template', 'user', 'auth', 'system', 'export')",
            name="audit_log_category_check",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="audit_log_severity_check",
        ),
    )

    # The index set is shaped by the two read paths. The listing endpoint always
    # sorts by created_at DESC, optionally filtered by category, so the plain and
    # composite descending indexes let Postgres satisfy both the filter and the
    # ordering from a single scan. The notification bell only ever reads rows
    # WHERE notifiable, so that index is partial and stays small no matter how
    # much routine non-notifiable traffic the log accumulates. DESC ordering and
    # the partial predicate both require raw DDL.
    op.execute("CREATE INDEX ix_audit_log_created_at ON audit_log (created_at DESC)")
    op.execute(
        "CREATE INDEX ix_audit_log_category_created_at ON audit_log (category, created_at DESC)"
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.execute(
        "CREATE INDEX ix_audit_log_notifiable ON audit_log (created_at DESC) WHERE notifiable"
    )

    # --- user_notification_state ---
    # Watermark design: one row per user holding the timestamp they last read up
    # to, rather than fanning a notification row out to every user per event. A
    # new event therefore costs exactly one audit_log insert regardless of how
    # many users exist, and unread counts and mark-all-read stay O(1) to
    # maintain. The trade-off: with no per-user, per-event row there is nothing
    # to mark individually, so notifications cannot be dismissed independently.
    # Reading moves the single watermark forward for everything older than it.
    op.create_table(
        "user_notification_state",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("to_timestamp(0)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Deliberately no seed data: an audit log populated by a migration would
    # assert that events happened which never did. Do not add fixtures here.


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_log_notifiable")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_category_created_at")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_created_at")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_table("user_notification_state")
    op.drop_table("audit_log")
