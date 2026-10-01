"""Sitting_Scope resolution — expected member set for a parliamentary sitting.

Requirement 8 (Sitting-Scoped Candidate Preference) prefers corrections toward
the people who were actually in the chamber on a given sitting day. This module
resolves that Sitting_Scope: given a ``sitting_id`` and a database session, it
returns the set of canonical person names expected to appear in that sitting as
a ``frozenset[str]``.

Assumed schema
--------------
This service owns no dedicated sitting-membership table (Req 8 is about
*consuming* a Sitting_Scope, not defining one). The scope is derived from the
Hansard schema already present (migration 003):

    sitting (id, ...)
      └── hansard_record (id, sitting_id → sitting.id, ...)
            └── transcript (id, record_id → hansard_record.id, ...)
                  └── transcript_chunk (id, transcript_id → transcript.id,
                                        entity_names text[], ...)

``transcript_chunk.entity_names`` holds the entity names recognised within each
retrieval chunk. The distinct set of those names across every chunk belonging to
the records of a sitting is the best available proxy for "who appeared in that
sitting". :func:`resolve_sitting_scope` collects them with a single parameterised
query and returns them as a frozenset.

Because ``entity_names`` is not restricted to persons, the returned set is a
superset of the person names; the engine only ever consults it for person
candidates (Req 8.6) and membership is a pure set lookup, so non-person names in
the set are harmless. When richer sitting-membership data becomes available, only
this resolver changes — its callers already treat an empty result as "scope
unavailable, apply no adjustment" (Req 8.5).

Failure handling (Req 8.5, 8.10)
--------------------------------
The resolver is defensive: a sitting id that matches no rows, a query error, or
a missing table all yield an empty frozenset, which the pipeline treats as an
unavailable Sitting_Scope and proceeds with no Evidence_Score adjustment. The
2-second timeout that Req 8.5/8.10 mandate is enforced by the caller
(:func:`app.pipeline.run_pipeline`) via ``asyncio.wait_for`` around this
coroutine, so the resolver itself stays a simple awaitable query. A
``timeout_s`` parameter is accepted for callers that prefer the resolver to own
its own timeout.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("sittings")

# Default Sitting_Scope lookup timeout in seconds (Req 8.5, 8.10).
DEFAULT_SITTING_SCOPE_TIMEOUT_S: float = 2.0


async def _query_sitting_members(
    session: AsyncSession, sitting_id: str
) -> frozenset[str]:
    """Query the distinct entity names appearing in a sitting's transcripts.

    Walks ``sitting → hansard_record → transcript → transcript_chunk`` and
    unnests ``transcript_chunk.entity_names`` for every chunk belonging to the
    sitting, returning the distinct set. Uses a parameterised query
    (``:sitting_id``) so the identifier is never interpolated into SQL.

    The ``sitting.id`` column is a ``BIGINT``; the supplied identifier is a
    string per Req 8.1, so it is bound as text and cast in SQL. A non-numeric
    identifier simply matches no rows and yields an empty set.
    """
    result = await session.execute(
        text(
            """
            SELECT DISTINCT en AS name
            FROM hansard_record hr
            JOIN transcript t ON t.record_id = hr.id
            JOIN transcript_chunk tc ON tc.transcript_id = t.id
            CROSS JOIN LATERAL unnest(tc.entity_names) AS en
            WHERE hr.sitting_id = CAST(:sitting_id AS BIGINT)
              AND en IS NOT NULL
              AND length(trim(en)) > 0
            """
        ),
        {"sitting_id": sitting_id},
    )
    rows = result.fetchall()
    return frozenset(row[0] for row in rows if row[0])


async def resolve_sitting_scope(
    session: AsyncSession,
    sitting_id: str,
    *,
    timeout_s: float = DEFAULT_SITTING_SCOPE_TIMEOUT_S,
) -> frozenset[str]:
    """Resolve the Sitting_Scope member set for *sitting_id* (Req 8.3, 8.5, 8.10).

    Returns a ``frozenset`` of canonical person names (in practice, the distinct
    entity names) expected in the sitting. Returns an **empty** frozenset on any
    of the unavailable conditions of Req 8.5 — the identifier matches no sitting,
    the resolved member set is empty, the lookup returns an error, the query
    references a table that does not exist, or the lookup does not complete
    within *timeout_s* — so the caller applies no Evidence_Score adjustment and
    logs the single ``sitting_scope.unavailable`` event.

    This resolver never raises: every failure mode is caught and mapped to the
    empty result, so a Sitting_Scope lookup can never break the pipeline
    (Req 8.10). The caller may additionally wrap the call in its own
    ``asyncio.wait_for`` (the pipeline does), in which case the timeout is
    enforced whichever bound fires first.

    Parameters
    ----------
    session:
        An open async SQLAlchemy session against the correction database.
    sitting_id:
        The sitting identifier from ``CorrectionRequest.sitting_id`` (Req 8.1).
        Assumed already validated as a non-empty, non-whitespace string by the
        caller; a value that matches no sitting yields an empty set.
    timeout_s:
        Maximum wall-clock seconds for the lookup (Req 8.5). Defaults to 2.0.
    """
    try:
        return await asyncio.wait_for(
            _query_sitting_members(session, sitting_id), timeout=timeout_s
        )
    except TimeoutError:
        # Lookup exceeded the bound (Req 8.5, 8.10) — unavailable, no adjustment.
        return frozenset()
    except Exception:  # noqa: BLE001 - deliberate catch-all
        # Any other failure — missing table, query error, connection drop — is
        # an unavailable Sitting_Scope (Req 8.5, 8.10). Return empty; the caller
        # logs the single debug event. Deliberately broad: the resolver must
        # never surface an error to the pipeline.
        return frozenset()
