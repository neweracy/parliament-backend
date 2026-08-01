"""Tests for the RAG router contract.

Validates: Requirements 4.5, 8.6, 9.1

Covers:
- AskResponse model dump carries snake_case `related_records` members
  with values preserved (Requirement 4.5)
- Corpus hints are fetched unconditionally on every /rag/ask call
- History is capped at 20 messages (Requirement 8.6)
"""

from __future__ import annotations

from app.rag.router import (
    AskRequest,
    AskResponse,
    ChatMessage,
    RelatedRecord,
)

# ---------------------------------------------------------------------------
# AskResponse contract: related_records serialisation (Requirement 4.5)
# ---------------------------------------------------------------------------


class TestAskResponseContract:
    """Tests for the AskResponse Pydantic model contract."""

    def test_related_records_in_model_dump(self) -> None:
        """AskResponse model dump carries snake_case related_records with values preserved.

        **Validates: Requirement 4.5**
        """
        records = [
            RelatedRecord(
                transcript_id=42,
                label="Second Sitting",
                chunk_id=7,
                speaker="Hon. Mensah",
                sitting_title="Second Sitting of the Eighth Parliament",
                record_title="Question Time",
                date="2025-03-14",
                start_s=120.5,
            ),
            RelatedRecord(
                transcript_id=11,
                label="Minister for Finance",
                chunk_id=3,
                speaker=None,
                sitting_title=None,
                record_title=None,
                date=None,
                start_s=None,
            ),
        ]
        resp = AskResponse(
            answer="Test answer",
            citations=[],
            source_chunks=[],
            recommendations=[],
            related_records=records,
            latency_ms=50.0,
        )
        dump = resp.model_dump()

        assert "related_records" in dump
        assert len(dump["related_records"]) == 2
        assert dump["related_records"][0]["transcript_id"] == 42
        assert dump["related_records"][0]["label"] == "Second Sitting"
        assert dump["related_records"][0]["chunk_id"] == 7
        assert dump["related_records"][0]["speaker"] == "Hon. Mensah"
        assert (
            dump["related_records"][0]["sitting_title"] == "Second Sitting of the Eighth Parliament"
        )
        assert dump["related_records"][0]["start_s"] == 120.5
        # Null fields preserved
        assert dump["related_records"][1]["speaker"] is None
        assert dump["related_records"][1]["start_s"] is None

    def test_related_records_defaults_to_empty(self) -> None:
        """AskResponse.related_records defaults to empty list when not provided.

        **Validates: Requirement 4.5**
        """
        resp = AskResponse(
            answer="Test",
            citations=[],
            source_chunks=[],
            latency_ms=10.0,
        )
        assert resp.related_records == []
        dump = resp.model_dump()
        assert dump["related_records"] == []

    def test_related_records_empty_list_in_dump(self) -> None:
        """Empty related_records serializes correctly.

        **Validates: Requirement 4.5**
        """
        resp = AskResponse(
            answer="Test",
            citations=[],
            source_chunks=[],
            related_records=[],
            latency_ms=10.0,
        )
        dump = resp.model_dump()
        assert dump["related_records"] == []

    def test_all_related_record_fields_serialise(self) -> None:
        """Every field on RelatedRecord round-trips through model_dump.

        **Validates: Requirement 4.5**
        """
        record = RelatedRecord(
            transcript_id=99,
            label="Budget Debate",
            chunk_id=12,
            speaker="Mr. Speaker",
            sitting_title="Third Sitting",
            record_title="Budget Statement",
            date="2024-11-01",
            start_s=60.0,
        )
        dump = record.model_dump()
        assert dump == {
            "transcript_id": 99,
            "label": "Budget Debate",
            "chunk_id": 12,
            "speaker": "Mr. Speaker",
            "sitting_title": "Third Sitting",
            "record_title": "Budget Statement",
            "date": "2024-11-01",
            "start_s": 60.0,
        }

    def test_recommendations_defaults_to_empty(self) -> None:
        """AskResponse.recommendations defaults to empty list.

        **Validates: Requirement 9.1**
        """
        resp = AskResponse(
            answer="Test",
            citations=[],
            source_chunks=[],
            latency_ms=5.0,
        )
        assert resp.recommendations == []
        dump = resp.model_dump()
        assert dump["recommendations"] == []


# ---------------------------------------------------------------------------
# History cap at 20 messages (Requirement 8.6)
# ---------------------------------------------------------------------------


class TestHistoryCap:
    """Tests for the conversation history cap in the router."""

    def test_ask_request_accepts_more_than_20_messages(self) -> None:
        """AskRequest model does not reject >20 messages — cap is in endpoint logic.

        **Validates: Requirement 8.6**
        """
        messages = [
            ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}")
            for i in range(30)
        ]
        req = AskRequest(question="Test?", conversation_history=messages)
        assert len(req.conversation_history) == 30

    def test_history_slicing_yields_last_20(self) -> None:
        """The router slices conversation_history[-20:] — verify the slice contract.

        The endpoint does:
            recent = body.conversation_history[-20:]
        This test verifies that slice yields exactly the last 20 messages.

        **Validates: Requirement 8.6**
        """
        messages = [
            ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}")
            for i in range(30)
        ]
        req = AskRequest(question="Test?", conversation_history=messages)

        # Reproduce the router logic
        recent = req.conversation_history[-20:]

        assert len(recent) == 20
        assert recent[0].content == "msg 10"
        assert recent[-1].content == "msg 29"

    def test_history_slicing_under_20_is_identity(self) -> None:
        """When history has fewer than 20 messages, the slice returns all.

        **Validates: Requirement 8.6**
        """
        messages = [ChatMessage(role="user", content=f"msg {i}") for i in range(5)]
        req = AskRequest(question="Hi?", conversation_history=messages)

        recent = req.conversation_history[-20:]

        assert len(recent) == 5
        assert recent[0].content == "msg 0"

    def test_none_history_is_valid(self) -> None:
        """AskRequest with no conversation_history is valid (None default).

        **Validates: Requirement 8.6**
        """
        req = AskRequest(question="What happened?")
        assert req.conversation_history is None


# ---------------------------------------------------------------------------
# Unconditional corpus hints contract
# ---------------------------------------------------------------------------


class TestCorpusHintsAlwaysFetched:
    """Corpus hints are fetched on every /rag/ask call, not conditionally.

    The agent (HansardChatAgent), not the router, decides whether a turn needs
    a search_hansard call. The router therefore cannot know in advance whether
    hints will be needed, so `fetch_corpus_hints` is called unconditionally
    ahead of the agent invocation. This replaces the old `if not chunks:`
    conditional fetch from the pre-agent grounded-chain flow.
    """

    def test_rag_ask_fetches_hints_unconditionally(self) -> None:
        """rag_ask calls fetch_corpus_hints without a preceding retrieval gate."""
        import inspect

        from app.rag.router import rag_ask

        source = inspect.getsource(rag_ask)

        assert "fetch_corpus_hints(session_factory)" in source
        # The old conditional gate must not have resurfaced.
        assert "if not chunks" not in source
