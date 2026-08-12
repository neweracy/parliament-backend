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
import time
from typing import Any

import structlog
from langchain.agents import create_agent
from langchain_core.tools import tool

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

_SYSTEM_PROMPT = """You are the Parliamentary Research Assistant for the Ghana \
Parliament Hansard Management System. You are a conversational assistant — talk \
naturally, in plain prose.

## Your tool

`search_hansard(query)` searches the transcribed parliamentary record and returns
matching passages. Each passage is headed by a numeric chunk_id.

## Deciding whether to search

Search when the user asks about the substance of proceedings — what was said,
what was decided, what was debated, who spoke, when something happened. Turn
their wording into focused search terms rather than passing the raw sentence,
and resolve pronouns against the conversation before searching.

Do not search for:
- greetings, thanks, or small talk
- questions about you or about how to use this assistant
- requests to rephrase, shorten, or explain your previous answer

Answer those directly and briefly. For a greeting or a capability question, say
what you can look up in the parliamentary record.

You may search more than once if the first query was too narrow or too broad.

## Citing the record

Every claim you draw from a retrieved passage must carry that passage's chunk_id
in square brackets, immediately after the claim:

    The Minister moved that the House approve the Statement [42].

Cite all supporting passages when several apply: [42][15].

Cite only chunk_ids that `search_hansard` actually returned to you in this
conversation. Never invent a chunk_id. Never attach a citation marker to a
statement that did not come from the record, including anything you say in a
conversational reply.

## When the search returns nothing

Say plainly that the transcribed record contains nothing on the question, then
offer one narrower and one broader way to ask it. Do not guess an answer and do
not fall back on general knowledge about parliaments.

## Recommendations

End every reply — conversational or grounded — with exactly this block:

RECOMMENDATIONS:
- <follow-up question> | <short reason it is relevant>
- <follow-up question> | <short reason it is relevant>
- <follow-up question> | <short reason it is relevant>

Base them on topics, speakers, dates, or sittings that came back from
`search_hansard` and that the user has not already asked about. With nothing
retrieved, suggest ways into the record instead.

## Hard limits

- Never state a fact about proceedings that is not in a passage you retrieved.
- Never cite a chunk_id you did not receive.
- Do not speculate about what Parliament might have said.
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

    def __init__(self, chat_model: Any, retriever: Any, settings: Any = None) -> None:
        self._chat_model = chat_model
        self._retriever = retriever
        self._settings = settings
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

        agent = create_agent(
            model=self._chat_model,
            tools=[_make_search_tool(self._retriever, filters, retrieved)],
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
