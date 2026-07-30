"""Initial Dataset_Store schema.

Revision ID: 001
Revises: None
Create Date: 2026-07-09

Creates the pg_trgm extension and the four core tables:
- entity_record: canonical entities with source/rank/active
- entity_alias: per-record aliases with ordinal
- block_list: suppression tokens (block/stopword/word_stopword/title)
- correction_history: audit trail of applied corrections

All indexes from the design are included, notably the GIN trigram indexes
for fuzzy candidate retrieval and the deterministic load-order index.

Requirements: 9.2, 13.9
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pg_trgm extension for GIN trigram indexes
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- entity_record ---
    op.create_table(
        "entity_record",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("canonical", sa.Text, nullable=False),
        sa.Column("entity_kind", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("region", sa.Text, nullable=True),
        sa.Column("constituency", sa.Text, nullable=True),
        sa.Column("party", sa.Text, nullable=True),
        sa.Column("role", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_rank", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Natural key constraint
        sa.UniqueConstraint("canonical", "entity_kind", "source", name="uq_entity_record_natural_key"),
    )

    # Indexes on entity_record
    op.create_index(
        "ix_entity_record_kind_active",
        "entity_record",
        ["entity_kind", "active"],
    )
    op.create_index(
        "ix_entity_record_load_order",
        "entity_record",
        ["source_rank", "entity_kind", "canonical", "id"],
    )
    # GIN trigram index on entity_record.canonical
    op.execute(
        "CREATE INDEX ix_entity_record_canonical_trgm "
        "ON entity_record USING gin (canonical gin_trgm_ops)"
    )

    # --- entity_alias ---
    op.create_table(
        "entity_alias",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "entity_record_id",
            sa.Integer,
            sa.ForeignKey("entity_record.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text, nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False, server_default=sa.text("0")),
        # Unique constraint on (entity_record_id, alias)
        sa.UniqueConstraint("entity_record_id", "alias", name="uq_entity_alias_record_alias"),
    )

    # GIN trigram index on entity_alias.alias
    op.execute(
        "CREATE INDEX ix_entity_alias_alias_trgm "
        "ON entity_alias USING gin (alias gin_trgm_ops)"
    )

    # --- block_list ---
    op.create_table(
        "block_list",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("list_kind", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        # Unique constraint on (token, list_kind)
        sa.UniqueConstraint("token", "list_kind", name="uq_block_list_token_kind"),
    )

    # Index on block_list.list_kind
    op.create_index(
        "ix_block_list_kind",
        "block_list",
        ["list_kind"],
    )

    # --- correction_history ---
    op.create_table(
        "correction_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("correlation_id", sa.Text, nullable=True),
        sa.Column("text_hash", sa.Text, nullable=True),
        sa.Column("original", sa.Text, nullable=False),
        sa.Column("corrected", sa.Text, nullable=False),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("entity_kind", sa.Text, nullable=True),
        sa.Column("entity_type", sa.Text, nullable=True),
        sa.Column("model_version", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Indexes on correction_history
    op.create_index(
        "ix_correction_history_correlation",
        "correction_history",
        ["correlation_id"],
    )
    op.create_index(
        "ix_correction_history_created",
        "correction_history",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("correction_history")
    op.drop_table("block_list")
    op.drop_table("entity_alias")
    op.drop_table("entity_record")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
