"""Tests for app/rag/agent.py — HansardChatAgent and its search_hansard tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.rag.agent import (
    GENERATION_FAILURE_TEXT,
    HansardChatAgent,
    _format_passages,
    _make_search_tool,
)
from app.rag.parsing import message_text
from app.rag.retriever import RetrievedChunk


class FakeRetriever:
    """Records the queries it was called with and returns a configurable list."""

    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self.chunks = chunks or []
        self.calls: list[tuple[str, object, int]] = []

    async def retrieve(self, query, filters=None, limit=10):
        self.calls.append((query, filters, limit))
        return self.chunks


def _make_chunk(chunk_id: int, **overrides) -> RetrievedChunk:
    defaults = dict(
        chunk_id=chunk_id,
        text=f"Text for chunk {chunk_id}.",
        relevance_score=0.9,
        transcript_id=100 + chunk_id,
        speaker="Hon. Doe",
        start_s=10.0,
        end_s=20.0,
        matched_entities=[],
        record_title="Budget Debate",
        sitting_title="3rd Sitting",
        date="2024-01-15",
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


def _fake_agent_returning(text: str, side_effect=None):
    """Build a MagicMock standing in for the create_agent() return value."""
    agent = MagicMock()
    if side_effect is not None:
        agent.ainvoke = AsyncMock(side_effect=side_effect)
    else:
        agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content=text)]})
    return agent


# ---------------------------------------------------------------------------
# _format_passages
# ---------------------------------------------------------------------------


class TestFormatPassages:
    def test_includes_chunk_id_and_text(self):
        chunk = _make_chunk(42)
        rendered = _format_passages([chunk])
        assert "chunk_id: 42" in rendered
        assert "Text for chunk 42." in rendered
        # Positive form of the headers, so the absence assertions in
        # test_omits_absent_optional_metadata are not vacuous.
        assert "speaker: Hon. Doe" in rendered
        assert "sitting: 3rd Sitting" in rendered
        assert "record: Budget Debate" in rendered
        assert "date: 2024-01-15" in rendered
        assert "time: 10.0s-20.0s" in rendered

    def test_omits_absent_optional_metadata(self):
        chunk = _make_chunk(
            1,
            speaker=None,
            sitting_title=None,
            record_title=None,
            date=None,
            start_s=None,
            end_s=None,
        )
        rendered = _format_passages([chunk])
        assert "chunk_id: 1" in rendered
        # Assert on the header form `<field>:` that _format_passages emits, not a
        # bare field name. The output is wrapped in
        # <retrieved_parliamentary_record> tags, so bare substrings such as
        # "record" are always present and would make these assertions vacuous.
        assert "speaker:" not in rendered
        assert "sitting:" not in rendered
        assert "record:" not in rendered
        assert "date:" not in rendered
        assert "time:" not in rendered

    def test_wraps_all_passages_in_boundary_tags(self):
        """The boundary markers are part of the contract, not incidental framing.

        They pair with the system prompt's content-boundary rule to mark
        retrieved transcript text as data rather than instructions, so the
        untrusted text must sit wholly inside a single open/close pair.
        """
        rendered = _format_passages([_make_chunk(1), _make_chunk(2)])

        assert rendered.startswith("<retrieved_parliamentary_record>\n")
        assert rendered.endswith("\n</retrieved_parliamentary_record>")
        # One pair only — per-passage tags would leave untrusted text between a
        # closing and the next opening marker, outside the boundary.
        assert rendered.count("<retrieved_parliamentary_record>") == 1
        assert rendered.count("</retrieved_parliamentary_record>") == 1

        inner = rendered.removeprefix("<retrieved_parliamentary_record>\n").removesuffix(
            "\n</retrieved_parliamentary_record>"
        )
        assert "chunk_id: 1" in inner
        assert "Text for chunk 1." in inner
        assert "chunk_id: 2" in inner
        assert "Text for chunk 2." in inner


# ---------------------------------------------------------------------------
# message_text
# ---------------------------------------------------------------------------


class TestMessageText:
    def test_plain_string(self):
        message = AIMessage(content="Hello there.")
        assert message_text(message) == "Hello there."

    def test_list_of_text_blocks(self):
        message = AIMessage(
            content=[{"type": "text", "text": "Hello "}, {"type": "text", "text": "world."}]
        )
        assert message_text(message) == "Hello world."

    def test_list_of_bare_strings(self):
        message = AIMessage(content=["Hello ", "world."])
        assert message_text(message) == "Hello world."


# ---------------------------------------------------------------------------
# search_hansard tool (real tool, no patching of create_agent)
# ---------------------------------------------------------------------------


class TestSearchHansardTool:
    @pytest.mark.asyncio
    async def test_collects_chunks_and_returns_chunk_ids(self):
        chunk = _make_chunk(7)
        retriever = FakeRetriever([chunk])
        collector: list[RetrievedChunk] = []

        tool = _make_search_tool(retriever, None, collector)
        result = await tool.ainvoke({"query": "budget"})

        assert len(collector) == 1
        assert collector[0].chunk_id == 7
        assert "chunk_id: 7" in result
        assert retriever.calls == [("budget", None, 10)]

    @pytest.mark.asyncio
    async def test_repeated_overlapping_results_do_not_duplicate(self):
        chunk_a = _make_chunk(1)
        chunk_b = _make_chunk(2)
        retriever = FakeRetriever([chunk_a, chunk_b])
        collector: list[RetrievedChunk] = []

        tool = _make_search_tool(retriever, None, collector)
        await tool.ainvoke({"query": "budget"})
        await tool.ainvoke({"query": "budget again"})

        assert len(collector) == 2
        assert {c.chunk_id for c in collector} == {1, 2}

    @pytest.mark.asyncio
    async def test_no_match_returns_note(self):
        retriever = FakeRetriever([])
        collector: list[RetrievedChunk] = []

        tool = _make_search_tool(retriever, None, collector)
        result = await tool.ainvoke({"query": "nothing"})

        assert collector == []
        assert "No passages" in result


# ---------------------------------------------------------------------------
# HansardChatAgent.chat
# ---------------------------------------------------------------------------


class TestChat:
    @pytest.mark.asyncio
    async def test_recommendations_block_stripped_and_parsed(self, mock_settings):
        raw = "Hi there, happy to help!\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        retriever = FakeRetriever([])
        with patch("app.rag.agent.create_agent", return_value=_fake_agent_returning(raw)):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            response = await agent.chat("Hello!")

        assert "RECOMMENDATIONS" not in response.answer
        assert "Hi there, happy to help!" in response.answer
        assert len(response.recommendations) == 3

    @pytest.mark.asyncio
    async def test_conversational_path_has_no_citations_and_strips_markers(self, mock_settings):
        raw = (
            "Sure, I can help with that [42].\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        )
        retriever = FakeRetriever([])  # tool never runs -> collector stays empty
        with patch("app.rag.agent.create_agent", return_value=_fake_agent_returning(raw)):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            response = await agent.chat("Hello!")

        assert response.citations == []
        assert response.related_records == []
        assert "[42]" not in response.answer

    @pytest.mark.asyncio
    async def test_grounded_path_populates_citations_and_source_chunks(self, mock_settings):
        chunk = _make_chunk(99)
        raw = (
            "The Minister spoke on this [99].\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        )
        retriever = FakeRetriever([chunk])
        captured_tools: dict = {}

        def fake_create_agent(*args, **kwargs):
            captured_tools["tools"] = kwargs["tools"]
            fake_agent = MagicMock()

            async def fake_ainvoke(*a, **kw):
                # Simulate the model actually calling the search_hansard tool
                # during the agent loop, which populates chat()'s real collector.
                await captured_tools["tools"][0].ainvoke({"query": "budget"})
                return {"messages": [AIMessage(content=raw)]}

            fake_agent.ainvoke = AsyncMock(side_effect=fake_ainvoke)
            return fake_agent

        with patch("app.rag.agent.create_agent", side_effect=fake_create_agent):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            response = await agent.chat("What did the minister say?")

        assert len(response.citations) == 1
        assert response.citations[0].chunk_id == 99
        assert len(response.source_chunks) == 1

    @pytest.mark.asyncio
    async def test_invalid_citation_dropped(self, mock_settings):
        chunk = _make_chunk(5)
        raw = (
            "This references an unseen chunk [999].\n\n"
            "RECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        )
        retriever = FakeRetriever([chunk])
        captured_tools: dict = {}

        def fake_create_agent(*args, **kwargs):
            captured_tools["tools"] = kwargs["tools"]
            fake_agent = MagicMock()

            async def fake_ainvoke(*a, **kw):
                await captured_tools["tools"][0].ainvoke({"query": "budget"})
                return {"messages": [AIMessage(content=raw)]}

            fake_agent.ainvoke = AsyncMock(side_effect=fake_ainvoke)
            return fake_agent

        with patch("app.rag.agent.create_agent", side_effect=fake_create_agent):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            response = await agent.chat("What did the minister say?")

        assert response.citations == []

    @pytest.mark.asyncio
    async def test_error_path_returns_generation_failure_text(self, mock_settings):
        retriever = FakeRetriever([])
        with patch(
            "app.rag.agent.create_agent",
            return_value=_fake_agent_returning("", side_effect=Exception("boom")),
        ):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            response = await agent.chat("Hello!")

        assert response.answer == GENERATION_FAILURE_TEXT
        assert len(response.recommendations) == 3

    @pytest.mark.asyncio
    async def test_history_truncated_to_last_20(self, mock_settings):
        raw = "Ok.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        history = [("user", f"msg {i}") for i in range(30)]
        retriever = FakeRetriever([])

        captured_messages = {}

        async def fake_ainvoke(payload, **kwargs):
            captured_messages["messages"] = payload["messages"]
            return {"messages": [AIMessage(content=raw)]}

        with patch("app.rag.agent.create_agent") as create_agent_mock:
            fake_agent = MagicMock()
            fake_agent.ainvoke = AsyncMock(side_effect=fake_ainvoke)
            create_agent_mock.return_value = fake_agent

            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            await agent.chat("Hello!", conversation_history=history)

        # 20 history messages + the new user question = 21
        assert len(captured_messages["messages"]) == 21
        assert captured_messages["messages"][0]["content"] == "msg 10"

    @pytest.mark.asyncio
    async def test_registry_references_populated_when_recent_activity_tool_runs(
        self, mock_settings
    ):
        """When the model calls find_recent_activity, the result reaches AnswerResponse.

        Mirrors the grounded-path test for search_hansard: the fake agent invokes
        the second tool (find_recent_activity) during `ainvoke`, and the response
        should carry the registry reference the tool found — even though nothing
        was retrieved via search_hansard, so `grounded` stays False.
        """
        raw = (
            "One record was uploaded recently.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        )
        retriever = FakeRetriever([])
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(
                fetchall=lambda: [
                    (
                        "record",
                        5,
                        "Morning Session",
                        "3rd Sitting",
                        None,
                        "audio.mp3",
                        1,
                        5,
                    )
                ]
            )
        )
        context_manager = AsyncMock()
        context_manager.__aenter__ = AsyncMock(return_value=session)
        context_manager.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=context_manager)

        captured_tools: dict = {}

        def fake_create_agent(*args, **kwargs):
            captured_tools["tools"] = kwargs["tools"]
            fake_agent = MagicMock()

            async def fake_ainvoke(*a, **kw):
                # Simulate the model calling find_recent_activity (tools[1]).
                await captured_tools["tools"][1].ainvoke({"period": "recent", "scope": "uploads"})
                return {"messages": [AIMessage(content=raw)]}

            fake_agent.ainvoke = AsyncMock(side_effect=fake_ainvoke)
            return fake_agent

        with patch("app.rag.agent.create_agent", side_effect=fake_create_agent):
            agent = HansardChatAgent(
                chat_model=AsyncMock(),
                retriever=retriever,
                settings=mock_settings,
                session_factory=session_factory,
            )
            response = await agent.chat("What was uploaded recently?")

        assert len(response.registry_references) == 1
        ref = response.registry_references[0]
        assert ref.kind == "record"
        assert ref.id == 5
        assert ref.sitting_id == 1
        assert ref.record_id == 5
        # No transcript chunk was retrieved, so this stays ungrounded/uncited.
        assert response.citations == []

    @pytest.mark.asyncio
    async def test_no_recent_activity_tool_without_session_factory(self, mock_settings):
        """Without a session_factory, only search_hansard is wired — no crash."""
        raw = "Ok.\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2\n- Q3 | R3"
        retriever = FakeRetriever([])
        captured_tools: dict = {}

        def fake_create_agent(*args, **kwargs):
            captured_tools["tools"] = kwargs["tools"]
            fake_agent = MagicMock()
            fake_agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content=raw)]})
            return fake_agent

        with patch("app.rag.agent.create_agent", side_effect=fake_create_agent):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            response = await agent.chat("Hello!")

        assert len(captured_tools["tools"]) == 1
        assert response.registry_references == []

    @pytest.mark.asyncio
    async def test_recommendations_always_exactly_three_across_paths(self, mock_settings):
        conversational_raw = "Hi!\n\nRECOMMENDATIONS:\n- Q1 | R1"
        grounded_raw = "The record shows this [1].\n\nRECOMMENDATIONS:\n- Q1 | R1\n- Q2 | R2"
        chunk = _make_chunk(1)

        # Conversational: fewer than 3 parsed -> topped up to 3
        retriever = FakeRetriever([])
        with patch(
            "app.rag.agent.create_agent", return_value=_fake_agent_returning(conversational_raw)
        ):
            agent = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever, settings=mock_settings
            )
            conv_response = await agent.chat("Hello!")
        assert len(conv_response.recommendations) == 3

        # Grounded: fewer than 3 parsed -> topped up to 3
        retriever2 = FakeRetriever([chunk])
        captured_tools: dict = {}

        def fake_create_agent(*args, **kwargs):
            captured_tools["tools"] = kwargs["tools"]
            fake_agent = MagicMock()

            async def fake_ainvoke(*a, **kw):
                await captured_tools["tools"][0].ainvoke({"query": "budget"})
                return {"messages": [AIMessage(content=grounded_raw)]}

            fake_agent.ainvoke = AsyncMock(side_effect=fake_ainvoke)
            return fake_agent

        with patch("app.rag.agent.create_agent", side_effect=fake_create_agent):
            agent2 = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever2, settings=mock_settings
            )
            grounded_response = await agent2.chat("What happened?")
        assert len(grounded_response.recommendations) == 3

        # Error path
        retriever3 = FakeRetriever([])
        with patch(
            "app.rag.agent.create_agent",
            return_value=_fake_agent_returning("", side_effect=Exception("boom")),
        ):
            agent3 = HansardChatAgent(
                chat_model=AsyncMock(), retriever=retriever3, settings=mock_settings
            )
            error_response = await agent3.chat("Hello!")
        assert len(error_response.recommendations) == 3
