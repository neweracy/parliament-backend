"""Tests for the search-query recommendation path.

Covers the guarantee the whole stack now leans on: a search-recommendation
request yields exactly the requested number of usable items no matter what the
language model does. The gateway and the search box both treat a 2xx as
carrying suggestions, so every degradation route is exercised here — model
absent, model raising, model timing out, model returning prose, model echoing
the query back.

No test here opens a database connection or contacts a model.
"""

from __future__ import annotations

import asyncio

import pytest

from app.rag import search_recommendations as sr
from app.rag.recommendations import (
    MAX_SEARCH_RECOMMENDATION_COUNT,
    TARGET_SEARCH_RECOMMENDATION_COUNT,
    CorpusHints,
    RecommendationBuilder,
    chunks_to_documents,
    normalise_question,
)
from app.rag.retriever import RetrievedChunk
from app.rag.search_recommendations import generate_search_recommendations

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMessage:
    """Minimal stand-in for a LangChain message."""

    def __init__(self, content) -> None:
        self.content = content


class _ScriptedModel:
    """Returns fixed content, recording the prompts it was given."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[list[dict]] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return _FakeMessage(self._content)


class _RaisingModel:
    async def ainvoke(self, messages):
        raise RuntimeError("bedrock is unavailable")


class _HangingModel:
    """Sleeps past any sane deadline so the timeout path is reachable."""

    def __init__(self) -> None:
        self.started = False

    async def ainvoke(self, messages):
        self.started = True
        await asyncio.sleep(60)
        return _FakeMessage("too late to matter")


class _StubRetriever:
    """Returns queued chunks, or raises, recording each call."""

    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        self._chunks = chunks or []
        self._error = error
        self.calls: list[dict] = []

    async def retrieve(self, query, filters=None, limit=None):
        self.calls.append({"query": query, "filters": filters, "limit": limit})
        if self._error is not None:
            raise self._error
        return self._chunks


def _chunk(chunk_id: int, **overrides) -> RetrievedChunk:
    """Build a RetrievedChunk with sensible defaults."""
    fields = {
        "chunk_id": chunk_id,
        "text": "The Minister addressed the House on the matter.",
        "relevance_score": 0.9,
        "transcript_id": 100,
        "speaker": "Hon. Ama Mensah",
        "start_s": 12.0,
        "end_s": 30.0,
        "matched_entities": ["Ministry of Health"],
        "record_title": "Health Financing Statement",
        "sitting_title": "Morning Session",
        "date": "2026-03-04",
    }
    fields.update(overrides)
    return RetrievedChunk(**fields)


HINTS = CorpusHints(
    entities=["Ministry of Health", "Appropriation Bill"],
    speakers=["Hon. Ama Mensah", "Hon. Kofi Arhin"],
)


def _assert_well_formed(items, count: int) -> None:
    """Assert the invariant every caller depends on."""
    assert len(items) == count
    for item in items:
        assert item.text.strip(), "text must be non-empty"
        assert item.reason.strip(), "reason must be non-empty"
    keys = [normalise_question(item.text) for item in items]
    assert len(keys) == len(set(keys)), f"items must be distinct, got {keys}"


# ---------------------------------------------------------------------------
# RecommendationBuilder.search_queries — the deterministic floor
# ---------------------------------------------------------------------------


class TestSearchQueriesBuilder:
    def test_returns_exactly_count_from_chunks(self):
        builder = RecommendationBuilder()
        chunks = chunks_to_documents([_chunk(1), _chunk(2, transcript_id=101)])

        for count in (1, 3, 5, MAX_SEARCH_RECOMMENDATION_COUNT):
            items = builder.search_queries(chunks=chunks, count=count)
            _assert_well_formed(items, count)

    def test_chunk_values_are_preferred_over_hints(self):
        builder = RecommendationBuilder()
        chunks = chunks_to_documents([_chunk(1)])

        items = builder.search_queries(chunks=chunks, corpus_hints=HINTS, count=3)
        joined = " ".join(item.text for item in items)

        # Every leading suggestion should name something from the chunk.
        assert "Ministry of Health" in joined or "Hon. Ama Mensah" in joined

    def test_falls_back_to_hints_when_no_chunks(self):
        builder = RecommendationBuilder()

        items = builder.search_queries(chunks=[], corpus_hints=HINTS, count=4)
        _assert_well_formed(items, 4)

        texts = [item.text for item in items]
        assert "Ministry of Health" in texts
        assert "Hon. Ama Mensah" in texts

    def test_falls_back_to_static_and_filler_with_no_data(self):
        builder = RecommendationBuilder()

        items = builder.search_queries(chunks=[], corpus_hints=None, count=6)
        _assert_well_formed(items, 6)

    def test_honours_exclude(self):
        builder = RecommendationBuilder()

        items = builder.search_queries(
            chunks=[],
            corpus_hints=HINTS,
            exclude=["Ministry of Health", "budget allocation"],
            count=5,
        )
        _assert_well_formed(items, 5)

        keys = {normalise_question(item.text) for item in items}
        assert normalise_question("Ministry of Health") not in keys
        assert normalise_question("budget allocation") not in keys

    def test_exclude_matching_is_case_and_punctuation_insensitive(self):
        builder = RecommendationBuilder()

        items = builder.search_queries(
            chunks=[], corpus_hints=HINTS, exclude=["  MINISTRY OF HEALTH?  "], count=4
        )

        keys = {normalise_question(item.text) for item in items}
        assert normalise_question("Ministry of Health") not in keys

    def test_suggestions_are_terms_not_questions(self):
        builder = RecommendationBuilder()
        chunks = chunks_to_documents([_chunk(1)])

        items = builder.search_queries(chunks=chunks, corpus_hints=HINTS, count=8)

        # The Q&A path yields prose questions; the search path must not, since
        # these go into a keyword search box.
        for item in items:
            assert "?" not in item.text
            assert not item.text.lower().startswith(("what ", "which ", "who ", "how "))

    def test_non_positive_count_yields_nothing(self):
        builder = RecommendationBuilder()

        assert builder.search_queries(chunks=[], count=0) == []
        assert builder.search_queries(chunks=[], count=-3) == []

    def test_blank_chunk_metadata_does_not_produce_empty_text(self):
        builder = RecommendationBuilder()
        chunks = chunks_to_documents(
            [
                _chunk(
                    1,
                    speaker="   ",
                    matched_entities=["", "  "],
                    record_title=None,
                    sitting_title="",
                    date=None,
                )
            ]
        )

        items = builder.search_queries(chunks=chunks, count=5)
        _assert_well_formed(items, 5)


# ---------------------------------------------------------------------------
# generate_search_recommendations — model path plus every degradation
# ---------------------------------------------------------------------------


class TestGenerateSearchRecommendations:
    async def test_parses_model_lines(self):
        model = _ScriptedModel(
            "NHIS funding | Health financing debates\n"
            "teacher allowance arrears | A recurring grievance\n"
            "Ministry of Energy tariff | Utility pricing decisions"
        )

        result = await generate_search_recommendations(
            chat_model=model, query="health", corpus_hints=HINTS, count=3
        )

        _assert_well_formed(result.recommendations, 3)
        assert result.source == "model"
        assert result.model_used is True
        assert [item.text for item in result.recommendations] == [
            "NHIS funding",
            "teacher allowance arrears",
            "Ministry of Energy tariff",
        ]

    async def test_strips_markdown_emphasis_from_model_output(self):
        # The previous gateway implementation *required* these markers and
        # dropped anything without them. They must now simply be removed.
        model = _ScriptedModel(
            "**NHIS funding** | Health financing\n"
            "- __teacher allowances__ | Education grievance\n"
            "1. `committee report` | Tabled findings"
        )

        result = await generate_search_recommendations(
            chat_model=model, query="health", count=3
        )

        assert [item.text for item in result.recommendations] == [
            "NHIS funding",
            "teacher allowances",
            "committee report",
        ]

    async def test_tops_up_when_model_returns_too_few(self):
        model = _ScriptedModel("NHIS funding | Only one suggestion")

        result = await generate_search_recommendations(
            chat_model=model, query="health", corpus_hints=HINTS, count=5
        )

        _assert_well_formed(result.recommendations, 5)
        assert result.source == "mixed"
        assert result.recommendations[0].text == "NHIS funding"

    async def test_ignores_model_line_echoing_the_typed_query(self):
        model = _ScriptedModel(
            "health | just echoes the query\n"
            "HEALTH? | the same thing, restyled\n"
            "health policy | genuinely different"
        )

        result = await generate_search_recommendations(
            chat_model=model, query="health", corpus_hints=HINTS, count=4
        )

        _assert_well_formed(result.recommendations, 4)
        keys = {normalise_question(item.text) for item in result.recommendations}
        assert normalise_question("health") not in keys
        assert normalise_question("health policy") in keys

    async def test_deduplicates_repeated_model_lines(self):
        model = _ScriptedModel(
            "health policy | first\nhealth policy | duplicate\nHEALTH POLICY | also duplicate"
        )

        result = await generate_search_recommendations(
            chat_model=model, query="x", corpus_hints=HINTS, count=4
        )

        _assert_well_formed(result.recommendations, 4)

    @pytest.mark.parametrize(
        "model",
        [None, _RaisingModel(), _ScriptedModel("   "), _ScriptedModel("")],
        ids=["no_model", "model_raises", "blank_output", "empty_output"],
    )
    async def test_degrades_to_deterministic(self, model):
        result = await generate_search_recommendations(
            chat_model=model, query="health", corpus_hints=HINTS, count=5
        )

        _assert_well_formed(result.recommendations, 5)
        assert result.source == "deterministic"
        assert result.model_used is False

    async def test_model_timeout_degrades_to_deterministic(self, monkeypatch):
        monkeypatch.setattr(sr, "LLM_TIMEOUT_S", 0.05)
        model = _HangingModel()

        result = await generate_search_recommendations(
            chat_model=model, query="health", corpus_hints=HINTS, count=5
        )

        assert model.started is True
        _assert_well_formed(result.recommendations, 5)
        assert result.source == "deterministic"

    async def test_prose_output_does_not_yield_empty_set(self):
        # A model that ignores the format instruction entirely.
        model = _ScriptedModel("I am not able to help with that request.")

        result = await generate_search_recommendations(
            chat_model=model, query="health", corpus_hints=HINTS, count=5
        )

        _assert_well_formed(result.recommendations, 5)

    async def test_retrieval_grounds_the_prompt(self):
        model = _ScriptedModel("a | b")
        retriever = _StubRetriever(chunks=[_chunk(1)])

        await generate_search_recommendations(
            chat_model=model, query="health", retriever=retriever, count=3
        )

        assert retriever.calls[0]["query"] == "health"
        assert retriever.calls[0]["limit"] == sr.RETRIEVAL_LIMIT

        prompt = model.calls[0][-1]["content"]
        assert "CORPUS CONTEXT" in prompt
        assert "Hon. Ama Mensah" in prompt

    async def test_retrieval_failure_does_not_fail_the_request(self):
        retriever = _StubRetriever(error=RuntimeError("index unavailable"))

        result = await generate_search_recommendations(
            chat_model=None, query="health", retriever=retriever, corpus_hints=HINTS, count=5
        )

        _assert_well_formed(result.recommendations, 5)

    async def test_retrieval_skipped_without_a_query(self):
        retriever = _StubRetriever(chunks=[_chunk(1)])

        result = await generate_search_recommendations(
            chat_model=None, query=None, retriever=retriever, corpus_hints=HINTS, count=5
        )

        assert retriever.calls == [], "no query means nothing to retrieve against"
        _assert_well_formed(result.recommendations, 5)

    async def test_prompt_differs_when_nothing_typed(self):
        model = _ScriptedModel("a | b")

        await generate_search_recommendations(chat_model=model, query=None, count=3)
        blank_prompt = model.calls[0][-1]["content"]

        await generate_search_recommendations(chat_model=model, query="health", count=3)
        typed_prompt = model.calls[1][-1]["content"]

        assert "not typed anything" in blank_prompt
        assert "health" in typed_prompt

    async def test_defaults_to_target_count(self):
        result = await generate_search_recommendations(chat_model=None, corpus_hints=HINTS)

        _assert_well_formed(result.recommendations, TARGET_SEARCH_RECOMMENDATION_COUNT)

    async def test_non_positive_count_yields_nothing(self):
        result = await generate_search_recommendations(
            chat_model=None, query="health", corpus_hints=HINTS, count=0
        )

        assert result.recommendations == []

    async def test_latency_is_recorded(self):
        result = await generate_search_recommendations(chat_model=None, count=3)

        assert result.latency_ms >= 0.0

    async def test_no_corpus_data_at_all_still_returns_full_set(self):
        result = await generate_search_recommendations(
            chat_model=None, query=None, retriever=None, corpus_hints=CorpusHints(), count=8
        )

        _assert_well_formed(result.recommendations, 8)
