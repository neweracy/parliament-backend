"""Dataset_Store schema: entity_record, entity_alias, block_list.

Creates the three tables the Dataset_Store reads and the seed script writes,
which no earlier migration ever provisioned. ``app/datasets/store.py`` queries
``entity_record``, ``entity_alias`` and ``block_list`` (and their pg_trgm GIN
indexes) to build the Match_Index and to serve LLM retrieval, and
``scripts/migrate_js_datasets.py`` upserts the Ghana entity datasets into them.
Without these tables a fresh database fails the Dataset_Cache build on startup
with ``relation "entity_record" does not exist``.

The change is purely additive and backward compatible:
- it introduces three brand-new tables plus their indexes and touches no
  existing table or column;
- there is no backfill — the tables are seeded out-of-band by
  ``scripts/migrate_js_datasets.py``, never by this migration;
- the only write path is the Postprocess_Service; the Gateway never writes it.

The unique constraints are the exact ``ON CONFLICT`` targets the seed script
relies on for idempotent upserts, and the check constraints mirror the
``EntityKind`` / ``EntityType`` enums in ``app/models/entities.py`` and the four
``block_list`` kinds the loader seeds — so a hand-written row cannot drift from
the model's closed sets.

No existing migration is modified — this file chains onto the current head (012).
Confirm the current revision (``alembic current``) before applying in any
environment.

Revision ID: 013
Revises: 012
Create Date: 2026-09-24

Requirements: 3.12, 9.5, 9.11, 12.6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The trigram GIN indexes below (and store.py's similarity() queries for LLM
    # retrieval, Req 12.6) need the pg_trgm extension. Production first-init runs
    # scripts/init-pg-trgm.sql, but a database brought up by migrations alone
    # never runs that, so create the extension here. IF NOT EXISTS keeps it
    # idempotent and harmless when init-pg-trgm.sql already created it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------
    # entity_record — one canonical entity per (canonical, entity_kind, source).
    # ------------------------------------------------------------------
    op.create_table(
        "entity_record",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("canonical", sa.Text, nullable=False),
        sa.Column("entity_kind", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("region", sa.Text, nullable=True),
        sa.Column("constituency", sa.Text, nullable=True),
        sa.Column("party", sa.Text, nullable=True),
        sa.Column("role", sa.Text, nullable=True),
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column(
            "source_rank",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Exact ON CONFLICT target of the seed's UPSERT_RECORD_SQL — must match
        # (canonical, entity_kind, source) for idempotent re-runs.
        sa.UniqueConstraint(
            "canonical",
            "entity_kind",
            "source",
            name="uq_entity_record_canonical_kind_source",
        ),
        # Mirrors EntityKind in app/models/entities.py.
        sa.CheckConstraint(
            "entity_kind IN ('location', 'person', 'party')",
            name="ck_entity_record_kind",
        ),
        # Mirrors EntityType in app/models/entities.py.
        sa.CheckConstraint(
            "entity_type IN ('region', 'city', 'constituency', 'district', "
            "'president', 'minister', 'mp', 'party', 'supplementary')",
            name="ck_entity_record_type",
        ),
    )

    # Load-bearing, not merely a perf index: store.py.load_active_records issues
    # ORDER BY (source_rank, entity_kind, canonical, id), and the Match_Index
    # build depends on that exact deterministic order for write-order parity
    # (Req 3.12). Keep this index's column order identical to that ORDER BY.
    op.create_index(
        "ix_entity_record_load_order",
        "entity_record",
        ["source_rank", "entity_kind", "canonical", "id"],
    )

    # Trigram index over canonical for similarity() retrieval. Raw DDL because
    # gin_trgm_ops is an operator class Alembic's op.create_index cannot express.
    op.execute(
        "CREATE INDEX ix_entity_record_canonical_trgm "
        "ON entity_record USING gin (canonical gin_trgm_ops)"
    )

    # ------------------------------------------------------------------
    # entity_alias — alternate surface forms for an entity_record.
    # ------------------------------------------------------------------
    op.create_table(
        "entity_alias",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "entity_record_id",
            sa.BigInteger,
            sa.ForeignKey("entity_record.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text, nullable=False),
        sa.Column(
            "ordinal",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Exact ON CONFLICT target of the seed's INSERT_ALIAS_SQL.
        sa.UniqueConstraint(
            "entity_record_id",
            "alias",
            name="uq_entity_alias_record_alias",
        ),
    )

    # store.py loads aliases ORDER BY (entity_record_id, ordinal); this index
    # serves that deterministic per-record ordering.
    op.create_index(
        "ix_entity_alias_record",
        "entity_alias",
        ["entity_record_id", "ordinal"],
    )

    # Trigram index over alias for similarity() retrieval (raw DDL for
    # gin_trgm_ops, as above).
    op.execute(
        "CREATE INDEX ix_entity_alias_alias_trgm "
        "ON entity_alias USING gin (alias gin_trgm_ops)"
    )

    # ------------------------------------------------------------------
    # block_list — suppression tokens keyed by (token, list_kind).
    # ------------------------------------------------------------------
    op.create_table(
        "block_list",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("list_kind", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Exact ON CONFLICT target of the seed's INSERT_BLOCK_LIST_SQL.
        sa.UniqueConstraint(
            "token",
            "list_kind",
            name="uq_block_list_token_kind",
        ),
        # Mirrors BLOCK_LIST_KINDS in scripts/migrate_js_datasets.py.
        sa.CheckConstraint(
            "list_kind IN ('block', 'stopword', 'word_stopword', 'title')",
            name="ck_block_list_kind",
        ),
    )

    # store.py loads a block list ORDER BY token, filtered by list_kind; this
    # index serves both the filter and the ordering.
    op.create_index(
        "ix_block_list_kind",
        "block_list",
        ["list_kind", "token"],
    )


def downgrade() -> None:
    # Drop in reverse dependency order: block_list first (no dependents), then
    # entity_alias (FK -> entity_record), then entity_record last.
    op.drop_index("ix_block_list_kind", table_name="block_list")
    op.drop_table("block_list")

    # Raw DROP INDEX IF EXISTS for the trigram index created via raw DDL.
    op.execute("DROP INDEX IF EXISTS ix_entity_alias_alias_trgm")
    op.drop_index("ix_entity_alias_record", table_name="entity_alias")
    op.drop_table("entity_alias")

    op.execute("DROP INDEX IF EXISTS ix_entity_record_canonical_trgm")
    op.drop_index("ix_entity_record_load_order", table_name="entity_record")
    op.drop_table("entity_record")

    # The pg_trgm extension is intentionally NOT dropped: other features may
    # rely on it, and it is safe/idempotent to leave installed.
