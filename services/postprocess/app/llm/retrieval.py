"""Per-chunk candidate extraction for the LLM_Refiner.

Extracts candidate entity names from each chunk, retrieves similar records
from the Dataset_Store (pg_trgm similarity search), deduplicates, and caps
at LLM_MAX_PROMPT_RECORDS.

When no database session is available, falls back to a naive approach using
the Dataset_Cache's canonical_map.

Requirements: 12.6, 12.7, 12.8, 12.9, 12.10, 11.10, 11.11
"""

from __future__ import annotations

import re

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets.cache import DatasetSnapshot
from app.datasets.store import SimilarityResult, similarity_search_multi
from app.models.entities import EntityKind, EntityRecord, EntityType

logger = structlog.get_logger("llm.retrieval")


def _extract_candidate_tokens(
    chunk_text: str,
    snapshot: DatasetSnapshot,
    min_length: int = 4,
) -> list[str]:
    """Extract candidate entity names from chunk text.

    Filters out:
      - Tokens shorter than min_length characters
      - Block_List tokens
      - Stopwords
      - Common English words that are unlikely to be entities

    Returns deduplicated candidate tokens preserving first-seen order.
    """
    # Split on whitespace, then clean punctuation from edges
    raw_tokens = chunk_text.split()

    # Also try 2-grams and 3-grams for multi-word entities
    candidates: list[str] = []
    seen: set[str] = set()

    # Common English words that should never be treated as entity candidates
    # (supplement the block_list/stopwords from the snapshot)
    _common_skip = frozenset({
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "was", "are", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "shall",
        "can", "not", "no", "yes", "this", "that", "these", "those",
        "it", "its", "they", "them", "their", "we", "our", "us",
        "he", "him", "his", "she", "her", "you", "your", "i", "my",
        "me", "who", "what", "where", "when", "why", "how", "which",
        "there", "here", "then", "than", "very", "just", "also",
        "about", "after", "before", "between", "through", "during",
        "general", "nation", "national", "page", "several",
    })

    block_set = snapshot.block_list | snapshot.stopwords | _common_skip

    def _clean_token(t: str) -> str:
        """Strip leading/trailing punctuation."""
        return re.sub(r"^[^\w]+|[^\w]+$", "", t)

    def _add_candidate(text: str) -> None:
        """Add a candidate if it passes filters."""
        lower = text.lower()
        if lower in seen:
            return
        if len(text) < min_length:
            return
        if lower in block_set:
            return
        # Skip if it's all digits
        if text.isdigit():
            return
        seen.add(lower)
        candidates.append(text)

    # Single tokens
    for raw in raw_tokens:
        token = _clean_token(raw)
        if token:
            _add_candidate(token)

    # 2-grams (for multi-word entity names)
    for i in range(len(raw_tokens) - 1):
        t1 = _clean_token(raw_tokens[i])
        t2 = _clean_token(raw_tokens[i + 1])
        if t1 and t2:
            bigram = f"{t1} {t2}"
            _add_candidate(bigram)

    # 3-grams
    for i in range(len(raw_tokens) - 2):
        t1 = _clean_token(raw_tokens[i])
        t2 = _clean_token(raw_tokens[i + 1])
        t3 = _clean_token(raw_tokens[i + 2])
        if t1 and t2 and t3:
            trigram = f"{t1} {t2} {t3}"
            _add_candidate(trigram)

    return candidates


def _similarity_results_to_records(
    results: list[SimilarityResult],
) -> list[EntityRecord]:
    """Convert SimilarityResult objects to EntityRecord models."""
    records: list[EntityRecord] = []
    for r in results:
        records.append(
            EntityRecord(
                canonical=r.canonical,
                entity_kind=EntityKind(r.entity_kind),
                entity_type=EntityType(r.entity_type),
                aliases=r.aliases,
            )
        )
    return records


def _fallback_from_canonical_map(
    candidates: list[str],
    snapshot: DatasetSnapshot,
    max_records: int,
) -> list[EntityRecord]:
    """Naive fallback: look up candidates in the canonical_map directly.

    Used when no database session is available for pg_trgm queries.
    Matches are exact (lowercase) lookups against the canonical_map.
    """
    seen_canonicals: set[str] = set()
    records: list[EntityRecord] = []

    canonical_map = snapshot.index.canonical_map

    for candidate in candidates:
        lower = candidate.lower()
        if lower in canonical_map:
            canonical = canonical_map[lower]
            if canonical not in seen_canonicals:
                seen_canonicals.add(canonical)
                # Build a minimal EntityRecord from index data
                kind_str = snapshot.index.entity_kind_map.get(canonical, "location")
                type_str = snapshot.index.entity_type_map.get(canonical, "supplementary")
                records.append(
                    EntityRecord(
                        canonical=canonical,
                        entity_kind=EntityKind(kind_str),
                        entity_type=EntityType(type_str),
                        aliases=[],
                    )
                )
                if len(records) >= max_records:
                    break

    return records


async def retrieve_candidates(
    chunk_text: str,
    snapshot: DatasetSnapshot,
    session: AsyncSession | None,
    max_records: int = 50,
) -> list[EntityRecord]:
    """Retrieve Entity_Records relevant to a chunk for LLM prompt grounding.

    Extraction flow:
      1. Extract candidate tokens from the chunk text (length >= 4,
         not in Block_List/stopwords)
      2. Search for similar records using pg_trgm (Dataset_Store fallback)
      3. Deduplicate results and cap at max_records

    If session is None (no DB access), falls back to naive canonical_map
    lookup from the Dataset_Cache.

    Args:
        chunk_text: The text of the chunk to retrieve candidates for.
        snapshot: The current DatasetSnapshot with index data.
        session: An async SQLAlchemy session for pg_trgm queries, or None.
        max_records: Maximum number of records to return (default 50).

    Returns:
        A list of EntityRecord objects, deduplicated and capped.
    """
    if not chunk_text or not chunk_text.strip():
        return []

    # Step 1: Extract candidate tokens
    candidates = _extract_candidate_tokens(chunk_text, snapshot)

    if not candidates:
        return []

    # Step 2: Retrieve similar records
    if session is not None:
        try:
            results = await similarity_search_multi(
                session,
                candidates,
                limit=max_records,
                threshold=0.3,
            )
            records = _similarity_results_to_records(results)
        except Exception:  # noqa: BLE001
            # pg_trgm query failed — fall back to canonical_map
            logger.warning(
                "llm.retrieval.pg_trgm_failed",
                candidate_count=len(candidates),
            )
            records = _fallback_from_canonical_map(candidates, snapshot, max_records)
    else:
        # No session available — use canonical_map fallback
        records = _fallback_from_canonical_map(candidates, snapshot, max_records)

    # Step 3: Deduplicate by canonical name and cap
    seen: set[str] = set()
    deduped: list[EntityRecord] = []
    for record in records:
        if record.canonical not in seen:
            seen.add(record.canonical)
            deduped.append(record)
            if len(deduped) >= max_records:
                break

    return deduped
