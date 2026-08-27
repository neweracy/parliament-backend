"""Tests for the RAG router contract.

Validates: Requirements 4.5, 8.6, 9.1

Covers:
- AskResponse model dump carries snake_case `related_records` members
  with values preserved (Requirement 4.5)
- Corpus hints are fetched unconditionally on every /rag/ask call
- History is capped at 20 messages (Requirement 8.6)
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.deps import verify_service_token
from app.rag.recommendations import (
    MAX_SEARCH_RECOMMENDATION_COUNT,
    TARGET_SEARCH_RECOMMENDATION_COUNT,
)
from app.rag.router import (
    AskRequest,
    AskResponse,
    ChatMessage,
    RegistryReferenceItem,
    RelatedRecord,
    SearchRecommendationRequest,
    SearchRecommendationResponse,
    _has_summary_intent,
    _is_simple_search_question,
)
from app.rag.router import router as rag_router

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
            sitting_id=3,
            record_id=8,
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
            "sitting_id": 3,
            "record_id": 8,
        }

    def test_related_record_navigation_ids_default_to_none(self) -> None:
        """A RelatedRecord built without navigation ids serialises them as null.

        The frontend treats null as "not navigable" and disables the chip, so
        the key has to be present rather than omitted.
        """
        record = RelatedRecord(transcript_id=1, label="Budget Debate", chunk_id=2)
        dump = record.model_dump()
        assert dump["sitting_id"] is None
        assert dump["record_id"] is None

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

    def test_registry_references_defaults_to_empty(self) -> None:
        """AskResponse.registry_references defaults to empty list."""
        resp = AskResponse(
            answer="Test",
            citations=[],
            source_chunks=[],
            latency_ms=5.0,
        )
        assert resp.registry_references == []
        dump = resp.model_dump()
        assert dump["registry_references"] == []

    def test_registry_references_in_model_dump(self) -> None:
        """AskResponse model dump carries registry_references with values preserved.

        Unlike RelatedRecord, a RegistryReferenceItem never carries a
        chunk_id/transcript_id — it comes from sitting/hansard_record rows
        directly, and a sitting-only reference has record_id=None.
        """
        refs = [
            RegistryReferenceItem(
                kind="sitting",
                id=1,
                title="Startup Verification Sitting",
                sitting_id=1,
                record_id=None,
                created_at="2026-08-20T12:00:00",
            ),
            RegistryReferenceItem(
                kind="record",
                id=5,
                title="Morning Session",
                sitting_id=1,
                record_id=5,
                created_at="2026-08-20T12:05:00",
            ),
        ]
        resp = AskResponse(
            answer="Test answer",
            citations=[],
            source_chunks=[],
            registry_references=refs,
            latency_ms=50.0,
        )
        dump = resp.model_dump()

        assert len(dump["registry_references"]) == 2
        assert dump["registry_references"][0]["kind"] == "sitting"
        assert dump["registry_references"][0]["id"] == 1
        assert dump["registry_references"][0]["record_id"] is None
        assert dump["registry_references"][1]["kind"] == "record"
        assert dump["registry_references"][1]["record_id"] == 5


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


# ---------------------------------------------------------------------------
# Search recommendation contract
# ---------------------------------------------------------------------------


class TestSearchRecommendationRequest:
    """The limit band must match the one the gateway clamps to."""

    def test_limit_defaults_to_target_count(self) -> None:
        assert SearchRecommendationRequest().limit == TARGET_SEARCH_RECOMMENDATION_COUNT

    def test_query_is_optional(self) -> None:
        # The search box asks for suggestions before anything is typed.
        assert SearchRecommendationRequest().query is None

    def test_limit_accepts_the_whole_band(self) -> None:
        for value in range(1, MAX_SEARCH_RECOMMENDATION_COUNT + 1):
            assert SearchRecommendationRequest(limit=value).limit == value

    def test_limit_rejects_values_outside_the_band(self) -> None:
        for value in (0, -1, MAX_SEARCH_RECOMMENDATION_COUNT + 1):
            with pytest.raises(ValidationError):
                SearchRecommendationRequest(limit=value)

    def test_filters_use_snake_case(self) -> None:
        body = SearchRecommendationRequest(
            query="health",
            entity_filter=["Ministry of Health"],
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
            speaker="Hon. Ama Mensah",
        )

        dumped = body.model_dump()
        assert dumped["entity_filter"] == ["Ministry of Health"]
        assert "date_from" in dumped and "date_to" in dumped
        assert "entityFilter" not in dumped


class TestSearchRecommendationResponse:
    def test_defaults_are_safe(self) -> None:
        response = SearchRecommendationResponse()

        assert response.recommendations == []
        assert response.latency_ms == 0.0
        assert response.source == "deterministic"
        assert response.model_used is False

    def test_dump_uses_snake_case_for_model_used(self) -> None:
        # The gateway maps model_used -> modelUsed, so the wire name matters.
        dumped = SearchRecommendationResponse(model_used=True).model_dump()

        assert dumped["model_used"] is True
        assert "modelUsed" not in dumped


class TestRecommendationsEndpointDegrades:
    """POST /rag/recommendations must not mirror /rag/ask's 503.

    A search box that stops offering suggestions reads as broken. The endpoint
    has a deterministic builder to fall back on, so it answers 200 even with no
    chat model and no reachable database.
    """

    @staticmethod
    def _client(chat_model=None) -> TestClient:
        app = FastAPI()
        app.include_router(rag_router)
        app.dependency_overrides[verify_service_token] = lambda: None

        def broken_session_factory():
            raise RuntimeError("no database in this test")

        app.state.session_factory = broken_session_factory
        app.state.settings = SimpleNamespace(rag_query_timeout_ms=30000)
        app.state.embeddings = None
        app.state.chat_model = chat_model

        return TestClient(app)

    def test_returns_200_without_a_chat_model(self) -> None:
        response = self._client().post("/rag/recommendations", json={"query": "health"})

        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "deterministic"
        assert body["model_used"] is False
        assert len(body["recommendations"]) == TARGET_SEARCH_RECOMMENDATION_COUNT
        for item in body["recommendations"]:
            assert item["text"].strip()
            assert item["reason"].strip()

    def test_returns_200_with_no_query(self) -> None:
        response = self._client().post("/rag/recommendations", json={})

        assert response.status_code == 200
        assert len(response.json()["recommendations"]) == TARGET_SEARCH_RECOMMENDATION_COUNT

    def test_honours_the_requested_limit(self) -> None:
        client = self._client()

        for value in (1, 3, MAX_SEARCH_RECOMMENDATION_COUNT):
            response = client.post("/rag/recommendations", json={"query": "x", "limit": value})
            assert response.status_code == 200
            assert len(response.json()["recommendations"]) == value

    def test_rejects_an_out_of_band_limit(self) -> None:
        client = self._client()

        for value in (0, MAX_SEARCH_RECOMMENDATION_COUNT + 1):
            response = client.post("/rag/recommendations", json={"query": "x", "limit": value})
            assert response.status_code == 422


# ---------------------------------------------------------------------------
# Summary-intent routing
# ---------------------------------------------------------------------------


class TestSummaryIntentDetection:
    """Summary requests must be recognised so they reach the tool-bearing agent.

    GroundedAnsweringChain (the fast path) has no tools; the record
    summarization tool lives on HansardChatAgent. A summary question that looks
    like a simple first-turn search would otherwise never see the tool.
    """

    @pytest.mark.parametrize(
        "question",
        [
            "Summarize the Morning Session record",
            'What does "Budget Debate" talk about? Give me a summary of the key '
            "topics, decisions, and speakers.",
            "What was discussed in the Startup Verification Sitting?",
            'What is "Morning Session" about?',
            "Give me a summary of the proceedings",
            # "tell me" + container word pairs
            "tell me somethihng short about test record",
            "tell me about the Morning Session record",
            "Tell me what happened in the plenary sitting",
            "tell me about the budget session",
            # "what is in" / "what's in" patterns
            "What is in the test record?",
            "What's in the Morning Session?",
        ],
    )
    def test_summary_phrasings_are_detected(self, question: str) -> None:
        assert _has_summary_intent(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            # A topic search, not a request for a record's contents.
            "What was discussed about the budget?",
            "What did the Finance Minister say about inflation?",
            "Hello",
            # Possessive apostrophes must not read as a quoted record title.
            "What is the Speaker's ruling about inflation?",
            # "tell me" without a container word is a topic search.
            "tell me about the budget allocations",
            "tell me what the minister said about taxes",
        ],
    )
    def test_topic_searches_are_not_summary_intent(self, question: str) -> None:
        assert _has_summary_intent(question) is False

    def test_discussed_in_versus_discussed_about(self) -> None:
        """The preposition is the whole distinction: `in` names a record, `about` a topic."""
        assert _has_summary_intent("What was discussed in the Morning Session?") is True
        assert _has_summary_intent("What was discussed about the budget?") is False

    @pytest.mark.parametrize("verb", ["summarize", "summarise"])
    def test_both_spellings_match(self, verb: str) -> None:
        assert _has_summary_intent(f"Please {verb} this record") is True

    @pytest.mark.parametrize(
        "question",
        [
            "SUMMARIZE THE MORNING SESSION RECORD",
            "Summarise The Morning Session Record",
            "WHAT WAS DISCUSSED IN THE STARTUP VERIFICATION SITTING?",
        ],
    )
    def test_matching_is_case_insensitive(self, question: str) -> None:
        assert _has_summary_intent(question) is True

    def test_summary_intent_overrides_the_fast_path(self) -> None:
        """The frontend button phrasing qualifies as a simple search but must not use it.

        This mirrors the router's routing decision: a question only takes the
        fast path when it is a simple first-turn search *and* carries no summary
        intent.
        """
        question = (
            'What does "Morning Session" talk about? Give me a summary of the '
            "key topics, decisions, and speakers."
        )

        assert _is_simple_search_question(question, has_history=False) is True
        assert _has_summary_intent(question) is True

        use_fast_path = _is_simple_search_question(question, has_history=False) and not (
            _has_summary_intent(question)
        )
        assert use_fast_path is False

    def test_plain_topic_search_still_takes_the_fast_path(self) -> None:
        question = "What did the Finance Minister say about inflation?"

        use_fast_path = _is_simple_search_question(question, has_history=False) and not (
            _has_summary_intent(question)
        )
        assert use_fast_path is True

    def test_tell_me_about_record_takes_agent_path(self) -> None:
        """The original failing prompt must route to the agent where summarize_record lives."""
        question = "tell me somethihng short about test record"

        # Qualifies as a simple search (7 words, no history, no conversational prefix)
        assert _is_simple_search_question(question, has_history=False) is True
        # But the summary intent override kicks in
        assert _has_summary_intent(question) is True

        use_fast_path = _is_simple_search_question(question, has_history=False) and not (
            _has_summary_intent(question)
        )
        assert use_fast_path is False
