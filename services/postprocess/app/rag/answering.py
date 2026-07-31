"""Grounded answering engine — generates cited answers from retrieved chunks.

Accepts retrieved chunks from the HybridRetriever, builds a citation-aware
prompt, calls Claude via the existing BedrockClient, parses citation markers,
validates chunk references, and returns a structured response with answer,
citations, source chunks, and latency.

Follows the same async pattern as retrieval.py: boto3 calls run in a thread
executor, structured logging via structlog.

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

import structlog

from app.config import Settings
from app.llm.bedrock import BedrockClient
from app.rag.retrieval import RetrievedChunk

logger = structlog.get_logger("rag.answering")

# Default relevance threshold — chunks below this score are excluded
_DEFAULT_RELEVANCE_THRESHOLD = 0.01

# Default model for answering (can be overridden via settings)
_DEFAULT_ANSWERING_MODEL = "anthropic.claude-3-sonnet-20240229-v1:0"

# Maximum number of chunks to include in the prompt context
_MAX_CONTEXT_CHUNKS = 10

# System prompt instructing Claude to produce cited answers
_SYSTEM_PROMPT = """You are a parliamentary research assistant. Your task is to answer questions using ONLY the provided transcript chunks. Follow these rules strictly:

1. Answer the question using ONLY information from the provided chunks.
2. Cite your sources using [chunk_id] notation (e.g., [42]) immediately after the relevant statement.
3. If multiple chunks support a statement, cite all of them (e.g., [42][15]).
4. Do NOT invent or infer information not present in the chunks.
5. If the chunks do not contain enough information to answer the question, say so explicitly.
6. Keep your answer concise and factual.
7. When quoting speakers, attribute the quote to the speaker mentioned in the chunk metadata."""


@dataclass(frozen=True)
class Citation:
    """A reference linking an answer statement to a source chunk.

    Attributes:
        transcript_id: The transcript containing the cited chunk.
        chunk_id: The unique identifier of the cited chunk.
        speaker: The speaker attributed to the cited chunk (if available).
        start_s: Start timestamp of the cited chunk in seconds.
        end_s: End timestamp of the cited chunk in seconds.
        excerpt: A short excerpt from the cited chunk text.
    """

    transcript_id: int
    chunk_id: int
    speaker: str | None
    start_s: float | None
    end_s: float | None
    excerpt: str


@dataclass
class AnswerResponse:
    """Structured response from the grounded answering engine.

    Attributes:
        answer: The generated answer text with inline citation markers.
        citations: List of validated Citation objects referenced in the answer.
        source_chunks: The retrieved chunks used as context for generation.
        latency_ms: Total time from request to response in milliseconds.
    """

    answer: str
    citations: list[Citation] = field(default_factory=list)
    source_chunks: list[RetrievedChunk] = field(default_factory=list)
    latency_ms: float = 0.0


class GroundedAnswerer:
    """Generates cited answers from retrieved chunks via Claude.

    Accepts a list of RetrievedChunk objects, filters by relevance threshold,
    builds a citation-aware prompt, calls Claude via Bedrock, parses and
    validates citations, and returns a structured AnswerResponse.

    Usage::

        answerer = GroundedAnswerer(bedrock_client, settings)
        response = await answerer.answer(
            question="What was discussed about the budget?",
            chunks=retrieved_chunks,
        )
    """

    def __init__(
        self,
        bedrock_client: BedrockClient,
        settings: Settings,
        relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
        max_context_chunks: int = _MAX_CONTEXT_CHUNKS,
    ) -> None:
        self._bedrock_client = bedrock_client
        self._settings = settings
        self._relevance_threshold = relevance_threshold
        self._max_context_chunks = max_context_chunks

    async def answer(
        self,
        question: str,
        chunks: list[RetrievedChunk],
    ) -> AnswerResponse:
        """Generate a grounded answer with citations from retrieved chunks.

        Steps:
            1. Filter chunks by relevance threshold.
            2. If no chunks meet threshold, return "no supporting evidence".
            3. Build prompt with chunk context and citation instructions.
            4. Call Claude via BedrockClient (in thread executor).
            5. Parse citation markers [chunk_id] from response.
            6. Validate each citation references an actual source chunk.
            7. Return AnswerResponse with answer, citations, chunks, latency.

        Args:
            question: The natural language question to answer.
            chunks: Retrieved chunks from the HybridRetriever.

        Returns:
            An AnswerResponse containing the answer, citations, source
            chunks, and latency in milliseconds.
        """
        start_time = time.perf_counter()

        logger.info(
            "rag.answering.start",
            question_preview=question[:80],
            chunk_count=len(chunks),
        )

        # Step 1: Filter chunks by relevance threshold
        relevant_chunks = [
            c for c in chunks
            if c.relevance_score >= self._relevance_threshold
        ]

        # Step 2: If no chunks meet threshold, return early
        if not relevant_chunks:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "rag.answering.no_relevant_chunks",
                question_preview=question[:80],
                threshold=self._relevance_threshold,
            )
            return AnswerResponse(
                answer="No supporting evidence found in the parliamentary record for this question.",
                citations=[],
                source_chunks=[],
                latency_ms=latency_ms,
            )

        # Limit to max context chunks (already sorted by relevance from retriever)
        context_chunks = relevant_chunks[: self._max_context_chunks]

        # Step 3: Build prompt
        user_content = self._build_user_prompt(question, context_chunks)

        # Step 4: Call Claude via Bedrock
        try:
            raw_answer = await self._invoke_claude(user_content)
        except Exception:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "rag.answering.claude_invocation_failed",
                question_preview=question[:80],
                exc_info=True,
            )
            return AnswerResponse(
                answer="Unable to generate an answer at this time. Please try again later.",
                citations=[],
                source_chunks=context_chunks,
                latency_ms=latency_ms,
            )

        # Step 5: Parse citation markers from response
        cited_chunk_ids = self._parse_citations(raw_answer)

        # Step 6: Validate citations against source chunks
        chunk_map = {c.chunk_id: c for c in context_chunks}
        citations = self._validate_citations(cited_chunk_ids, chunk_map)

        # Step 7: Return structured response
        latency_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "rag.answering.complete",
            question_preview=question[:80],
            answer_length=len(raw_answer),
            citation_count=len(citations),
            latency_ms=round(latency_ms, 1),
        )

        return AnswerResponse(
            answer=raw_answer,
            citations=citations,
            source_chunks=context_chunks,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_prompt(
        question: str,
        chunks: list[RetrievedChunk],
    ) -> str:
        """Build the user message with chunk context for Claude.

        Lists each chunk with its ID, speaker, timestamps, and text,
        then poses the question.
        """
        lines: list[str] = []
        lines.append("## Source Chunks\n")

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
            "Cite each claim using [chunk_id] notation."
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Claude invocation
    # ------------------------------------------------------------------

    async def _invoke_claude(self, user_content: str) -> str:
        """Call Claude via the existing BedrockClient in a thread executor.

        Runs the synchronous BedrockClient.invoke() in asyncio's default
        thread executor to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._bedrock_client.invoke,
            _SYSTEM_PROMPT,
            user_content,
        )

    # ------------------------------------------------------------------
    # Citation parsing and validation
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_citations(answer_text: str) -> list[int]:
        """Extract chunk_id integers from [chunk_id] citation markers.

        Returns a deduplicated list of chunk IDs in order of first appearance.
        """
        # Match [<integer>] patterns
        pattern = re.compile(r"\[(\d+)\]")
        matches = pattern.findall(answer_text)

        seen: set[int] = set()
        chunk_ids: list[int] = []

        for match in matches:
            chunk_id = int(match)
            if chunk_id not in seen:
                seen.add(chunk_id)
                chunk_ids.append(chunk_id)

        return chunk_ids

    @staticmethod
    def _validate_citations(
        cited_chunk_ids: list[int],
        chunk_map: dict[int, RetrievedChunk],
    ) -> list[Citation]:
        """Validate cited chunk IDs against the source chunk map.

        Only citations referencing an actual chunk in the context are
        included. Invalid references are logged and dropped.
        """
        citations: list[Citation] = []

        for chunk_id in cited_chunk_ids:
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                logger.warning(
                    "rag.answering.invalid_citation",
                    chunk_id=chunk_id,
                    available_ids=list(chunk_map.keys()),
                )
                continue

            # Build excerpt (first 150 characters of chunk text)
            excerpt = chunk.text[:150]
            if len(chunk.text) > 150:
                excerpt += "..."

            citations.append(
                Citation(
                    transcript_id=chunk.transcript_id,
                    chunk_id=chunk.chunk_id,
                    speaker=chunk.speaker,
                    start_s=chunk.start_s,
                    end_s=chunk.end_s,
                    excerpt=excerpt,
                )
            )

        return citations
