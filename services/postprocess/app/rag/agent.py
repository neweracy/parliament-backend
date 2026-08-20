"""Conversational Hansard agent — LangChain `create_agent` with a retrieval tool.

The model decides for itself whether a turn needs evidence. A greeting or a
question about the assistant is answered directly; a question about proceedings
triggers the `search_hansard` tool and the reply is grounded in what came back.

This replaces an unconditional retrieve-then-answer flow. Because retrieval ran
on every turn, a greeting reached the model with zero chunks and the prompt's
no-evidence clause fired, so every conversational turn produced "no supporting
evidence" — not chatbot behaviour. Choosing when to search removes that at the
root.

Memory is deliberately not a langgraph checkpointer. The service runs several
uvicorn workers, so an in-process saver would lose a thread whenever a turn
landed on a different worker. Conversation history arrives explicitly on each
request instead, which stays correct at any worker count.

Requirements: 2.1, 2.2, 2.3, 2.6, 3.5, 3.6, 4.9, 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import asyncio
import calendar
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from langchain.agents import create_agent
from langchain_core.tools import tool
from sqlalchemy import text

from app.rag.circuit_breaker import CircuitBreaker
from app.rag.parsing import (
    GENERATION_FAILURE_TEXT,
    message_text,
    parse_citations,
    split_recommendations,
    strip_citation_markers,
    validate_citations,
)
from app.rag.recommendations import (
    AnswerResponse,
    CorpusHints,
    RecommendationBuilder,
    finalise_answer,
)
from app.rag.retriever import RetrievalFilters, RetrievedChunk

logger = structlog.get_logger("rag.agent")

# Passages returned per search_hansard call.
_MAX_SEARCH_RESULTS = 10

# Matches the conversation_history cap the gateway and router already enforce.
_MAX_HISTORY_MESSAGES = 20

# Super-step ceiling for the agent loop. Each tool round trip costs roughly two,
# so this allows several searches while still bounding a misbehaving model.
_RECURSION_LIMIT = 12

# Circuit breaker for the Bedrock model — shared across all agent instances
# within a single worker process. Opens after 5 consecutive failures, recovers
# after 60 seconds.
_bedrock_breaker = CircuitBreaker(
    name="bedrock_agent", failure_threshold=5, recovery_timeout_s=60.0
)

_NO_MATCH_TEXT = "No passages in the transcribed parliamentary record match that query."

# Items listed per find_recent_activity call, per scope (sittings, records).
_MAX_RECENT_ITEMS = 15

_MONTH_NAMES = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
_MONTH_ABBR = {name.lower(): index for index, name in enumerate(calendar.month_abbr) if name}

_SYSTEM_PROMPT = """You are the Parliamentary Research Assistant for the Ghana \
Parliament Hansard Management System. You are a conversational assistant — talk \
naturally, in plain prose.

## Your tools

`search_hansard(query)` searches the transcribed parliamentary record and returns
matching passages. Each passage is headed by a numeric chunk_id. Use it for
questions about the substance of proceedings.

`find_recent_activity(period, scope)` looks up sittings, records, and audio
uploads added to the registry — not what was said, but what was added and when.
Use it for questions like "what was uploaded recently", "what new sittings or
records were added", or "what records were added in July" / "in 2026-07".
`period` accepts "recent" (default, last 7 days), "today", "this month", "last
month", "last N days", "YYYY-MM", or a month name with an optional year.
`scope` is "sittings", "records", "uploads" (records with an audio file), or
"all" (default). This tool never needs citation markers — it reports registry
metadata, not transcript content.

## Deciding whether to search

Search `search_hansard` when the user asks about the substance of proceedings —
what was said, what was decided, what was debated, who spoke, when something
happened. Turn their wording into focused search terms rather than passing the
raw sentence, and resolve pronouns against the conversation before searching.

Use `find_recent_activity` when the user asks what is new in the registry
itself — recent uploads, newly added sittings or records, or what was added in
a given month — rather than what was discussed.

Do not call either tool for:
- greetings, thanks, or small talk
- questions about you or about how to use this assistant
- requests to rephrase, shorten, or explain your previous answer

Answer those directly and briefly. For a greeting or a capability question, say
what you can look up in the parliamentary record.

You may call a tool more than once if the first call was too narrow or too broad.

## Citing the record

Every claim you draw from a retrieved passage must carry that passage's chunk_id
in square brackets, immediately after the claim:

    The Minister moved that the House approve the Statement [42].

Cite all supporting passages when several apply: [42][15].

Cite only chunk_ids that `search_hansard` actually returned to you in this
conversation. Never invent a chunk_id. Never attach a citation marker to a
statement that did not come from the record, including anything you say in a
conversational reply. `find_recent_activity` results are registry metadata, not
transcript passages — never invent or attach a chunk_id citation to them.

## When a tool returns nothing

Say plainly that the transcribed record (or the registry, for
`find_recent_activity`) contains nothing on the question, then offer one
narrower and one broader way to ask it. Do not guess an answer and do not fall
back on general knowledge about parliaments.

## Recommendations

End every reply — conversational or grounded — with exactly this block:

RECOMMENDATIONS:
- <follow-up question> | <short reason it is relevant>
- <follow-up question> | <short reason it is relevant>
- <follow-up question> | <short reason it is relevant>

Base them on topics, speakers, dates, or sittings that came back from your
tool calls and that the user has not already asked about. With nothing
retrieved, suggest ways into the record instead.

## Hard limits

- Never state a fact about proceedings that is not in a passage you retrieved.
- Never cite a chunk_id you did not receive.
- Do not speculate about what Parliament might have said or about what is in
  the registry beyond what a tool told you.
"""


def _format_passages(chunks: list[RetrievedChunk]) -> str:
    """Render chunks for the model, leading with the chunk_id it must cite."""
    blocks: list[str] = []
    for chunk in chunks:
        header = [f"chunk_id: {chunk.chunk_id}"]
        if chunk.speaker:
            header.append(f"speaker: {chunk.speaker}")
        if chunk.sitting_title:
            header.append(f"sitting: {chunk.sitting_title}")
        if chunk.record_title:
            header.append(f"record: {chunk.record_title}")
        if chunk.date:
            header.append(f"date: {chunk.date}")
        if chunk.start_s is not None and chunk.end_s is not None:
            header.append(f"time: {chunk.start_s:.1f}s-{chunk.end_s:.1f}s")
        blocks.append("[" + " | ".join(header) + "]\n" + chunk.text)
    return "\n\n".join(blocks)


def _make_search_tool(retriever: Any, filters: RetrievalFilters | None, collector: list):
    """Build a `search_hansard` tool bound to one request's retriever and collector.

    Retrieval happens inside the tool, so the surrounding code cannot see the
    chunks unless the tool records them — and citations, `source_chunks`, and the
    recommendation inputs all need them. The collector is that channel. It is
    created fresh per request, which keeps turns isolated, and it de-duplicates
    by chunk_id so repeated searches do not double-count a passage.
    """
    seen_ids: set[int] = set()

    @tool
    async def search_hansard(query: str) -> str:
        """Search the transcribed Ghana parliamentary record for relevant passages.

        Use for any question about the substance of proceedings — what was said or
        decided, which members spoke, what a sitting covered. Pass focused search
        terms rather than the user's raw sentence.

        Args:
            query: Search terms, for example "education budget allocation".

        Returns:
            Matching passages, each headed by the chunk_id to cite it with, or a
            note that nothing matched.
        """
        chunks = await retriever.retrieve(query=query, filters=filters, limit=_MAX_SEARCH_RESULTS)

        for chunk in chunks:
            if chunk.chunk_id not in seen_ids:
                seen_ids.add(chunk.chunk_id)
                collector.append(chunk)

        logger.info(
            "rag.agent.search_hansard",
            query_preview=query[:80],
            results=len(chunks),
            collected_total=len(collector),
        )

        if not chunks:
            return _NO_MATCH_TEXT
        return _format_passages(chunks)

    return search_hansard


class _PeriodParseError(ValueError):
    """Raised when a period string cannot be resolved to a date range."""


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Return the [start, end) UTC bounds of a calendar month."""
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


def _resolve_period(period: str, *, now: datetime) -> tuple[datetime, datetime, str]:
    """Resolve a free-form period string to a `[start, end)` UTC range.

    Accepts, case-insensitively: "recent"/"" (last 7 days), "today", "this
    month", "last month", "last N days", "YYYY-MM", "YYYY-MM-DD..YYYY-MM-DD",
    and a month name with an optional year (e.g. "July", "July 2026", "Jul").

    Returns the range plus a human-readable label for the range, so the tool's
    response can tell the model exactly what window it searched.

    Raises:
        _PeriodParseError: If the string does not match any known form.
    """
    raw = (period or "").strip().lower()

    if raw in ("", "recent"):
        start = now - timedelta(days=7)
        return start, now, "the last 7 days"

    if raw == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, "today"

    if raw == "this month":
        start, end = _month_bounds(now.year, now.month)
        return start, min(end, now), f"{calendar.month_name[now.month]} {now.year}"

    if raw == "last month":
        first_of_this_month = now.replace(day=1)
        last_month_end = first_of_this_month
        prior = last_month_end - timedelta(days=1)
        start, end = _month_bounds(prior.year, prior.month)
        return start, end, f"{calendar.month_name[prior.month]} {prior.year}"

    days_match = re.fullmatch(r"last (\d+) days?", raw)
    if days_match:
        n = int(days_match.group(1))
        start = now - timedelta(days=n)
        return start, now, f"the last {n} days"

    iso_month_match = re.fullmatch(r"(\d{4})-(\d{2})", raw)
    if iso_month_match:
        year, month = int(iso_month_match.group(1)), int(iso_month_match.group(2))
        if not 1 <= month <= 12:
            raise _PeriodParseError(f"Invalid month in period '{period}'")
        start, end = _month_bounds(year, month)
        return start, min(end, now) if end > now else end, f"{calendar.month_name[month]} {year}"

    range_match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", raw)
    if range_match:
        start = datetime.strptime(range_match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
        end = datetime.strptime(range_match.group(2), "%Y-%m-%d").replace(
            tzinfo=UTC
        ) + timedelta(days=1)
        return start, end, f"{range_match.group(1)} to {range_match.group(2)}"

    month_word_match = re.fullmatch(r"([a-z]+)\.?\s*(\d{4})?", raw)
    if month_word_match:
        month_token, year_token = month_word_match.groups()
        month = _MONTH_NAMES.get(month_token) or _MONTH_ABBR.get(month_token)
        if month:
            year = int(year_token) if year_token else now.year
            start, end = _month_bounds(year, month)
            bounded_end = min(end, now) if end > now else end
            return start, bounded_end, f"{calendar.month_name[month]} {year}"

    raise _PeriodParseError(
        f"Could not parse period '{period}'. Try 'recent', 'today', 'this month', "
        "'last month', 'last N days', 'YYYY-MM', or a month name."
    )


_RECENT_ACTIVITY_SQL = """
    SELECT
        'sitting' AS kind,
        s.id,
        s.title,
        NULL::text AS sitting_title,
        s.created_at,
        NULL::text AS audio_file_name
    FROM sitting s
    WHERE :want_sittings AND s.created_at >= :start AND s.created_at < :end

    UNION ALL

    SELECT
        'record' AS kind,
        hr.id,
        hr.title,
        s.title AS sitting_title,
        hr.created_at,
        hr.audio_file_name
    FROM hansard_record hr
    JOIN sitting s ON s.id = hr.sitting_id
    WHERE :want_records
      AND hr.created_at >= :start AND hr.created_at < :end
      AND (NOT :uploads_only OR hr.audio_file_name IS NOT NULL)

    ORDER BY created_at DESC
    LIMIT :limit
"""


def _make_recent_activity_tool(session_factory: Any, now_fn=None):
    """Build a `find_recent_activity` tool bound to one request's DB session.

    Answers "what's new in the registry" questions directly from
    `sitting.created_at` / `hansard_record.created_at` rather than through
    transcript search — these are questions about what was added and when,
    not about what was said. Kept as its own tool (rather than folded into
    `search_hansard`) because it queries different tables entirely and never
    produces citable chunk_ids.

    `now_fn` is injectable for tests; defaults to `datetime.now(UTC)`.
    """
    clock = now_fn or (lambda: datetime.now(UTC))

    @tool
    async def find_recent_activity(period: str = "recent", scope: str = "all") -> str:
        """Look up sittings and records added to the registry in a time window.

        Use for "what's new" questions — recent uploads, newly added sittings or
        records, or what was added in a given month — as distinct from what was
        said in proceedings (use `search_hansard` for that).

        Args:
            period: Time window. Accepts "recent" (default, last 7 days),
                "today", "this month", "last month", "last N days", "YYYY-MM",
                "YYYY-MM-DD..YYYY-MM-DD", or a month name with an optional year
                (e.g. "July", "July 2026").
            scope: Which registry items to include: "sittings", "records",
                "uploads" (records that have an audio file attached), or "all"
                (default).

        Returns:
            A list of matching sittings/records with their creation dates, or a
            note that nothing was added in that window.
        """
        now = clock()
        try:
            start, end, label = _resolve_period(period, now=now)
        except _PeriodParseError as exc:
            return str(exc)

        scope_key = (scope or "all").strip().lower()
        want_sittings = scope_key in ("all", "sittings")
        want_records = scope_key in ("all", "records", "uploads")
        uploads_only = scope_key == "uploads"

        if not want_sittings and not want_records:
            return (
                f"Unknown scope '{scope}'. Use 'sittings', 'records', 'uploads', "
                "or 'all'."
            )

        try:
            async with session_factory() as session:
                result = await session.execute(
                    text(_RECENT_ACTIVITY_SQL),
                    {
                        "want_sittings": want_sittings,
                        "want_records": want_records,
                        "uploads_only": uploads_only,
                        "start": start,
                        "end": end,
                        "limit": _MAX_RECENT_ITEMS,
                    },
                )
                rows = result.fetchall()
        except Exception:
            logger.error(
                "rag.agent.find_recent_activity_failed",
                period=period,
                scope=scope,
                exc_info=True,
            )
            return "Could not look up recent registry activity right now."

        logger.info(
            "rag.agent.find_recent_activity",
            period=period,
            resolved_label=label,
            scope=scope,
            results=len(rows),
        )

        if not rows:
            return f"Nothing was added to the registry in {label} ({scope_key})."

        lines = [f"Added in {label} ({len(rows)} item(s), most recent first):"]
        for kind, item_id, title, sitting_title, created_at, audio_file_name in rows:
            when = created_at.strftime("%Y-%m-%d %H:%M UTC") if created_at else "unknown date"
            if kind == "sitting":
                lines.append(f"- Sitting #{item_id} \"{title}\" — created {when}")
            else:
                upload_note = f", audio: {audio_file_name}" if audio_file_name else ""
                lines.append(
                    f"- Record #{item_id} \"{title}\" (sitting: {sitting_title}) — "
                    f"created {when}{upload_note}"
                )
        return "\n".join(lines)

    return find_recent_activity


class HansardChatAgent:
    """Conversational agent over the transcribed parliamentary record.

    Wraps LangChain's `create_agent` with a single retrieval tool, so the model
    decides whether a turn needs evidence. Returns the same `AnswerResponse` the
    grounded chain returned, which leaves the router's Pydantic models, the
    gateway's camelCase mapping, and the frontend contract unchanged.

    Usage::

        agent = HansardChatAgent(chat_model, retriever)
        response = await agent.chat("What was said about the budget?")
    """

    def __init__(
        self,
        chat_model: Any,
        retriever: Any,
        settings: Any = None,
        session_factory: Any = None,
    ) -> None:
        self._chat_model = chat_model
        self._retriever = retriever
        self._settings = settings
        self._session_factory = session_factory
        self._builder = RecommendationBuilder()

    async def chat(
        self,
        question: str,
        filters: RetrievalFilters | None = None,
        conversation_history: list[tuple[str, str]] | None = None,
        corpus_hints: CorpusHints | None = None,
    ) -> AnswerResponse:
        """Answer one conversational turn, searching the record only if needed.

        Args:
            question: The user's message for this turn.
            filters: Optional retrieval pre-filters applied to every search.
            conversation_history: Prior `(role, content)` turns, newest last.
            corpus_hints: Corpus-wide entity and speaker names, used as a
                recommendation source when nothing was retrieved.

        Returns:
            An `AnswerResponse`. Citations are non-empty only when the model both
            searched and cited a chunk_id that came back.
        """
        start_time = time.perf_counter()

        history = list(conversation_history or [])[-_MAX_HISTORY_MESSAGES:]
        history_questions = [content for role, content in history if role == "user"]

        # Circuit breaker: if Bedrock has been failing repeatedly, return a
        # friendly message immediately rather than waiting for another timeout.
        if not _bedrock_breaker.allow_request():
            return finalise_answer(
                builder=self._builder,
                answer=(
                    "The AI service is temporarily unavailable due to repeated "
                    "errors. Please try again in a minute."
                ),
                citations=[],
                context_chunks=[],
                parsed=[],
                hint_chunks=[],
                corpus_hints=corpus_hints,
                history_questions=history_questions,
                grounded=False,
                start_time=start_time,
            )

        # Everything search_hansard retrieved this turn, in first-seen order.
        retrieved: list[RetrievedChunk] = []

        tools = [_make_search_tool(self._retriever, filters, retrieved)]
        if self._session_factory is not None:
            tools.append(_make_recent_activity_tool(self._session_factory))

        agent = create_agent(
            model=self._chat_model,
            tools=tools,
            system_prompt=_SYSTEM_PROMPT,
        )

        messages: list[dict[str, str]] = [
            {"role": role, "content": content} for role, content in history
        ]
        messages.append({"role": "user", "content": question})

        logger.info(
            "rag.agent.start",
            question_preview=question[:80],
            history_turns=len(history),
            has_filters=filters is not None,
        )

        try:
            # Wall-clock timeout bounds the full agent loop (multiple tool
            # calls + model invocations). The gateway aborts at 30s, so this
            # must finish well inside that window to return a structured error
            # rather than a TCP reset.
            timeout_s = (
                self._settings.rag_agent_timeout_s if self._settings else 25
            )
            result = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": messages},
                    config={"recursion_limit": _RECURSION_LIMIT},
                ),
                timeout=timeout_s,
            )
            raw_answer = message_text(result["messages"][-1])
            _bedrock_breaker.record_success()
        except TimeoutError:
            _bedrock_breaker.record_failure()
            logger.warning(
                "rag.agent.timeout",
                question_preview=question[:80],
                timeout_s=timeout_s if "timeout_s" in dir() else 25,
            )
            return finalise_answer(
                builder=self._builder,
                answer="The request took too long to process. Please try a more specific question.",
                citations=[],
                context_chunks=retrieved,
                parsed=[],
                hint_chunks=retrieved,
                corpus_hints=corpus_hints,
                history_questions=history_questions,
                grounded=False,
                start_time=start_time,
            )
        except Exception:
            _bedrock_breaker.record_failure()
            logger.error(
                "rag.agent.invocation_failed",
                question_preview=question[:80],
                exc_info=True,
            )
            return finalise_answer(
                builder=self._builder,
                answer=GENERATION_FAILURE_TEXT,
                citations=[],
                context_chunks=retrieved,
                parsed=[],
                hint_chunks=retrieved,
                corpus_hints=corpus_hints,
                history_questions=history_questions,
                grounded=False,
                start_time=start_time,
            )

        answer_text, parsed = split_recommendations(raw_answer)

        # Grounded means the model searched and got something back. When it did
        # not, nothing is citable, so any marker it emitted is a dead reference
        # and is stripped rather than surfaced to the user.
        grounded = bool(retrieved)
        if grounded:
            chunk_map = {chunk.chunk_id: chunk for chunk in retrieved}
            citations = validate_citations(parse_citations(answer_text), chunk_map)
        else:
            answer_text = strip_citation_markers(answer_text)
            citations = []

        logger.info(
            "rag.agent.complete",
            question_preview=question[:80],
            searched=grounded,
            retrieved_count=len(retrieved),
            citation_count=len(citations),
            parsed_recommendation_count=len(parsed),
        )

        return finalise_answer(
            builder=self._builder,
            answer=answer_text,
            citations=citations,
            context_chunks=retrieved,
            parsed=parsed,
            hint_chunks=retrieved,
            corpus_hints=corpus_hints,
            history_questions=history_questions,
            grounded=grounded,
            start_time=start_time,
        )
