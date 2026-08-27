"""Add retrieval performance indexes.

The hybrid retriever's _LATEST_VERSION_JOIN computes
``MAX(version) GROUP BY record_id`` on every search query. Without a
covering index this is a sequential scan that grows with every transcript
edit. The composite index makes it an index-only scan.

The partial index on NULL embeddings speeds up both the vector arm's
``WHERE tc.embedding IS NOT NULL`` filter and the ``POST /rag/reindex``
discovery query.

Revision ID: 008
Revises: 007
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Covering index for the _LATEST_VERSION_JOIN subquery.
    # DESC on version so MAX() reads the first row per group.
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_transcript_record_version "
        "ON transcript (record_id, version DESC)"
    )

    # Partial index for chunks without embeddings — used by:
    # 1. VectorRetriever: WHERE tc.embedding IS NOT NULL (inverse)
    # 2. POST /rag/reindex: WHERE tc.embedding IS NULL
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_transcript_chunk_null_embedding "
        "ON transcript_chunk (transcript_id) "
        "WHERE embedding IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_transcript_chunk_null_embedding")
    op.execute("DROP INDEX IF EXISTS ix_transcript_record_version")
