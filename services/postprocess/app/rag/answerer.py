"""Grounded answering chain — generates cited answers from retrieved chunks.

Uses LangChain ChatBedrock for async-native model invocation and
ChatPromptTemplate for prompt construction. Replaces the hand-rolled
BedrockClient + thread executor approach in answering.py.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""

from __future__ import annotations

import time

import structlog
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings
from app.rag.parsing import (
    GENERATION_FAILURE_TEXT,
    parse_citations,
    split_recommendations,
    strip_citation_markers,
    validate_citations,
)
from app.rag.recommendations import (
    AnswerResponse,
    Citation,
    CorpusHints,
    RecommendationBuilder,
    RecommendationItem,
    finalise_answer,
)
from app.rag.retriever import RetrievedChunk

logger = structlog.get_logger("rag.answerer")

# Default relevance threshold — chunks below this score are excluded
_DEFAULT_RELEVANCE_THRESHOLD = 0.01

# Maximum number of chunks to include in the prompt context
_MAX_CONTEXT_CHUNKS = 10

# Placeholder emitted in the prompt's context block when no chunk is citable
_NO_SOURCE_CHUNKS_TEXT = "(NO SOURCE CHUNKS AVAILABLE FOR THIS QUESTION)"

# System prompt instructing Claude to produce cited answers with recommendations
_SYSTEM_PROMPT = (
    "You are a parliamentary research assistant chatbot for the "
    "Ghana Parliamentary Hansard System.\n"
    "\n"
    "## Mode Selection\n"
    "\n"
    "Based on the user's message and available source chunks, "
    "select ONE mode:\n"
    "\n"
    "### Mode A — Conversational (no evidence needed)\n"
    "Use when the user sends a greeting, asks what you can do, "
    "or requests clarification/rephrasing.\n"
    "- Reply directly and state what you can do with the "
    "parliamentary record.\n"
    "- Do NOT emit citation markers like [42].\n"
    "- Do NOT assert any fact about specific proceedings.\n"
    "\n"
    "### Mode B — Grounded (evidence available)\n"
    "Use when source chunks are provided and the user asks a "
    "factual question about proceedings.\n"
    "Follow these rules strictly:\n"
    "\n"
    "1. Answer the question using ONLY information from the "
    "provided chunks.\n"
    "2. Cite your sources using [chunk_id] notation (e.g., [42]) "
    "immediately after the relevant statement.\n"
    "3. If multiple chunks support a statement, cite all of them "
    "(e.g., [42][15]).\n"
    "4. Do NOT invent or infer information not present in the "
    "chunks.\n"
    "5. If the chunks do not contain enough information to answer "
    "the question, say so explicitly.\n"
    "6. Keep your answer concise and factual.\n"
    "7. When quoting speakers, attribute the quote to the speaker "
    "mentioned in the chunk metadata.\n"
    "8. If conversation history is provided, use it to understand "
    "context and resolve pronouns/references from prior "
    "exchanges.\n"
    "\n"
    "### No-source-chunks clause\n"
    "When the Source Chunks section contains "
    '"(NO SOURCE CHUNKS AVAILABLE FOR THIS QUESTION)":\n'
    "- State that the supplied record contains no supporting "
    "evidence for this question.\n"
    "- Suggest one narrower way to rephrase the question.\n"
    "- Suggest one broader way to rephrase the question.\n"
    "\n"
    "## Recommendations (both modes)\n"
    "\n"
    "9. After your answer, ALWAYS include a RECOMMENDATIONS "
    "section with exactly 3 suggested follow-up questions. "
    "Format them as:\n"
    "\n"
    "RECOMMENDATIONS:\n"
    "- [question text] | [brief reason why this is relevant]\n"
    "- [question text] | [brief reason why this is relevant]\n"
    "- [question text] | [brief reason why this is relevant]\n"
    "\n"
    "Generate follow-up questions that dig DEEPER into the "
    "specific content you just summarised or answered about. "
    "Good follow-ups ask about a specific claim, decision, "
    "speaker contribution, or policy mentioned in the chunks. "
    "Bad follow-ups are generic (e.g. 'What else was discussed?'). "
    "If your answer was a summary of a record, suggest questions "
    "that drill into the most substantive points — specific "
    "debates, named policies, contested votes, or individual "
    "contributions. If no source chunks are available, suggest "
    "questions about general parliamentary proceedings."
)


class GroundedAnsweringChain:
    """Generates cited answers from retrieved chunks via LangChain ChatBedrock.

    Uses ChatBedrock.ainvoke() for async-native model invocation — no thread
    executor needed. Prompt construction uses LangChain messages (SystemMessage
    + HumanMessage) instead of manual string concatenation.

    Preserves the same public interface as GroundedAnswerer:
        - async answer(question, chunks, conversation_history, corpus_hints) -> AnswerResponse

    Usage::

        chain = GroundedAnsweringChain(chat_model, settings)
        response = await chain.answer(
            question="What was discussed about the budget?",
            chunks=retrieved_chunks,
        )
    """

    def __init__(
        self,
        chat_model: ChatBedrock,
        settings: Settings,
        relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
        max_context_chunks: int = _MAX_CONTEXT_CHUNKS,
    ) -> None:
        self._chat_model = chat_model
        self._settings = settings
        self._relevance_threshold = relevance_threshold
        self._max_context_chunks = max_context_chunks
        self._builder = RecommendationBuilder()

    async def answer(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        conversation_history: list[tuple[str, str]] | None = None,
        corpus_hints: CorpusHints | None = None,
    ) -> AnswerResponse:
        """Generate a grounded answer with citations from retrieved chunks.

        Steps:
            1. Filter chunks by relevance threshold.
            2. Build prompt messages (SystemMessage + HumanMessage).
            3. Call self._chat_model.ainvoke(messages) — async-native.
            4. Parse citation markers [chunk_id] from response.
            5. Validate each citation references an actual source chunk.
            6. Parse recommendations from response.
            7. Return via _finalise().

        Every return path goes through `_finalise`, which is the single place
        recommendations and related records are assembled, so no path can
        return an empty recommendation set.

        Args:
            question: The natural language question to answer.
            chunks: Retrieved chunks from the HybridRetriever.
            conversation_history: Optional list of (role, content) tuples for
                multi-turn context.
            corpus_hints: Optional corpus-wide entity and speaker names, supplied
                by RAG_Router when retrieval came back empty, used as a
                recommendation source of last resort before the static set.

        Returns:
            An AnswerResponse containing the answer, citations, source chunks,
            recommendations, and latency in milliseconds.
        """
        start_time = time.perf_counter()

        logger.info(
            "rag.answerer.start",
            question_preview=question[:80],
            chunk_count=len(chunks),
        )

        # Questions the user has already asked — excluded from any builder
        # top-up so a suggestion never restates a prior turn (Requirement 3.6)
        history_questions = [
            content for role, content in (conversation_history or []) if role == "user"
        ]

        # Step 1: Filter chunks by relevance threshold
        relevant_chunks = [c for c in chunks if c.relevance_score >= self._relevance_threshold]

        # Limit to max context chunks (already sorted by relevance from retriever)
        context_chunks = relevant_chunks[: self._max_context_chunks]
        grounded = bool(context_chunks)

        if not grounded:
            logger.info(
                "rag.answerer.no_relevant_chunks",
                question_preview=question[:80],
                threshold=self._relevance_threshold,
            )

        # Step 2: Build prompt messages
        messages = self._build_messages(
            question, context_chunks, conversation_history, grounded=grounded
        )

        # Step 3: Call ChatBedrock.ainvoke() — async-native, no thread executor
        try:
            ai_message = await self._chat_model.ainvoke(messages)
            raw_answer = ai_message.content
        except Exception:
            logger.error(
                "rag.answerer.model_invocation_failed",
                question_preview=question[:80],
                exc_info=True,
            )
            return self._finalise(
                answer=GENERATION_FAILURE_TEXT,
                citations=[],
                context_chunks=context_chunks,
                parsed=[],
                hint_chunks=context_chunks or chunks,
                corpus_hints=corpus_hints,
                history_questions=history_questions,
                grounded=grounded,
                start_time=start_time,
            )

        # Step 4: Parse citation markers from response
        # Split answer text from recommendations section
        answer_text, parsed = split_recommendations(raw_answer)

        # Step 5: Resolve citations
        if grounded:
            chunk_map = {c.chunk_id: c for c in context_chunks}
            citations = validate_citations(parse_citations(answer_text), chunk_map)
        else:
            # Zero-context path: nothing is citable, so citations are empty by
            # construction and any marker the model emitted is stripped
            answer_text = strip_citation_markers(answer_text)
            citations = []

        # Step 6: Return structured response
        logger.info(
            "rag.answerer.complete",
            question_preview=question[:80],
            answer_length=len(answer_text),
            citation_count=len(citations),
            parsed_recommendation_count=len(parsed),
            grounded=grounded,
        )

        return self._finalise(
            answer=answer_text,
            citations=citations,
            context_chunks=context_chunks,
            parsed=parsed,
            hint_chunks=context_chunks or chunks,
            corpus_hints=corpus_hints,
            history_questions=history_questions,
            grounded=grounded,
            start_time=start_time,
        )

    # ------------------------------------------------------------------
    # Recommendation assembly
    # ------------------------------------------------------------------

    def _finalise(
        self,
        *,
        answer: str,
        citations: list[Citation],
        context_chunks: list[RetrievedChunk],
        parsed: list[RecommendationItem],
        hint_chunks: list[RetrievedChunk],
        corpus_hints: CorpusHints | None,
        history_questions: list[str],
        grounded: bool,
        start_time: float,
    ) -> AnswerResponse:
        """Delegate to the shared assembly point in recommendations.py."""
        return finalise_answer(
            builder=self._builder,
            answer=answer,
            citations=citations,
            context_chunks=context_chunks,
            parsed=parsed,
            hint_chunks=hint_chunks,
            corpus_hints=corpus_hints,
            history_questions=history_questions,
            grounded=grounded,
            start_time=start_time,
        )

    # ------------------------------------------------------------------
    # Prompt construction (LangChain messages)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        question: str,
        chunks: list[RetrievedChunk],
        conversation_history: list[tuple[str, str]] | None = None,
        *,
        grounded: bool = True,
    ) -> list[SystemMessage | HumanMessage]:
        """Build LangChain messages for ChatBedrock.ainvoke().

        Returns a list of [SystemMessage, HumanMessage] where the human message
        contains chunk context, conversation history, and the question.

        Args:
            question: The natural language question to answer.
            chunks: Chunks to present as citable evidence. Empty when
                `grounded` is false.
            conversation_history: Optional (role, content) tuples for multi-turn
                context.
            grounded: Whether citable evidence is available.
        """
        # Build the human message content (same structure as _build_user_prompt)
        lines: list[str] = []

        # Include conversation history for multi-turn context
        if conversation_history:
            lines.append("## Conversation History\n")
            for role, content in conversation_history[-10:]:  # Last 10 exchanges
                label = "User" if role == "user" else "Assistant"
                # Truncate long prior answers to save context window
                truncated = content[:500] + "..." if len(content) > 500 else content
                lines.append(f"**{label}:** {truncated}")
                lines.append("")
            lines.append("---\n")

        lines.append("## Source Chunks\n")

        if not grounded:
            lines.append(_NO_SOURCE_CHUNKS_TEXT)
            lines.append("")
        else:
            for chunk in chunks:
                speaker_label = chunk.speaker or "Unknown Speaker"
                start_s = f"{chunk.start_s:.1f}s" if chunk.start_s is not None else "N/A"
                end_s = f"{chunk.end_s:.1f}s" if chunk.end_s is not None else "N/A"

                lines.append(f"### [Chunk ID: {chunk.chunk_id}]")
                lines.append(f"- Speaker: {speaker_label}")
                lines.append(f"- Timestamps: {start_s} – {end_s}")
                lines.append(f"- Text: {chunk.text}")
                lines.append("")

        lines.append("## Question\n")
        lines.append(question)
        lines.append("")
        lines.append(
            "Please answer the question using ONLY the source chunks above. "
            "Cite each claim using [chunk_id] notation. "
            "Then provide 3 recommended follow-up questions in the "
            "RECOMMENDATIONS section."
        )

        human_content = "\n".join(lines)

        return [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=human_content),
        ]
