"""SQLAlchemy access layer for the Dataset_Store.

Provides async functions to:
- Load active Entity_Records with their aliases, ordered deterministically
  by (source_rank, entity_kind, canonical, id) for Match_Index build parity.
- Load Block_List entries grouped by list_kind.
- Perform pg_trgm similarity queries for LLM retrieval (finding Entity_Records
  relevant to a transcript chunk).

All queries exclude records with active=false (Requirement 9.11).
The load order is deterministic (Requirement 3.12) and matches the
ix_entity_record_load_order index defined in the migration.

Requirements: 9.5, 9.11, 3.12, 12.6
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.entities import EntityKind, EntityRecord, EntityType


@dataclass(frozen=True)
class AliasRow:
    """A single alias row from entity_alias."""

    alias: str
    ordinal: int


@dataclass(frozen=True)
class BlockListEntry:
    """A single block_list row."""

    token: str
    list_kind: str
    reason: str | None = None


@dataclass(frozen=True)
class SimilarityResult:
    """An Entity_Record returned by a pg_trgm similarity query."""

    record_id: int
    canonical: str
    entity_kind: str
    entity_type: str
    similarity: float
    aliases: list[str] = field(default_factory=list)


def make_engine(database_url: str):
    """Create an async SQLAlchemy engine from a DATABASE_URL.

    Converts postgresql:// to postgresql+psycopg:// if needed, since
    psycopg (v3) is the async driver.
    """
    url = database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)

    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
    )


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def load_active_records(
    session: AsyncSession,
) -> list[EntityRecord]:
    """Load all active Entity_Records with their aliases.

    Records are ordered by (source_rank, entity_kind, canonical, id) which
    matches the ix_entity_record_load_order index. This ordering is load-bearing
    for the Match_Index write-order semantics (Requirement 3.12).

    Inactive records (active=false) are excluded (Requirement 9.11).

    Returns a list of EntityRecord Pydantic models with populated aliases.
    """
    # Load records in deterministic order
    records_result = await session.execute(
        text(
            "SELECT id, canonical, entity_kind, entity_type, region, "
            "constituency, party, role, active, source, source_rank "
            "FROM entity_record "
            "WHERE active = true "
            "ORDER BY source_rank, entity_kind, canonical, id"
        )
    )
    rows = records_result.fetchall()

    if not rows:
        return []

    # Collect all record IDs for batch alias fetch
    record_ids = [row[0] for row in rows]

    # Load all aliases for active records in one query, ordered by
    # entity_record_id and ordinal for deterministic assignment
    aliases_result = await session.execute(
        text(
            "SELECT entity_record_id, alias, ordinal "
            "FROM entity_alias "
            "WHERE entity_record_id = ANY(:ids) "
            "ORDER BY entity_record_id, ordinal"
        ),
        {"ids": record_ids},
    )
    alias_rows = aliases_result.fetchall()

    # Group aliases by record_id
    aliases_by_record: dict[int, list[str]] = {}
    for alias_row in alias_rows:
        record_id = alias_row[0]
        alias_text = alias_row[1]
        if record_id not in aliases_by_record:
            aliases_by_record[record_id] = []
        aliases_by_record[record_id].append(alias_text)

    # Build EntityRecord models
    records: list[EntityRecord] = []
    for row in rows:
        record_id = row[0]
        record = EntityRecord(
            canonical=row[1],
            entity_kind=EntityKind(row[2]),
            entity_type=EntityType(row[3]),
            region=row[4],
            constituency=row[5],
            party=row[6],
            role=row[7],
            active=row[8],
            source=row[9],
            source_rank=row[10],
            aliases=aliases_by_record.get(record_id, []),
        )
        records.append(record)

    return records


async def load_block_list(
    session: AsyncSession,
    list_kind: str,
) -> list[BlockListEntry]:
    """Load block_list entries for a specific list_kind.

    Valid list_kind values: 'block', 'stopword', 'word_stopword', 'title'.

    Returns entries ordered by token for deterministic results.
    """
    result = await session.execute(
        text(
            "SELECT token, list_kind, reason "
            "FROM block_list "
            "WHERE list_kind = :list_kind "
            "ORDER BY token"
        ),
        {"list_kind": list_kind},
    )
    rows = result.fetchall()
    return [BlockListEntry(token=row[0], list_kind=row[1], reason=row[2]) for row in rows]


async def load_all_block_lists(
    session: AsyncSession,
) -> dict[str, list[BlockListEntry]]:
    """Load all block_list entries grouped by list_kind.

    Returns a dict mapping list_kind to its entries. Known kinds:
    'block', 'stopword', 'word_stopword', 'title'.
    """
    result = await session.execute(
        text(
            "SELECT token, list_kind, reason "
            "FROM block_list "
            "ORDER BY list_kind, token"
        )
    )
    rows = result.fetchall()

    grouped: dict[str, list[BlockListEntry]] = {}
    for row in rows:
        entry = BlockListEntry(token=row[0], list_kind=row[1], reason=row[2])
        if entry.list_kind not in grouped:
            grouped[entry.list_kind] = []
        grouped[entry.list_kind].append(entry)

    return grouped


async def similarity_search(
    session: AsyncSession,
    query_text: str,
    *,
    limit: int = 50,
    threshold: float = 0.3,
) -> list[SimilarityResult]:
    """Find Entity_Records similar to query_text using pg_trgm.

    Used by the LLM retrieval path (Requirement 12.6) to find records
    relevant to a transcript chunk without injecting the full dataset
    into the prompt.

    Searches both entity_record.canonical and entity_alias.alias using
    the GIN trigram indexes. Results are deduplicated by record and
    ordered by best similarity score descending.

    Only active records are returned (Requirement 9.11).

    Args:
        session: An async SQLAlchemy session.
        query_text: The text to search for similar entities.
        limit: Maximum number of results to return (default 50).
        threshold: Minimum similarity score threshold (default 0.3).

    Returns:
        A list of SimilarityResult ordered by similarity descending.
    """
    # Use pg_trgm similarity() to find matching records.
    # Search both canonical names and aliases, take the best score per record.
    result = await session.execute(
        text(
            """
            WITH canonical_matches AS (
                SELECT
                    er.id AS record_id,
                    er.canonical,
                    er.entity_kind,
                    er.entity_type,
                    similarity(er.canonical, :query) AS sim_score
                FROM entity_record er
                WHERE er.active = true
                  AND similarity(er.canonical, :query) >= :threshold
            ),
            alias_matches AS (
                SELECT
                    er.id AS record_id,
                    er.canonical,
                    er.entity_kind,
                    er.entity_type,
                    similarity(ea.alias, :query) AS sim_score
                FROM entity_alias ea
                JOIN entity_record er ON er.id = ea.entity_record_id
                WHERE er.active = true
                  AND similarity(ea.alias, :query) >= :threshold
            ),
            combined AS (
                SELECT * FROM canonical_matches
                UNION ALL
                SELECT * FROM alias_matches
            ),
            best_per_record AS (
                SELECT
                    record_id,
                    canonical,
                    entity_kind,
                    entity_type,
                    MAX(sim_score) AS best_sim
                FROM combined
                GROUP BY record_id, canonical, entity_kind, entity_type
            )
            SELECT record_id, canonical, entity_kind, entity_type, best_sim
            FROM best_per_record
            ORDER BY best_sim DESC, canonical
            LIMIT :limit
            """
        ),
        {"query": query_text, "threshold": threshold, "limit": limit},
    )
    rows = result.fetchall()

    if not rows:
        return []

    # Fetch aliases for the matched records
    matched_ids = [row[0] for row in rows]
    aliases_result = await session.execute(
        text(
            "SELECT entity_record_id, alias "
            "FROM entity_alias "
            "WHERE entity_record_id = ANY(:ids) "
            "ORDER BY entity_record_id, ordinal"
        ),
        {"ids": matched_ids},
    )
    alias_rows = aliases_result.fetchall()

    aliases_by_record: dict[int, list[str]] = {}
    for alias_row in alias_rows:
        record_id = alias_row[0]
        alias_text = alias_row[1]
        if record_id not in aliases_by_record:
            aliases_by_record[record_id] = []
        aliases_by_record[record_id].append(alias_text)

    # Build results
    results: list[SimilarityResult] = []
    for row in rows:
        results.append(
            SimilarityResult(
                record_id=row[0],
                canonical=row[1],
                entity_kind=row[2],
                entity_type=row[3],
                similarity=float(row[4]),
                aliases=aliases_by_record.get(row[0], []),
            )
        )

    return results


async def similarity_search_multi(
    session: AsyncSession,
    query_texts: list[str],
    *,
    limit: int = 50,
    threshold: float = 0.3,
) -> list[SimilarityResult]:
    """Find Entity_Records similar to any of the given query texts.

    Useful for LLM retrieval where a chunk may contain multiple entity
    references. Deduplicates results across all queries and returns
    the top matches by best similarity score.

    Only active records are returned (Requirement 9.11).

    Args:
        session: An async SQLAlchemy session.
        query_texts: A list of text fragments to search for.
        limit: Maximum total results to return (default 50).
        threshold: Minimum similarity score threshold (default 0.3).

    Returns:
        A list of SimilarityResult ordered by similarity descending,
        deduplicated by record_id across all queries.
    """
    if not query_texts:
        return []

    # Collect results from all queries, keeping best score per record
    seen: dict[int, SimilarityResult] = {}

    for query_text in query_texts:
        # Skip empty or very short queries that won't produce useful trigrams
        if not query_text or len(query_text.strip()) < 3:
            continue

        results = await similarity_search(
            session,
            query_text,
            limit=limit,
            threshold=threshold,
        )
        for r in results:
            if r.record_id not in seen or r.similarity > seen[r.record_id].similarity:
                seen[r.record_id] = r

    # Sort by similarity descending, then canonical for determinism
    all_results = sorted(
        seen.values(),
        key=lambda r: (-r.similarity, r.canonical),
    )

    return all_results[:limit]
