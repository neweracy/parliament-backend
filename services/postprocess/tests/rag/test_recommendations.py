"""Unit tests for RecommendationBuilder fallbacks and corpus hint lookup.

Covers the example cases the property tests deliberately leave open: the exact
static set returned when neither chunks nor hints supply a value, the shape of
hint-derived suggestions when the chunk list is empty, and the failure
behaviour of `fetch_corpus_hints`, which must degrade to empty hints rather
than propagate a database error into the answer path.

The session factory is always a fake — no test here opens a database
connection.

Validates: Requirements 3.3, 3.4
"""

from __future__ import annotations

from langchain_core.documents import Document

from app.rag.recommendations import (
    _STATIC_SUGGESTIONS,
    CorpusHints,
    RecommendationBuilder,
    chunks_to_documents,
    fetch_corpus_hints,
    normalise_question,
)

# ---------------------------------------------------------------------------
# Fake async session plumbing
# ---------------------------------------------------------------------------


class _FakeResult:
    """Minimal stand-in for a SQLAlchemy `Result`."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    """Async session returning queued row batches, or raising on execute."""

    def __init__(
        self,
        row_batches: list[list[tuple]] | None = None,
        execute_error: Exception | None = None,
    ) -> None:
        self._row_batches = list(row_batches or [])
        self._execute_error = execute_error
        self.executed: list[str] = []

    async def execute(self, statement, params=None):
        self.executed.append(str(statement))
        if self._execute_error is not None:
            raise self._execute_error
        rows = self._row_batches.pop(0) if self._row_batches else []
        return _FakeResult(rows)


class _FakeSessionFactory:
    """Callable returning an async context manager yielding a `_FakeSession`.

    `call_error` raises when the factory itself is called, `enter_error` raises
    on context entry — the two distinct ways a real factory can fail before a
    query is ever issued.
    """

    def __init__(
        self,
        session: _FakeSession | None = None,
        call_error: Exception | None = None,
        enter_error: Exception | None = None,
    ) -> None:
        self.session = session or _FakeSession()
        self._call_error = call_error
        self._enter_error = enter_error
        self.call_count = 0

    def __call__(self) -> _FakeSessionFactory:
        self.call_count += 1
        if self._call_error is not None:
            raise self._call_error
        return self

    async def __aenter__(self) -> _FakeSession:
        if self._enter_error is not None:
            raise self._enter_error
        return self.session

    async def __aexit__(self, *exc_info) -> bool:
        return False


# ---------------------------------------------------------------------------
# Static fallback: no chunks, no hints
# ---------------------------------------------------------------------------


class TestStaticFallback:
    """IF neither chunks nor hints are available, the static set is returned."""

    def test_no_chunks_and_no_hints_returns_the_static_set(self) -> None:
        """The fixed 3-item general research set is returned verbatim.

        **Validates: Requirements 3.4**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(chunks=[], corpus_hints=None)

        assert len(items) == 3
        assert [(item.text, item.reason) for item in items] == list(_STATIC_SUGGESTIONS)

    def test_empty_hints_object_returns_the_static_set(self) -> None:
        """An empty `CorpusHints` is equivalent to no hints at all.

        **Validates: Requirements 3.4**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(chunks=[], corpus_hints=CorpusHints())

        assert [(item.text, item.reason) for item in items] == list(_STATIC_SUGGESTIONS)

    def test_blank_hint_values_are_ignored(self) -> None:
        """Blank and whitespace-only hint values yield no suggestion of their own.

        **Validates: Requirements 3.3, 3.4**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(
            chunks=[],
            corpus_hints=CorpusHints(entities=["", "   "], speakers=["  "]),
        )

        assert [(item.text, item.reason) for item in items] == list(_STATIC_SUGGESTIONS)

    def test_static_items_are_well_formed_and_distinct(self) -> None:
        """Every static item carries non-empty text and reason and is distinct.

        **Validates: Requirements 3.4**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(chunks=[], corpus_hints=None)

        for item in items:
            assert item.text.strip()
            assert item.reason.strip()

        keys = [normalise_question(item.text) for item in items]
        assert len(set(keys)) == len(keys)


# ---------------------------------------------------------------------------
# Hint-derived suggestions with an empty chunk list
# ---------------------------------------------------------------------------


class TestHintDerivedSuggestions:
    """WHERE no chunks exist, suggestions come from the corpus hints."""

    def test_hints_name_entities_before_speakers(self) -> None:
        """Entity hints are consumed first, then speaker hints, then the static set.

        **Validates: Requirements 3.3**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(
            chunks=[],
            corpus_hints=CorpusHints(entities=["Budget 2025"], speakers=["Hon. Ama Mensah"]),
        )

        assert len(items) == 3
        assert "Budget 2025" in items[0].text
        assert "Hon. Ama Mensah" in items[1].text
        assert items[2].text == _STATIC_SUGGESTIONS[0][0]

        for item in items:
            assert item.text.strip()
            assert item.reason.strip()

    def test_hint_values_are_whitespace_normalised(self) -> None:
        """Padded hint values are collapsed before being named in a suggestion.

        **Validates: Requirements 3.3**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(
            chunks=[],
            corpus_hints=CorpusHints(entities=["  Ministry   of Education  "]),
            count=1,
        )

        assert items[0].text == "What was said about Ministry of Education in the record?"

    def test_hints_fill_the_full_count_when_plentiful(self) -> None:
        """With enough hint values, no static item is needed.

        **Validates: Requirements 3.3**
        """
        builder = RecommendationBuilder()

        items = builder.suggestions(
            chunks=[],
            corpus_hints=CorpusHints(
                entities=["Accra", "Kumasi"],
                speakers=["Mr Speaker", "Minister for Finance"],
            ),
        )

        static_texts = {text for text, _ in _STATIC_SUGGESTIONS}
        assert len(items) == 3
        assert not any(item.text in static_texts for item in items)

    def test_excluded_hint_suggestion_is_skipped(self) -> None:
        """A hint suggestion already present in `exclude` is not returned again.

        **Validates: Requirements 3.3**
        """
        builder = RecommendationBuilder()

        first = builder.suggestions(
            chunks=[],
            corpus_hints=CorpusHints(entities=["Accra"]),
            count=1,
        )

        items = builder.suggestions(
            chunks=[],
            corpus_hints=CorpusHints(entities=["Accra"], speakers=["Mr Speaker"]),
            exclude=[first[0].text],
            count=1,
        )

        assert items[0].text != first[0].text
        assert "Mr Speaker" in items[0].text


# ---------------------------------------------------------------------------
# fetch_corpus_hints
# ---------------------------------------------------------------------------


class TestFetchCorpusHints:
    """A failed hint lookup must never fail the answer."""

    async def test_successful_lookup_returns_cleaned_values(self) -> None:
        """Entity and speaker rows are cleaned and blanks dropped.

        **Validates: Requirements 3.3**
        """
        session = _FakeSession(
            row_batches=[
                [("Budget 2025",), ("  Accra  ",), (None,), ("",)],
                [("Mr Speaker",), ("   ",)],
            ]
        )
        factory = _FakeSessionFactory(session=session)

        hints = await fetch_corpus_hints(factory)

        assert hints.entities == ["Budget 2025", "Accra"]
        assert hints.speakers == ["Mr Speaker"]
        assert len(session.executed) == 2

    async def test_empty_corpus_returns_empty_hints(self) -> None:
        """No rows means empty hints, not an error.

        **Validates: Requirements 3.3**
        """
        factory = _FakeSessionFactory(session=_FakeSession(row_batches=[[], []]))

        hints = await fetch_corpus_hints(factory)

        assert hints == CorpusHints()

    async def test_factory_call_failure_returns_empty_hints(self) -> None:
        """A factory that raises when called degrades to empty hints.

        **Validates: Requirements 3.3**
        """
        factory = _FakeSessionFactory(call_error=RuntimeError("pool exhausted"))

        hints = await fetch_corpus_hints(factory)

        assert hints == CorpusHints()

    async def test_session_entry_failure_returns_empty_hints(self) -> None:
        """A session that raises on context entry degrades to empty hints.

        **Validates: Requirements 3.3**
        """
        factory = _FakeSessionFactory(enter_error=RuntimeError("connection refused"))

        hints = await fetch_corpus_hints(factory)

        assert hints == CorpusHints()
        assert factory.call_count == 1

    async def test_execute_failure_returns_empty_hints(self) -> None:
        """A query that raises degrades to empty hints.

        **Validates: Requirements 3.3**
        """
        session = _FakeSession(execute_error=RuntimeError("relation does not exist"))
        factory = _FakeSessionFactory(session=session)

        hints = await fetch_corpus_hints(factory)

        assert hints == CorpusHints()
        assert len(session.executed) == 1

    async def test_none_session_factory_returns_empty_hints(self) -> None:
        """A missing session factory degrades to empty hints.

        **Validates: Requirements 3.3**
        """
        hints = await fetch_corpus_hints(None)

        assert hints == CorpusHints()

    async def test_hints_from_a_failed_lookup_still_drive_the_static_set(self) -> None:
        """Empty hints from a failed lookup leave the builder on its static set.

        **Validates: Requirements 3.3, 3.4**
        """
        factory = _FakeSessionFactory(enter_error=RuntimeError("connection refused"))

        hints = await fetch_corpus_hints(factory)
        items = RecommendationBuilder().suggestions(chunks=[], corpus_hints=hints)

        assert [(item.text, item.reason) for item in items] == list(_STATIC_SUGGESTIONS)


def test_finalise_answer_accepts_retrieved_chunks(sample_retrieved_chunks):
    """finalise_answer converts RetrievedChunk input before handing it to the builder.

    Regression: the builder reads `Document.metadata`, so passing RetrievedChunk
    straight through used to raise AttributeError.
    """
    import time as _time

    from app.rag.recommendations import RecommendationBuilder, finalise_answer

    response = finalise_answer(
        builder=RecommendationBuilder(),
        answer="Some answer [1].",
        citations=[],
        context_chunks=sample_retrieved_chunks,
        parsed=[],
        hint_chunks=sample_retrieved_chunks,
        corpus_hints=None,
        history_questions=[],
        grounded=True,
        start_time=_time.perf_counter(),
    )

    assert len(response.recommendations) == 3
    assert all(r.text and r.reason for r in response.recommendations)
    assert len(response.related_records) >= 1
    assert response.source_chunks == sample_retrieved_chunks


# ---------------------------------------------------------------------------
# related_records dedup key — record_id, falling back to transcript_id
# ---------------------------------------------------------------------------


def _related_chunk(
    *,
    chunk_id: int,
    transcript_id: int,
    record_id: int | None,
    sitting_id: int | None = 1,
    label: str = "Budget Debate 2026 — Revised",
) -> Document:
    """Build a Document carrying only the fields related_records reads."""
    return Document(
        page_content="text",
        metadata={
            "chunk_id": chunk_id,
            "transcript_id": transcript_id,
            "record_id": record_id,
            "sitting_id": sitting_id,
            "speaker": None,
            "start_s": None,
            "end_s": None,
            "entity_names": [],
            "score": 0.5,
            "sitting_title": label,
            "record_title": None,
            "date": None,
        },
    )


def test_related_records_dedup_by_record_id_not_transcript_id():
    """Two transcript versions of one record collapse into a single chip.

    Regression: dedup used to key on `transcript_id`, so two transcripts of the
    same record produced two chips with the same label pointing at the same
    destination.
    """
    chunks = [
        _related_chunk(chunk_id=1, transcript_id=1, record_id=1),
        _related_chunk(chunk_id=2, transcript_id=2, record_id=1),
    ]

    records = RecommendationBuilder().related_records(chunks=chunks)

    assert len(records) == 1
    assert records[0].record_id == 1
    assert records[0].sitting_id == 1
    # The highest-scoring chunk wins its key.
    assert records[0].chunk_id == 1
    assert records[0].transcript_id == 1


def test_related_records_keeps_distinct_record_ids():
    """Chunks with different record_id values each get their own chip."""
    chunks = [
        _related_chunk(chunk_id=1, transcript_id=1, record_id=1, label="Budget Debate"),
        _related_chunk(chunk_id=2, transcript_id=1, record_id=2, label="Question Time"),
    ]

    records = RecommendationBuilder().related_records(chunks=chunks)

    assert [r.record_id for r in records] == [1, 2]
    assert [r.label for r in records] == ["Budget Debate", "Question Time"]


def test_related_records_falls_back_to_transcript_id_when_record_id_is_none():
    """Un-enriched chunks keep the old transcript_id dedup behaviour."""
    chunks = [
        _related_chunk(chunk_id=1, transcript_id=1, record_id=None, sitting_id=None),
        _related_chunk(chunk_id=2, transcript_id=1, record_id=None, sitting_id=None),
        _related_chunk(chunk_id=3, transcript_id=2, record_id=None, sitting_id=None),
    ]

    records = RecommendationBuilder().related_records(chunks=chunks)

    assert [r.transcript_id for r in records] == [1, 2]
    assert all(r.record_id is None and r.sitting_id is None for r in records)


def test_related_records_mixed_present_and_absent_record_ids():
    """A None record_id never collides with a present one."""
    chunks = [
        _related_chunk(chunk_id=1, transcript_id=1, record_id=1),
        _related_chunk(chunk_id=2, transcript_id=1, record_id=None),
    ]

    records = RecommendationBuilder().related_records(chunks=chunks)

    assert len(records) == 2
    assert [r.record_id for r in records] == [1, None]


def test_chunks_to_documents_carries_navigation_ids(sample_retrieved_chunks):
    """chunks_to_documents projects sitting_id and record_id onto the metadata."""
    sample_retrieved_chunks[0].sitting_id = 4
    sample_retrieved_chunks[0].record_id = 9

    documents = chunks_to_documents(sample_retrieved_chunks)

    assert documents[0].metadata["sitting_id"] == 4
    assert documents[0].metadata["record_id"] == 9
    assert documents[1].metadata["sitting_id"] is None
    assert documents[1].metadata["record_id"] is None
