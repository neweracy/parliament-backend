"""Hansard schema: sittings, records, transcripts, chunks, settings.

Revision ID: 003
Revises: 002
Create Date: 2026-07-10

Creates the pgvector extension and five new tables:
- sitting: parliamentary session metadata
- hansard_record: individual transcription units within a sitting
- transcript: versioned transcript storage with entities and timings
- transcript_chunk: RAG retrieval units with embeddings and tsvector
- app_settings: singleton application configuration

Includes HNSW index on embedding, GIN indexes on tsvector and entity_names,
and a trigger to auto-populate the tsvector column on insert/update.

Requirements: 6.1, 6.2, 7.3, 7.5, 7.6, 14.1
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension for vector similarity search.
    conn = op.get_bind()
    already_present = conn.exec_driver_sql(
        "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
    ).scalar()

    if not already_present:
        try:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "pgvector is required for semantic search but could not be "
                "created automatically, most likely because the migration role "
                "lacks privileges. Have an administrator run "
                "'CREATE EXTENSION vector;' in this database, then re-run the "
                f"migration. Underlying error: {exc}"
            ) from exc

    # --- sitting ---
    op.create_table(
        "sitting",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "session_type",
            sa.Text,
            nullable=False,
        ),
        sa.Column("committee", sa.Text, nullable=True),
        sa.Column("presiding_officer", sa.Text, nullable=False),
        sa.Column("parliament", sa.Text, nullable=True),
        sa.Column("date_from", sa.Date, nullable=False),
        sa.Column("date_to", sa.Date, nullable=False),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Active'"),
        ),
        sa.Column(
            "priority",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Medium'"),
        ),
        sa.Column("participants", sa.Integer, server_default=sa.text("0")),
        sa.Column("topic", sa.Text, nullable=True),
        sa.Column("order_paper_ref", sa.Text, nullable=True),
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
        sa.CheckConstraint(
            "session_type IN ('Plenary', 'Committee', 'Special Sitting')",
            name="ck_sitting_session_type",
        ),
        sa.CheckConstraint(
            "status IN ('Active', 'Completed', 'Archived')",
            name="ck_sitting_status",
        ),
        sa.CheckConstraint(
            "priority IN ('High', 'Medium', 'Low')",
            name="ck_sitting_priority",
        ),
    )

    op.create_index(
        "ix_sitting_status",
        "sitting",
        ["status"],
        postgresql_where=sa.text("status != 'Archived'"),
    )
    op.create_index(
        "ix_sitting_date_range",
        "sitting",
        ["date_from", "date_to"],
    )

    # --- hansard_record ---
    op.create_table(
        "hansard_record",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "sitting_id",
            sa.BigInteger,
            sa.ForeignKey("sitting.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("duration", sa.Text, nullable=True),
        sa.Column("duration_hours", sa.Float, nullable=True),
        sa.Column("language", sa.Text, server_default=sa.text("'English'")),
        sa.Column("audio_file_name", sa.Text, nullable=True),
        sa.Column("audio_path", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Draft'"),
        ),
        sa.Column("progress", sa.Integer, server_default=sa.text("0")),
        sa.Column(
            "visibility",
            sa.Text,
            nullable=False,
            server_default=sa.text("'Public'"),
        ),
        sa.Column("assignee_name", sa.Text, nullable=True),
        sa.Column("assignee_avatar", sa.Text, nullable=True),
        sa.Column("assignee_role", sa.Text, nullable=True),
        sa.Column("start_time", sa.Text, nullable=True),
        sa.Column("end_time", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
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
        sa.CheckConstraint(
            "status IN ('Transcribing', 'Draft', 'Editing', "
            "'Under Review', 'Certified', 'Published')",
            name="ck_record_status",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_record_progress",
        ),
        sa.CheckConstraint(
            "visibility IN ('Public', 'Internal', 'Restricted')",
            name="ck_record_visibility",
        ),
    )

    op.create_index(
        "ix_record_sitting",
        "hansard_record",
        ["sitting_id"],
    )
    op.create_index(
        "ix_record_status",
        "hansard_record",
        ["status"],
    )
    op.create_index(
        "ix_record_assignee",
        "hansard_record",
        ["assignee_name"],
        postgresql_where=sa.text("assignee_name IS NOT NULL"),
    )

    # --- transcript ---
    op.create_table(
        "transcript",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "record_id",
            sa.BigInteger,
            sa.ForeignKey("hansard_record.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("correlation_id", sa.Text, nullable=True),
        sa.Column("source_name", sa.Text, nullable=True),
        sa.Column("provider", sa.Text, nullable=True),
        sa.Column("duration_s", sa.Float, nullable=True),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("corrected_text", sa.Text, nullable=False),
        sa.Column(
            "entities",
            sa.JSON,
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "word_timings",
            sa.JSON,
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metadata",
            sa.JSON,
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("record_id", "version", name="uq_transcript_record_version"),
    )

    op.create_index(
        "ix_transcript_record",
        "transcript",
        ["record_id"],
    )
    op.create_index(
        "ix_transcript_created",
        "transcript",
        ["created_at"],
    )

    # --- transcript_chunk ---
    op.create_table(
        "transcript_chunk",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "transcript_id",
            sa.BigInteger,
            sa.ForeignKey("transcript.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("start_s", sa.Float, nullable=True),
        sa.Column("end_s", sa.Float, nullable=True),
        sa.Column("speaker", sa.Text, nullable=True),
        sa.Column("entity_names", sa.ARRAY(sa.Text), server_default=sa.text("'{}'::text[]")),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("transcript_id", "ordinal", name="uq_chunk_transcript_ordinal"),
    )

    # The embedding and tsv columns require raw DDL because SQLAlchemy
    # doesn't have native pgvector / tsvector column types.
    op.execute(
        "ALTER TABLE transcript_chunk "
        "ADD COLUMN embedding vector(1024)"
    )
    op.execute(
        "ALTER TABLE transcript_chunk "
        "ADD COLUMN tsv tsvector"
    )

    op.create_index(
        "ix_chunk_transcript",
        "transcript_chunk",
        ["transcript_id"],
    )
    op.execute(
        "CREATE INDEX ix_chunk_embedding "
        "ON transcript_chunk USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_chunk_tsv "
        "ON transcript_chunk USING gin (tsv)"
    )
    op.execute(
        "CREATE INDEX ix_chunk_entity_names "
        "ON transcript_chunk USING gin (entity_names)"
    )
    op.create_index(
        "ix_chunk_speaker",
        "transcript_chunk",
        ["speaker"],
        postgresql_where=sa.text("speaker IS NOT NULL"),
    )

    # --- app_settings ---
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer, primary_key=True, server_default=sa.text("1")),
        sa.Column(
            "transcription_engine",
            sa.Text,
            server_default=sa.text("'deepgram'"),
        ),
        sa.Column(
            "default_language",
            sa.Text,
            server_default=sa.text("'en'"),
        ),
        sa.Column(
            "custom_dictionary",
            sa.JSON,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "auto_save_interval_s",
            sa.Integer,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="ck_app_settings_singleton"),
    )

    # Insert default singleton row
    op.execute("INSERT INTO app_settings DEFAULT VALUES")

    # --- tsvector trigger ---
    op.execute("""
        CREATE OR REPLACE FUNCTION chunk_tsv_trigger() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('english', NEW.text);
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER trg_chunk_tsv
            BEFORE INSERT OR UPDATE OF text ON transcript_chunk
            FOR EACH ROW EXECUTE FUNCTION chunk_tsv_trigger()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_tsv ON transcript_chunk")
    op.execute("DROP FUNCTION IF EXISTS chunk_tsv_trigger()")
    op.drop_table("app_settings")
    op.drop_table("transcript_chunk")
    op.drop_table("transcript")
    op.drop_table("hansard_record")
    op.drop_table("sitting")
    op.execute("DROP EXTENSION IF EXISTS vector")
