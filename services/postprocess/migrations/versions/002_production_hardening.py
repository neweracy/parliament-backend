"""Production hardening for the Dataset_Store.

Revision ID: 002
Revises: 001
Create Date: 2026-07-30

Three groups of changes, each closing a gap that only bites in production:

1. **CHECK constraints on enum-like text columns.** ``entity_kind``,
   ``entity_type``, ``list_kind``, and ``strategy`` are validated by Pydantic on
   the way in, but the database accepted any string. Anything writing outside the
   service (the seed script, a migration, a manual fix, a future consumer) could
   silently insert a value the correction engine will never match on. The
   constraints make the database reject it instead.

2. **Retention index on ``correction_history.created_at``.** The retention
   sweeper deletes by ``created_at``; 001 indexed that column but the sweeper's
   ``ORDER BY id`` sub-select benefits from a composite. Also documents the
   retention contract in the schema itself.

3. **``updated_at`` trigger on ``entity_record``.** 001 set ``server_default
   now()`` and relied on every writer remembering ``updated_at = now()`` in its
   UPSERT. Any writer that forgets leaves a stale timestamp, which makes the
   column untrustworthy for change tracking. A trigger makes it unconditional.

All statements are written to be safely re-runnable.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mirrors app.models.entities.EntityKind
ENTITY_KINDS = ("location", "person", "party")

# Mirrors app.models.entities.EntityType
ENTITY_TYPES = (
    "region",
    "city",
    "constituency",
    "district",
    "president",
    "minister",
    "mp",
    "party",
    "supplementary",
)

# Mirrors scripts/migrate_js_datasets.py BLOCK_LIST_KINDS
LIST_KINDS = ("block", "stopword", "word_stopword", "title")

# Mirrors app.models.entities.MatchStrategy, plus "identity" which the metrics
# layer documents as a possible strategy dimension.
STRATEGIES = (
    "exact",
    "fused",
    "joined",
    "initials",
    "title_person",
    "phonetic",
    "fuzzy",
    "substring",
    "identity",
)


def _in_list(values: tuple[str, ...]) -> str:
    """Render a SQL IN list from a tuple of literals."""
    inner = ", ".join(f"'{v}'" for v in values)
    return f"({inner})"


def upgrade() -> None:
    # ---------------------------------------------------------------------
    # 1. CHECK constraints on enum-like columns
    # ---------------------------------------------------------------------
    # Added NOT VALID so the migration does not scan existing rows or block
    # writes on a large table. Each is validated immediately afterwards, which
    # takes only a SHARE UPDATE EXCLUSIVE lock rather than ACCESS EXCLUSIVE.
    op.execute(
        "ALTER TABLE entity_record "
        "ADD CONSTRAINT ck_entity_record_kind "
        f"CHECK (entity_kind IN {_in_list(ENTITY_KINDS)}) NOT VALID"
    )
    op.execute("ALTER TABLE entity_record VALIDATE CONSTRAINT ck_entity_record_kind")

    op.execute(
        "ALTER TABLE entity_record "
        "ADD CONSTRAINT ck_entity_record_type "
        f"CHECK (entity_type IN {_in_list(ENTITY_TYPES)}) NOT VALID"
    )
    op.execute("ALTER TABLE entity_record VALIDATE CONSTRAINT ck_entity_record_type")

    op.execute(
        "ALTER TABLE entity_record "
        "ADD CONSTRAINT ck_entity_record_source_rank_nonneg "
        "CHECK (source_rank >= 0) NOT VALID"
    )
    op.execute(
        "ALTER TABLE entity_record VALIDATE CONSTRAINT ck_entity_record_source_rank_nonneg"
    )

    op.execute(
        "ALTER TABLE block_list "
        "ADD CONSTRAINT ck_block_list_kind "
        f"CHECK (list_kind IN {_in_list(LIST_KINDS)}) NOT VALID"
    )
    op.execute("ALTER TABLE block_list VALIDATE CONSTRAINT ck_block_list_kind")

    op.execute(
        "ALTER TABLE correction_history "
        "ADD CONSTRAINT ck_correction_history_strategy "
        f"CHECK (strategy IN {_in_list(STRATEGIES)}) NOT VALID"
    )
    op.execute(
        "ALTER TABLE correction_history VALIDATE CONSTRAINT ck_correction_history_strategy"
    )

    # Confidence is a probability; the model enforces [0,1] but the table did not.
    op.execute(
        "ALTER TABLE correction_history "
        "ADD CONSTRAINT ck_correction_history_confidence_range "
        "CHECK (confidence >= 0.0 AND confidence <= 1.0) NOT VALID"
    )
    op.execute(
        "ALTER TABLE correction_history "
        "VALIDATE CONSTRAINT ck_correction_history_confidence_range"
    )

    # entity_alias.ordinal participates in deterministic index build order.
    op.execute(
        "ALTER TABLE entity_alias "
        "ADD CONSTRAINT ck_entity_alias_ordinal_nonneg "
        "CHECK (ordinal >= 0) NOT VALID"
    )
    op.execute(
        "ALTER TABLE entity_alias VALIDATE CONSTRAINT ck_entity_alias_ordinal_nonneg"
    )

    # ---------------------------------------------------------------------
    # 2. Retention support index
    # ---------------------------------------------------------------------
    # The sweeper runs: WHERE created_at < cutoff ORDER BY id LIMIT n.
    # A (created_at, id) composite serves both the filter and the ordering.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_correction_history_retention "
        "ON correction_history (created_at, id)"
    )

    # ---------------------------------------------------------------------
    # 3. updated_at maintained by trigger, not by convention
    # ---------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_entity_record_updated_at ON entity_record")
    op.execute(
        "CREATE TRIGGER trg_entity_record_updated_at "
        "BEFORE UPDATE ON entity_record "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_entity_record_updated_at ON entity_record")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    op.execute("DROP INDEX IF EXISTS ix_correction_history_retention")

    op.execute(
        "ALTER TABLE entity_alias DROP CONSTRAINT IF EXISTS ck_entity_alias_ordinal_nonneg"
    )
    op.execute(
        "ALTER TABLE correction_history "
        "DROP CONSTRAINT IF EXISTS ck_correction_history_confidence_range"
    )
    op.execute(
        "ALTER TABLE correction_history "
        "DROP CONSTRAINT IF EXISTS ck_correction_history_strategy"
    )
    op.execute("ALTER TABLE block_list DROP CONSTRAINT IF EXISTS ck_block_list_kind")
    op.execute(
        "ALTER TABLE entity_record "
        "DROP CONSTRAINT IF EXISTS ck_entity_record_source_rank_nonneg"
    )
    op.execute("ALTER TABLE entity_record DROP CONSTRAINT IF EXISTS ck_entity_record_type")
    op.execute("ALTER TABLE entity_record DROP CONSTRAINT IF EXISTS ck_entity_record_kind")
