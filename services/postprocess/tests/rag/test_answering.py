"""Unit tests for the GroundedAnswerer class.

Tests core logic: relevance filtering, prompt building, citation parsing,
and citation validation — all without mocking external services for the
pure functions, and with minimal mocking for the async answer() flow.

Also holds the property tests for the recommendation invariant that must hold
on every answer path (Properties 1 and 2 of the design). The Bedrock client is
always a stub: a MagicMock whose `invoke(system, user)` records its call count
and either returns a generated body or raises. No test reaches AWS.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from app.rag.answering import (
    _DEFAULT_RELEVANCE_THRESHOLD,
    AnswerResponse,
    Citation,
    GroundedAnswerer,
    RecommendationItem,
)
from app.rag.recommendations import TARGET_SUGGESTION_COUNT, normalise_question
from app.rag.retrieval import RetrievedChunk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_chunk(
    chunk_id: int = 1,
    text: str = "The budget was discussed at length.",
    relevance_score: float = 0.05,
    transcript_id: int = 100,
    speaker: str | None = "Hon. Smith",
    start_s: float | None = 10.0,
    end_s: float | None = 25.0,
) -> RetrievedChunk:
    """Create a RetrievedChunk for testing."""
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        relevance_score=relevance_score,
        transcript_id=transcript_id,
        speaker=speaker,
        start_s=start_s,
        end_s=end_s,
        matched_entities=["Budget"],
    )


def _make_answerer(
    relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
    max_context_chunks: int = 10,
) -> GroundedAnswerer:
    """Create a GroundedAnswerer with a mock BedrockClient."""
    mock_bedrock = MagicMock()
    mock_bedrock.is_configured = True
    mock_bedrock.model_id = "anthropic.claude-3-sonnet-20240229-v1:0"

    mock_settings = MagicMock()
    mock_settings.aws_region = "us-east-1"

    return GroundedAnswerer(
        bedrock_client=mock_bedrock,
        settings=mock_settings,
        relevance_threshold=relevance_threshold,
        max_context_chunks=max_context_chunks,
    )


# ---------------------------------------------------------------------------
# Tests: Relevance threshold filtering
# ---------------------------------------------------------------------------


class TestRelevanceFiltering:
    """Tests for relevance threshold filtering behavior."""

    def test_no_chunks_still_invokes_model(self) -> None:
        """Empty chunk list still makes the single model call (Requirements 8.1, 2.6)."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.return_value = (
            "I can search debates and answer questions about the parliamentary record."
        )

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("Hello, what can you do?", chunks=[])
        )

        assert answerer._bedrock_client.invoke.call_count == 1
        assert "search debates" in result.answer
        assert result.citations == []
        assert result.source_chunks == []
        assert result.latency_ms > 0

    def test_no_chunks_prompt_carries_no_source_chunks_marker(self) -> None:
        """The zero-context prompt announces that no chunks are available."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.return_value = "Nothing in the supplied record."

        asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=[])
        )

        user_content = answerer._bedrock_client.invoke.call_args[0][1]
        assert "(NO SOURCE CHUNKS AVAILABLE FOR THIS QUESTION)" in user_content
        assert "[Chunk ID:" not in user_content

    def test_chunks_below_threshold_produce_no_citations(self) -> None:
        """Sub-threshold chunks are not evidence: one call, no citations, no sources."""
        answerer = _make_answerer(relevance_threshold=0.5)
        answerer._bedrock_client.invoke.return_value = (
            "The record supplied contains no supporting evidence for this question."
        )
        chunks = [
            _make_chunk(chunk_id=1, relevance_score=0.01),
            _make_chunk(chunk_id=2, relevance_score=0.1),
            _make_chunk(chunk_id=3, relevance_score=0.49),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )
        assert answerer._bedrock_client.invoke.call_count == 1
        assert "no supporting evidence" in result.answer
        assert result.citations == []
        assert result.source_chunks == []
        assert result.related_records == []

    def test_zero_context_path_strips_citation_markers(self) -> None:
        """Markers emitted without evidence are stripped, not left dead (Req 8.2)."""
        answerer = _make_answerer(relevance_threshold=0.5)
        answerer._bedrock_client.invoke.return_value = (
            "The Minister presented the review [42]. Spending rose [15][7]."
        )
        chunks = [_make_chunk(chunk_id=42, relevance_score=0.01)]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )

        assert "[42]" not in result.answer
        assert "[15]" not in result.answer
        assert result.answer == "The Minister presented the review. Spending rose."
        assert result.citations == []

    def test_zero_context_generation_failure_returns_failure_text(self) -> None:
        """A raising stub on the zero-context path yields the failure text (Req 2.3)."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.side_effect = RuntimeError("Bedrock unavailable")

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("Hello there", chunks=[])
        )

        assert "Unable to generate an answer" in result.answer
        assert result.citations == []
        assert result.source_chunks == []
        assert result.related_records == []

    def test_some_chunks_above_threshold_passes_only_relevant(self) -> None:
        """Only chunks above threshold are used as context."""
        answerer = _make_answerer(relevance_threshold=0.05)
        # Mock invoke to return a simple answer
        answerer._bedrock_client.invoke.return_value = (
            "The budget was discussed. [1]"
        )

        chunks = [
            _make_chunk(chunk_id=1, relevance_score=0.1),  # above
            _make_chunk(chunk_id=2, relevance_score=0.001),  # below
            _make_chunk(chunk_id=3, relevance_score=0.06),  # above
        ]
        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )
        # source_chunks should only include chunks above threshold
        assert len(result.source_chunks) == 2
        source_ids = {c.chunk_id for c in result.source_chunks}
        assert source_ids == {1, 3}


# ---------------------------------------------------------------------------
# Tests: Prompt building
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    """Tests for the prompt construction logic."""

    def test_prompt_includes_chunk_ids(self) -> None:
        """Prompt includes chunk IDs in the context section."""
        chunks = [
            _make_chunk(chunk_id=42, text="Budget allocation increased."),
            _make_chunk(chunk_id=15, text="The Speaker called for order."),
        ]
        prompt = GroundedAnswerer._build_user_prompt("What about the budget?", chunks)

        assert "[Chunk ID: 42]" in prompt
        assert "[Chunk ID: 15]" in prompt

    def test_prompt_includes_speaker_labels(self) -> None:
        """Prompt includes speaker metadata for each chunk."""
        chunks = [
            _make_chunk(chunk_id=1, speaker="Hon. Smith"),
            _make_chunk(chunk_id=2, speaker=None),
        ]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "Speaker: Hon. Smith" in prompt
        assert "Speaker: Unknown Speaker" in prompt

    def test_prompt_includes_timestamps(self) -> None:
        """Prompt includes start/end timestamps for each chunk."""
        chunks = [
            _make_chunk(chunk_id=1, start_s=10.5, end_s=25.3),
            _make_chunk(chunk_id=2, start_s=None, end_s=None),
        ]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "10.5s" in prompt
        assert "25.3s" in prompt
        assert "N/A" in prompt

    def test_prompt_includes_chunk_text(self) -> None:
        """Prompt includes the full text of each chunk."""
        chunks = [
            _make_chunk(chunk_id=1, text="The fiscal policy was debated."),
        ]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "The fiscal policy was debated." in prompt

    def test_prompt_includes_question(self) -> None:
        """Prompt includes the user's question."""
        chunks = [_make_chunk(chunk_id=1)]
        prompt = GroundedAnswerer._build_user_prompt(
            "What was the budget allocation?", chunks
        )

        assert "What was the budget allocation?" in prompt

    def test_prompt_ungrounded_replaces_chunk_list_with_marker(self) -> None:
        """grounded=False emits the no-source-chunks marker in the context block."""
        prompt = GroundedAnswerer._build_user_prompt("Hello?", [], grounded=False)

        assert "## Source Chunks\n\n(NO SOURCE CHUNKS AVAILABLE FOR THIS QUESTION)" in prompt
        assert "### [Chunk ID:" not in prompt
        assert "Hello?" in prompt

    def test_prompt_includes_citation_instruction(self) -> None:
        """Prompt instructs the model to cite using [chunk_id] notation."""
        chunks = [_make_chunk(chunk_id=1)]
        prompt = GroundedAnswerer._build_user_prompt("Question?", chunks)

        assert "[chunk_id]" in prompt


# ---------------------------------------------------------------------------
# Tests: Citation parsing
# ---------------------------------------------------------------------------


class TestCitationParsing:
    """Tests for extracting citation markers from answer text."""

    def test_single_citation(self) -> None:
        """Parses a single citation marker."""
        ids = GroundedAnswerer._parse_citations("The budget increased [42].")
        assert ids == [42]

    def test_multiple_citations(self) -> None:
        """Parses multiple citation markers."""
        ids = GroundedAnswerer._parse_citations(
            "Budget increased [42]. Spending rose [15]. See also [42][7]."
        )
        # Deduplicated, first-appearance order
        assert ids == [42, 15, 7]

    def test_no_citations(self) -> None:
        """Returns empty list when no citations are present."""
        ids = GroundedAnswerer._parse_citations("No citations here.")
        assert ids == []

    def test_non_numeric_brackets_ignored(self) -> None:
        """Non-numeric bracket content is ignored."""
        ids = GroundedAnswerer._parse_citations(
            "See [source] and [chunk_id] but also [123]."
        )
        assert ids == [123]

    def test_adjacent_citations(self) -> None:
        """Adjacent citations are parsed correctly."""
        ids = GroundedAnswerer._parse_citations("Statement [1][2][3].")
        assert ids == [1, 2, 3]

    def test_large_chunk_ids(self) -> None:
        """Large chunk IDs are parsed correctly."""
        ids = GroundedAnswerer._parse_citations("Evidence [999999].")
        assert ids == [999999]


# ---------------------------------------------------------------------------
# Tests: Citation validation
# ---------------------------------------------------------------------------


class TestCitationValidation:
    """Tests for validating citations against source chunks."""

    def test_valid_citation_produces_citation_object(self) -> None:
        """Valid chunk_id produces a Citation with correct fields."""
        chunk = _make_chunk(
            chunk_id=42,
            transcript_id=100,
            speaker="Hon. Smith",
            start_s=10.0,
            end_s=25.0,
            text="The budget was discussed thoroughly in committee.",
        )
        chunk_map = {42: chunk}

        citations = GroundedAnswerer._validate_citations([42], chunk_map)

        assert len(citations) == 1
        c = citations[0]
        assert c.chunk_id == 42
        assert c.transcript_id == 100
        assert c.speaker == "Hon. Smith"
        assert c.start_s == 10.0
        assert c.end_s == 25.0
        assert "The budget was discussed" in c.excerpt

    def test_invalid_citation_is_dropped(self) -> None:
        """Citation referencing non-existent chunk is dropped."""
        chunk = _make_chunk(chunk_id=42)
        chunk_map = {42: chunk}

        citations = GroundedAnswerer._validate_citations([42, 999], chunk_map)

        assert len(citations) == 1
        assert citations[0].chunk_id == 42

    def test_all_invalid_returns_empty(self) -> None:
        """All invalid citations return empty list."""
        chunk_map = {42: _make_chunk(chunk_id=42)}

        citations = GroundedAnswerer._validate_citations([100, 200], chunk_map)

        assert citations == []

    def test_excerpt_truncated_for_long_text(self) -> None:
        """Excerpt is truncated to 150 chars with ellipsis for long text."""
        long_text = "A" * 200
        chunk = _make_chunk(chunk_id=1, text=long_text)
        chunk_map = {1: chunk}

        citations = GroundedAnswerer._validate_citations([1], chunk_map)

        assert len(citations[0].excerpt) == 153  # 150 + "..."
        assert citations[0].excerpt.endswith("...")

    def test_excerpt_not_truncated_for_short_text(self) -> None:
        """Short text is not truncated."""
        short_text = "Short."
        chunk = _make_chunk(chunk_id=1, text=short_text)
        chunk_map = {1: chunk}

        citations = GroundedAnswerer._validate_citations([1], chunk_map)

        assert citations[0].excerpt == "Short."


# ---------------------------------------------------------------------------
# Tests: Full answer() flow
# ---------------------------------------------------------------------------


class TestAnswerFlow:
    """Integration tests for the full answer() flow with mocked Bedrock."""

    def test_successful_answer_with_citations(self) -> None:
        """Full flow: relevant chunks → Claude response → parsed citations."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.return_value = (
            "The budget allocation increased by 15% [1]. "
            "The education sector received the most [2]."
        )

        chunks = [
            _make_chunk(chunk_id=1, relevance_score=0.1, text="Budget increased."),
            _make_chunk(chunk_id=2, relevance_score=0.08, text="Education funding."),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )

        assert "budget allocation increased" in result.answer
        assert len(result.citations) == 2
        assert result.citations[0].chunk_id == 1
        assert result.citations[1].chunk_id == 2
        assert len(result.source_chunks) == 2
        assert result.latency_ms > 0

    def test_claude_failure_returns_error_message(self) -> None:
        """Claude invocation failure returns a user-friendly error."""
        answerer = _make_answerer()
        answerer._bedrock_client.invoke.side_effect = RuntimeError("Bedrock unavailable")

        chunks = [_make_chunk(chunk_id=1, relevance_score=0.1)]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("What about the budget?", chunks=chunks)
        )

        assert "Unable to generate an answer" in result.answer
        assert result.citations == []
        assert len(result.source_chunks) == 1  # chunks still returned
        assert result.latency_ms > 0

    def test_max_context_chunks_limit(self) -> None:
        """Only top max_context_chunks chunks are used."""
        answerer = _make_answerer(max_context_chunks=2)
        answerer._bedrock_client.invoke.return_value = "Answer [1]."

        chunks = [
            _make_chunk(chunk_id=i, relevance_score=0.1 - i * 0.01)
            for i in range(1, 6)
        ]

        result = asyncio.get_event_loop().run_until_complete(
            answerer.answer("Question?", chunks=chunks)
        )

        # Only 2 chunks should be in source_chunks
        assert len(result.source_chunks) == 2

    def test_answer_response_dataclass_fields(self) -> None:
        """AnswerResponse has all required fields."""
        resp = AnswerResponse(
            answer="Test answer",
            citations=[
                Citation(
                    transcript_id=1,
                    chunk_id=42,
                    speaker="Hon. X",
                    start_s=0.0,
                    end_s=10.0,
                    excerpt="Test",
                )
            ],
            source_chunks=[_make_chunk(chunk_id=42)],
            latency_ms=123.4,
        )

        assert resp.answer == "Test answer"
        assert len(resp.citations) == 1
        assert resp.citations[0].chunk_id == 42
        assert len(resp.source_chunks) == 1
        assert resp.latency_ms == 123.4


# ---------------------------------------------------------------------------
# Property tests: Recommendation invariant across all answer paths
# ---------------------------------------------------------------------------

# Strategies


@st.composite
def recommendation_body(draw: st.DrawFn) -> str:
    """Generate a Claude-like response body with varying recommendation formats.

    Produces bodies that cover:
    - Valid RECOMMENDATIONS: blocks with 0-5 lines like "- What about X? | reason"
    - Missing RECOMMENDATIONS section entirely
    - Malformed sections (e.g., RECOMMENDATIONS without colon, wrong formatting)
    """
    answer_part = draw(
        st.sampled_from([
            "The budget was discussed.",
            "Hello, I can help with parliamentary research.",
            "The Minister presented the fiscal review.",
            "Several members contributed to the debate on education.",
            "No relevant information was found in the record.",
        ])
    )

    rec_style = draw(st.sampled_from(["valid", "missing", "malformed_no_colon", "malformed_lines"]))

    if rec_style == "missing":
        return answer_part

    if rec_style == "malformed_no_colon":
        # RECOMMENDATIONS without the colon — parser should not match
        return f"{answer_part}\n\nRECOMMENDATIONS\n- Something here | a reason"

    if rec_style == "malformed_lines":
        # Has the header but lines don't follow "- text | reason" format
        malformed = draw(
            st.sampled_from([
                f"{answer_part}\n\nRECOMMENDATIONS:\n",
                f"{answer_part}\n\nRECOMMENDATIONS:\nno dash here\nalso no dash",
                f"{answer_part}\n\nRECOMMENDATIONS:\n1. numbered not dashed | reason",
            ])
        )
        return malformed

    # valid: 0 to 5 properly formatted recommendation lines
    num_recs = draw(st.integers(min_value=0, max_value=5))
    topics = draw(
        st.lists(
            st.sampled_from([
                "What about the education budget?",
                "Who spoke on healthcare?",
                "When was the motion tabled?",
                "What did the Minister say about taxes?",
                "How many members voted?",
                "What was the final resolution on housing?",
                "Which committee reviewed the bill?",
            ]),
            min_size=num_recs,
            max_size=num_recs,
        )
    )
    reasons = draw(
        st.lists(
            st.sampled_from([
                "Related to the fiscal debate",
                "Speaker contributed to this topic",
                "Recent sitting covered this",
                "Connected to the current question",
                "Builds on the discussion above",
            ]),
            min_size=num_recs,
            max_size=num_recs,
        )
    )

    if num_recs == 0:
        return f"{answer_part}\n\nRECOMMENDATIONS:\n"

    lines = [f"- {topic} | {reason}" for topic, reason in zip(topics, reasons)]
    return f"{answer_part}\n\nRECOMMENDATIONS:\n" + "\n".join(lines)


@st.composite
def chunk_scenario(draw: st.DrawFn) -> list[RetrievedChunk]:
    """Generate chunk lists: empty, all sub-threshold, or relevant.

    Three cases:
    - Empty list []
    - All sub-threshold: all chunks have relevance_score < 0.01
    - Relevant: at least one chunk with relevance_score >= 0.01, distinct transcript_ids
    """
    case = draw(st.sampled_from(["empty", "sub_threshold", "relevant"]))

    if case == "empty":
        return []

    if case == "sub_threshold":
        size = draw(st.integers(min_value=1, max_value=4))
        return [
            _make_chunk(
                chunk_id=i + 1,
                relevance_score=draw(
                    st.floats(min_value=0.0001, max_value=0.009, allow_nan=False)
                ),
                transcript_id=100 + i,
                speaker=draw(st.sampled_from(["Hon. Smith", "Mr Speaker", None])),
            )
            for i in range(size)
        ]

    # relevant: at least one chunk with score >= 0.01
    size = draw(st.integers(min_value=1, max_value=5))
    chunks = []
    for i in range(size):
        score = draw(
            st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
        )
        chunks.append(
            _make_chunk(
                chunk_id=i + 1,
                relevance_score=score,
                transcript_id=200 + i,  # distinct transcript_ids
                speaker=draw(st.sampled_from(["Hon. Mensah", "Minister for Finance", None])),
            )
        )
    return chunks


# Property 1


@given(body=recommendation_body(), chunks=chunk_scenario(), raises=st.booleans())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property1_every_answer_carries_exactly_three_suggestions(
    body: str,
    chunks: list[RetrievedChunk],
    raises: bool,
) -> None:
    """Every answer carries exactly TARGET_SUGGESTION_COUNT well-formed suggestions.

    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 3.2, 3.5, 4.9

    For ANY stub response body (0-5 recommendation lines, missing/malformed
    RECOMMENDATIONS: blocks) and ANY chunk list (empty, sub-threshold, relevant),
    the AnswerResponse carries exactly TARGET_SUGGESTION_COUNT (3) well-formed
    suggestions. Each suggestion has non-empty .text and .reason. Suggestions are
    distinct under normalise_question. When grounded: related_records is non-empty.
    When not grounded: related_records is empty.
    """
    answerer = _make_answerer()

    if raises:
        answerer._bedrock_client.invoke.side_effect = RuntimeError("Bedrock unavailable")
    else:
        answerer._bedrock_client.invoke.return_value = body

    result: AnswerResponse = asyncio.get_event_loop().run_until_complete(
        answerer.answer("What was discussed about the budget?", chunks=chunks)
    )

    # Exactly TARGET_SUGGESTION_COUNT suggestions
    assert len(result.recommendations) == TARGET_SUGGESTION_COUNT, (
        f"Expected {TARGET_SUGGESTION_COUNT} suggestions, got {len(result.recommendations)}"
    )

    # Each suggestion has non-empty text and reason
    for rec in result.recommendations:
        assert rec.text.strip(), f"Empty suggestion text: {rec}"
        assert rec.reason.strip(), f"Empty suggestion reason: {rec}"

    # Suggestions are distinct under normalise_question
    keys = [normalise_question(rec.text) for rec in result.recommendations]
    assert len(set(keys)) == len(keys), f"Duplicate suggestions: {keys}"

    # Grounded vs ungrounded assertions
    grounded = any(c.relevance_score >= _DEFAULT_RELEVANCE_THRESHOLD for c in chunks)

    if grounded:
        # related_records should be non-empty when distinct transcript_ids exist
        distinct_transcripts = {
            c.transcript_id
            for c in chunks
            if c.relevance_score >= _DEFAULT_RELEVANCE_THRESHOLD
        }
        if distinct_transcripts:
            assert len(result.related_records) > 0, (
                "Expected non-empty related_records when grounded with distinct transcript_ids"
            )
    else:
        # Requirement 4.9: no related records when not grounded
        assert result.related_records == [], (
            f"Expected empty related_records when not grounded, got {result.related_records}"
        )


# Property 2


@given(
    body=recommendation_body(),
    chunks=chunk_scenario(),
    history_qs=st.lists(st.text(min_size=3, max_size=50), min_size=1, max_size=5),
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property2_suggestions_never_restate_prior_user_question(
    body: str,
    chunks: list[RetrievedChunk],
    history_qs: list[str],
) -> None:
    """Suggestions never restate a prior user question.

    Validates: Requirements 3.6

    For ANY history containing user questions, none of the returned
    recommendations has text that normalises to any history question
    under normalise_question.
    """
    answerer = _make_answerer()
    answerer._bedrock_client.invoke.return_value = body

    conversation_history = [("user", q) for q in history_qs]

    result: AnswerResponse = asyncio.get_event_loop().run_until_complete(
        answerer.answer(
            "What was discussed about the budget?",
            chunks=chunks,
            conversation_history=conversation_history,
        )
    )

    history_keys = {normalise_question(q) for q in history_qs}

    for rec in result.recommendations:
        rec_key = normalise_question(rec.text)
        assert rec_key not in history_keys, (
            f"Suggestion {rec.text!r} (normalised: {rec_key!r}) restates a prior "
            f"user question from history: {history_qs}"
        )
