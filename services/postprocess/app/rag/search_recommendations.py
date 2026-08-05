"""Search-query recommendations — one focused model call, always non-empty.

Backs `POST /rag/recommendations`, the endpoint the search box calls to offer
the researcher better query terms than the one they typed.

Why this exists as its own path
-------------------------------
The gateway used to synthesise search recommendations by calling `/rag/ask` with
a prompt that asked the model to wrap query terms in markdown bold, then
scraping `**...**` out of the follow-up *questions* that came back. That was
wrong in three ways at once:

- It ran the full conversational agent — tool loop, retrieval, recursion budget
  — to produce what is really a one-shot list of phrases. Multi-second latency
  for a typeahead.
- The agent's own system prompt mandates `question | reason` lines, so it was
  competing with the gateway's bold instruction. Whenever the agent prompt won,
  the bold filter matched nothing and the endpoint returned an empty list.
- `text` therefore held a follow-up question on one endpoint and a bare search
  term on the other, while both were typed as the same `Recommendation`.

This module replaces that with a single model call whose only job is producing
search terms, parsed by the shared `parse_recommendation_lines`, then topped up
from `RecommendationBuilder.search_queries` so the result is never empty — the
same guarantee `finalise_answer` gives the Q&A path.

Every failure mode degrades instead of raising: no model configured, model
error, model timeout, retrieval error, or unparseable output all fall through to
the deterministic builder.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.rag.parsing import message_text, parse_recommendation_lines
from app.rag.recommendations import (
    TARGET_SEARCH_RECOMMENDATION_COUNT,
    CorpusHints,
    RecommendationBuilder,
    RecommendationItem,
    chunks_to_documents,
    normalise_question,
)
from app.rag.retriever import RetrievalFilters, RetrievedChunk

logger = structlog.get_logger("rag.search_recommendations")

# Deadline for the single model call. The gateway aborts at 30s and the browser
# gives up at 30s too, so the model has to finish well inside that or the user
# sees a timeout instead of the deterministic fallback this module can always
# produce. A one-shot call with a small output has no business taking longer.
LLM_TIMEOUT_S = 12.0

# Deadline for the grounding retrieval. Best-effort context only — the builder
# has corpus hints to fall back on — so this stays tight.
RETRIEVAL_TIMEOUT_S = 5.0

# Chunks pulled to ground the prompt. Enough to name real speakers, entities,
# and sittings without inflating the prompt.
RETRIEVAL_LIMIT = 6

# Characters of chunk text shown per passage in the prompt. The model only needs
# enough to recognise the topic, not to quote it.
_CHUNK_PREVIEW_CHARS = 240

_SYSTEM_PROMPT = """You suggest search queries for the Ghana Parliament Hansard \
archive — a corpus of transcribed parliamentary proceedings.

Your output is typed directly into a keyword search box, so suggest search
TERMS, not questions. "budget allocation education" is useful. "What was said
about the education budget?" is not.

Rules:
- Two to five words per query. No question marks, no sentences.
- Prefer terms grounded in the CORPUS CONTEXT you are given: real speaker names,
  entities, sitting titles, and topics that actually appear there.
- Each query must be meaningfully different from the others and from the query
  the researcher already typed. Do not restate their query with a synonym.
- Give a short, concrete reason naming what the query would surface.

Reply with one recommendation per line and nothing else — no preamble, no
numbering, no closing remark:

<search query> | <short reason>
"""


@dataclass
class SearchRecommendationResult:
    """Outcome of a search-recommendation request.

    Attributes:
        recommendations: Exactly the requested count, unless the count was not
            positive. Each item has non-empty `text` and `reason`.
        source: Where the items came from — `"model"` when every item was
            parsed from the model, `"deterministic"` when none were, `"mixed"`
            when the model supplied some and the builder topped up the rest.
        model_used: True when the model call was attempted and returned text.
        latency_ms: Total wall time for the request.
    """

    recommendations: list[RecommendationItem] = field(default_factory=list)
    source: str = "deterministic"
    model_used: bool = False
    latency_ms: float = 0.0


def _format_corpus_context(
    chunks: list[RetrievedChunk],
    corpus_hints: CorpusHints | None,
) -> str:
    """Render retrieved chunks and corpus hints as prompt context.

    Returns an empty string when there is nothing to show, which is the signal
    to the caller that the model would be guessing and the deterministic path is
    the better answer.
    """
    sections: list[str] = []

    if chunks:
        passages: list[str] = []
        for chunk in chunks:
            header: list[str] = []
            if chunk.speaker:
                header.append(f"speaker: {chunk.speaker}")
            if chunk.sitting_title:
                header.append(f"sitting: {chunk.sitting_title}")
            if chunk.record_title:
                header.append(f"record: {chunk.record_title}")
            if chunk.date:
                header.append(f"date: {chunk.date}")
            preview = (chunk.text or "")[:_CHUNK_PREVIEW_CHARS].strip()
            prefix = f"[{' | '.join(header)}] " if header else ""
            passages.append(f"- {prefix}{preview}")
        sections.append("Passages matching the current query:\n" + "\n".join(passages))

    if corpus_hints:
        if corpus_hints.entities:
            sections.append("Entities in the corpus: " + ", ".join(corpus_hints.entities[:25]))
        if corpus_hints.speakers:
            sections.append("Speakers in the corpus: " + ", ".join(corpus_hints.speakers[:25]))

    return "\n\n".join(sections)


def _build_user_prompt(query: str | None, context: str, count: int) -> str:
    """Compose the user turn: what the researcher typed plus grounding context."""
    typed = (query or "").strip()

    lines: list[str] = []
    if typed:
        lines.append(f'The researcher typed: "{typed}"')
        lines.append(
            f"Suggest {count} alternative or narrowing search queries that would surface "
            "relevant proceedings."
        )
    else:
        lines.append("The researcher has not typed anything yet.")
        lines.append(f"Suggest {count} search queries that are good entry points into this corpus.")

    if context:
        lines.append("\nCORPUS CONTEXT\n" + context)

    return "\n".join(lines)


async def _retrieve_context(
    retriever: Any,
    query: str | None,
    filters: RetrievalFilters | None,
) -> list[RetrievedChunk]:
    """Retrieve grounding chunks, returning empty on any failure.

    Grounding is an optimisation: it makes the model name real values and gives
    the builder better fallback candidates. It must never fail the request, so
    every error — including a timeout — resolves to an empty list.
    """
    typed = (query or "").strip()
    if retriever is None or not typed:
        return []

    try:
        return await asyncio.wait_for(
            retriever.retrieve(query=typed, filters=filters, limit=RETRIEVAL_LIMIT),
            timeout=RETRIEVAL_TIMEOUT_S,
        )
    except TimeoutError:
        logger.warning("rag.search_recommendations.retrieval_timeout", query_preview=typed[:80])
        return []
    except Exception:
        logger.warning(
            "rag.search_recommendations.retrieval_failed",
            query_preview=typed[:80],
            exc_info=True,
        )
        return []


async def _invoke_model(chat_model: Any, prompt: str) -> str:
    """Run the one-shot model call, returning empty text on any failure.

    No tools and no agent loop: this is a single request whose entire output is
    a handful of lines, which is what keeps it inside `LLM_TIMEOUT_S`.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await asyncio.wait_for(
            chat_model.ainvoke(messages),
            timeout=LLM_TIMEOUT_S,
        )
    except TimeoutError:
        logger.warning("rag.search_recommendations.llm_timeout", timeout_s=LLM_TIMEOUT_S)
        return ""
    except Exception:
        logger.error("rag.search_recommendations.llm_failed", exc_info=True)
        return ""

    return message_text(response)


async def generate_search_recommendations(
    *,
    chat_model: Any | None,
    query: str | None = None,
    retriever: Any | None = None,
    filters: RetrievalFilters | None = None,
    corpus_hints: CorpusHints | None = None,
    count: int = TARGET_SEARCH_RECOMMENDATION_COUNT,
    builder: RecommendationBuilder | None = None,
) -> SearchRecommendationResult:
    """Produce `count` search-query recommendations, never fewer.

    Grounds a single model call in retrieved passages and corpus hints, parses
    the returned `query | reason` lines, then tops the set up deterministically.
    The model is an enhancement on top of a guaranteed floor, not a dependency:
    with no model configured, or a failing or slow one, the result is still a
    full set of corpus-grounded terms.

    Args:
        chat_model: Chat model to call. None skips straight to the builder.
        query: What the researcher has typed so far. May be empty.
        retriever: Retriever used for grounding context. None skips retrieval.
        filters: Retrieval pre-filters mirroring the user's active search
            filters, so suggestions stay inside the slice they are looking at.
        corpus_hints: Corpus-wide entity and speaker names for the fallback.
        count: Exact number of items to return. Zero or negative yields none.
        builder: Injectable builder, for tests. Defaults to a fresh instance.

    Returns:
        A `SearchRecommendationResult` whose `recommendations` holds exactly
        `count` distinct items, each with non-empty `text` and `reason`.
    """
    start_time = time.perf_counter()

    def finish(
        items: list[RecommendationItem],
        source: str,
        model_used: bool,
    ) -> SearchRecommendationResult:
        return SearchRecommendationResult(
            recommendations=items,
            source=source,
            model_used=model_used,
            latency_ms=(time.perf_counter() - start_time) * 1000,
        )

    if count <= 0:
        return finish([], "deterministic", False)

    active_builder = builder or RecommendationBuilder()
    typed = (query or "").strip()

    chunks = await _retrieve_context(retriever, typed, filters)

    parsed: list[RecommendationItem] = []
    model_used = False

    if chat_model is not None:
        context = _format_corpus_context(chunks, corpus_hints)
        raw = await _invoke_model(chat_model, _build_user_prompt(typed, context, count))
        model_used = bool(raw.strip())

        if model_used:
            # `allow_bare_lines` because the prompt asks for plain `query |
            # reason` lines; the model is told not to number or bullet them, but
            # accepting either costs nothing and removes a failure mode.
            parsed = parse_recommendation_lines(raw, limit=count, allow_bare_lines=True)

    # The typed query is excluded so a suggestion never just echoes it back.
    seen: set[str] = {normalise_question(typed)} if typed else set()

    recommendations: list[RecommendationItem] = []
    for item in parsed:
        key = normalise_question(item.text)
        if not key or key in seen:
            continue
        seen.add(key)
        recommendations.append(item)
        if len(recommendations) == count:
            break

    model_kept = len(recommendations)

    if model_kept < count:
        recommendations += active_builder.search_queries(
            chunks=chunks_to_documents(chunks),
            corpus_hints=corpus_hints,
            exclude=[item.text for item in recommendations] + ([typed] if typed else []),
            count=count - model_kept,
        )

    if model_kept == 0:
        source = "deterministic"
    elif model_kept == len(recommendations):
        source = "model"
    else:
        source = "mixed"

    result = finish(recommendations, source, model_used)

    logger.info(
        "rag.search_recommendations.assembled",
        query_preview=typed[:80],
        grounding_chunks=len(chunks),
        model_used=model_used,
        model_parsed=len(parsed),
        model_kept=model_kept,
        topped_up=len(recommendations) - model_kept,
        source=source,
        latency_ms=round(result.latency_ms, 1),
    )

    return result
