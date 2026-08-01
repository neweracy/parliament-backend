"""Property tests for RecommendationBuilder suggestions and related records.

Validates: Requirements 3.1, 3.3, 4.1, 4.2, 4.3, 4.4, 4.9

Property 3: Builder suggestions are grounded in available data.

For any non-empty set of retrieved chunks, every builder-derived suggestion
`text` names at least one entity name, speaker label, date, or sitting title
present on those chunks; and for any empty chunk set with non-empty corpus
hints, every builder-derived suggestion `text` names at least one entity or
speaker value from those hints. Where neither source supplies a value, the
builder falls back to its general parliamentary research set — it never invents
a name that is absent from the data.

Generators produce chunk lists with `None` speakers and dates, blank and
whitespace-only entity values, empty `matched_entities`, and the empty list,
paired with `CorpusHints` both with and without values.

Property 4: Related records are deduplicated, labelled, and bounded.

For any chunk list, `related_records()` returns at most `MAX_RELATED_RECORDS`
items, at most one per distinct `transcript_id`, in the relevance order the
chunks were given, so the highest-scoring chunk wins its id. Every label is
non-empty and is the first non-empty value among `sitting_title`,
`record_title`, `speaker`, and `date`, falling back to
`Transcript {transcript_id}`. `start_s` is carried through when the source
chunk has one and is `None` when it does not. The empty chunk list yields an
empty collection.

Generators produce chunk lists drawing `transcript_id` from a narrow pool so
ids repeat, `None` and present `start_s` values, chunks whose label-bearing
fields are all absent or blank, and the empty list.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.documents import Document

from app.rag.recommendations import (
    _STATIC_SUGGESTIONS,
    MAX_RELATED_RECORDS,
    CorpusHints,
    RecommendationBuilder,
    normalise_question,
)

# ---------------------------------------------------------------------------
# Value pools
#
# Deliberately realistic multi-character names, none of which appear as a
# substring of the static or filler suggestion text. That keeps the "names a
# value from the data" check free of accidental substring matches.
# ---------------------------------------------------------------------------

_ENTITIES = ["Ghana", "Kumasi", "Budget 2025", "Ministry of Education", "Accra"]
_SPEAKERS = ["Hon. Ama Mensah", "Mr Speaker", "Minister for Finance"]
_DATES = ["2025-03-14", "12 June 2024"]
_SITTING_TITLES = ["Second Sitting of the Eighth Parliament", "Committee of the Whole"]
_RECORD_TITLES = ["Question Time", "Statement on Flood Relief"]

_UNRELATED_QUESTIONS = [
    "How many members voted?",
    "Who chaired the committee?",
    "What was the final resolution?",
]

_STATIC_KEYS = {normalise_question(text) for text, _ in _STATIC_SUGGESTIONS}
_FILLER_PATTERN = re.compile(r"^summarise the \d+(st|nd|rd|th) most recent sitting in the record$")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _optional_value(pool: list[str]) -> st.SearchStrategy[str | None]:
    """A value from `pool`, or a padded, blank, or absent variant of one."""
    return st.one_of(
        st.none(),
        st.just(""),
        st.just("   "),
        st.sampled_from(pool),
        st.sampled_from(pool).map(lambda value: f"  {value}  "),
    )


def _entity_value(pool: list[str]) -> st.SearchStrategy[str]:
    """An entity string — `matched_entities` is typed `list[str]`, never `None`."""
    return st.one_of(
        st.just(""),
        st.just("  "),
        st.sampled_from(pool),
        st.sampled_from(pool).map(lambda value: f" {value} "),
    )


@st.composite
def retrieved_chunks(
    draw: st.DrawFn,
    min_size: int = 0,
    max_size: int = 4,
    transcript_id_pool: list[int] | None = None,
    blank_labels: bool = False,
) -> list[Document]:
    """Generate a relevance-ordered chunk list with sparse metadata.

    `relevance_score` is strictly descending, matching the retriever's output
    order, so "the first chunk with a given id" is also the highest-scoring one.

    Args:
        min_size: Smallest list length to generate.
        max_size: Largest list length to generate.
        transcript_id_pool: When given, `transcript_id` values are drawn from
            this narrow pool so a generated list repeats them — this is what
            exercises related-record deduplication.
        blank_labels: When true, every label-bearing field (`sitting_title`,
            `record_title`, `speaker`, `date`) is absent or whitespace-only, so
            the `Transcript {transcript_id}` label fallback is reached.
    """
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    blank = st.one_of(st.none(), st.just(""), st.just("   "))
    id_st = (
        st.sampled_from(transcript_id_pool)
        if transcript_id_pool
        else st.integers(min_value=1, max_value=500)
    )
    chunks: list[Document] = []
    for index in range(size):
        start_s = draw(
            st.one_of(
                st.none(),
                st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
            )
        )
        chunks.append(
            Document(
                page_content=draw(st.sampled_from(["The House resolved.", "Debate continued."])),
                metadata={
                    "chunk_id": draw(st.integers(min_value=1, max_value=10_000)),
                    "transcript_id": draw(id_st),
                    "speaker": draw(blank if blank_labels else _optional_value(_SPEAKERS)),
                    "start_s": start_s,
                    "end_s": None if start_s is None else start_s + 5.0,
                    "entity_names": draw(st.lists(_entity_value(_ENTITIES), max_size=3)),
                    "record_title": draw(
                        blank if blank_labels else _optional_value(_RECORD_TITLES)
                    ),
                    "sitting_title": draw(
                        blank if blank_labels else _optional_value(_SITTING_TITLES)
                    ),
                    "date": draw(blank if blank_labels else _optional_value(_DATES)),
                    "score": 1.0 / (index + 1),
                },
            )
        )
    return chunks


@st.composite
def value_free_chunks(draw: st.DrawFn) -> list[Document]:
    """Generate chunks whose suggestion-bearing metadata is all absent or blank."""
    size = draw(st.integers(min_value=0, max_value=4))
    blank = st.one_of(st.none(), st.just(""), st.just("   "))
    return [
        Document(
            page_content="The House resolved.",
            metadata={
                "chunk_id": draw(st.integers(min_value=1, max_value=10_000)),
                "transcript_id": draw(st.integers(min_value=1, max_value=500)),
                "speaker": draw(blank),
                "start_s": None,
                "end_s": None,
                "entity_names": draw(st.lists(st.sampled_from(["", "  "]), max_size=3)),
                "record_title": draw(_optional_value(_RECORD_TITLES)),
                "sitting_title": draw(blank),
                "date": draw(blank),
                "score": 1.0,
            },
        )
        for _ in range(size)
    ]


@st.composite
def corpus_hints(draw: st.DrawFn) -> CorpusHints | None:
    """Generate corpus hints, sometimes absent and sometimes empty."""
    if draw(st.booleans()):
        return None
    return CorpusHints(
        entities=draw(st.lists(_entity_value(_ENTITIES), max_size=3)),
        speakers=draw(st.lists(_entity_value(_SPEAKERS), max_size=3)),
    )


_exclude_st = st.lists(
    st.sampled_from(_UNRELATED_QUESTIONS + [text for text, _ in _STATIC_SUGGESTIONS]),
    max_size=4,
)
_count_st = st.integers(min_value=1, max_value=5)

# Narrow enough that any list of two or more chunks very often repeats an id,
# which is what makes the deduplication assertions bite.
_TRANSCRIPT_ID_POOL = [7, 11, 42]

_related_chunks_st = st.one_of(
    retrieved_chunks(max_size=6, transcript_id_pool=_TRANSCRIPT_ID_POOL),
    retrieved_chunks(max_size=6, transcript_id_pool=_TRANSCRIPT_ID_POOL, blank_labels=True),
)
_limit_st = st.integers(min_value=1, max_value=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_values(raw_values: list[str | None]) -> set[str]:
    """Collapse whitespace and drop blanks, mirroring the builder's cleaning."""
    cleaned = set()
    for raw in raw_values:
        if raw is None:
            continue
        collapsed = re.sub(r"\s+", " ", raw).strip()
        if collapsed:
            cleaned.add(collapsed)
    return cleaned


def _chunk_values(chunks: list[Document]) -> set[str]:
    """Every value the builder may name from chunk metadata."""
    raw: list[str | None] = []
    for chunk in chunks:
        meta = chunk.metadata
        raw.extend([meta.get("speaker"), meta.get("date"), meta.get("sitting_title")])
        raw.extend(meta.get("entity_names") or [])
    return _clean_values(raw)


def _hint_values(hints: CorpusHints | None) -> set[str]:
    """Every value the builder may name from corpus hints."""
    if hints is None:
        return set()
    return _clean_values([*hints.entities, *hints.speakers])


def _names_a_value(text: str, values: set[str]) -> bool:
    return any(value in text for value in values)


def _is_general(text: str) -> bool:
    """True when the text belongs to the static set or the generic filler stream."""
    key = normalise_question(text)
    return key in _STATIC_KEYS or _FILLER_PATTERN.match(key) is not None


def _expected_label(chunk: Document) -> str:
    """Re-derive the required label independently of the builder's own helper.

    Precedence per Requirement 4.2: `sitting_title` → `record_title` →
    `speaker` → `date`, first non-empty winning, with
    `Transcript {transcript_id}` as the guaranteed non-empty fallback.
    """
    meta = chunk.metadata
    for value in (
        meta.get("sitting_title"),
        meta.get("record_title"),
        meta.get("speaker"),
        meta.get("date"),
    ):
        if value is None:
            continue
        collapsed = re.sub(r"\s+", " ", value).strip()
        if collapsed:
            return collapsed
    return f"Transcript {meta['transcript_id']}"


def _first_chunk_by_transcript_id(chunks: list[Document]) -> dict[int, Document]:
    """Map each transcript_id to the earliest — highest-scoring — chunk carrying it."""
    first: dict[int, Document] = {}
    for chunk in chunks:
        first.setdefault(chunk.metadata["transcript_id"], chunk)
    return first


# ---------------------------------------------------------------------------
# Property 3: Builder suggestions are grounded in available data
# ---------------------------------------------------------------------------


@given(
    chunks=retrieved_chunks(),
    hints=corpus_hints(),
    exclude=_exclude_st,
    count=_count_st,
)
@settings(max_examples=300)
def test_suggestions_are_grounded_in_available_data(
    chunks: list[Document],
    hints: CorpusHints | None,
    exclude: list[str],
    count: int,
) -> None:
    """Every suggestion either names a value from the data or is a general prompt.

    For ANY chunk list and ANY corpus hints, `suggestions()` returns exactly
    `count` well-formed, distinct, non-excluded items; where either source
    supplies at least one value, at least one returned item names a value drawn
    from that data, and no item names a value the data does not contain.

    **Validates: Requirements 3.1, 3.3**
    """
    builder = RecommendationBuilder()

    items = builder.suggestions(
        chunks=chunks,
        corpus_hints=hints,
        exclude=exclude,
        count=count,
    )

    assert len(items) == count, f"Expected {count} suggestions, got {len(items)}"

    for item in items:
        assert item.text.strip(), f"Empty suggestion text in {items}"
        assert item.reason.strip(), f"Empty suggestion reason for {item.text!r}"

    keys = [normalise_question(item.text) for item in items]
    assert len(set(keys)) == len(keys), f"Duplicate suggestions after normalisation: {keys}"

    excluded_keys = {normalise_question(text) for text in exclude}
    assert not (set(keys) & excluded_keys), (
        f"Excluded question returned: {set(keys) & excluded_keys}"
    )

    values = _chunk_values(chunks) | _hint_values(hints)

    for item in items:
        assert _names_a_value(item.text, values) or _is_general(item.text), (
            f"Suggestion {item.text!r} names no value from the data "
            f"{sorted(values)} and is not a general prompt"
        )

    if values:
        assert any(_names_a_value(item.text, values) for item in items), (
            f"Data supplied values {sorted(values)} but no suggestion names one: "
            f"{[item.text for item in items]}"
        )


@given(
    chunks=retrieved_chunks(min_size=1),
    hints=corpus_hints(),
    exclude=_exclude_st,
    count=_count_st,
)
@settings(max_examples=300)
def test_chunk_metadata_takes_precedence_over_corpus_hints(
    chunks: list[Document],
    hints: CorpusHints | None,
    exclude: list[str],
    count: int,
) -> None:
    """Chunk metadata is mined before corpus hints.

    WHERE the chunks carry at least one entity, speaker, date, or sitting title,
    the first returned suggestion names a value drawn from the chunks rather
    than from the corpus hints.

    **Validates: Requirements 3.1**
    """
    chunk_values = _chunk_values(chunks)
    if not chunk_values:
        return

    builder = RecommendationBuilder()
    items = builder.suggestions(
        chunks=chunks,
        corpus_hints=hints,
        exclude=exclude,
        count=count,
    )

    assert _names_a_value(items[0].text, chunk_values), (
        f"First suggestion {items[0].text!r} names no chunk value from {sorted(chunk_values)}"
    )


@given(chunks=value_free_chunks(), exclude=_exclude_st, count=_count_st)
@settings(max_examples=300)
def test_general_suggestions_when_no_values_available(
    chunks: list[Document],
    exclude: list[str],
    count: int,
) -> None:
    """With no usable chunk metadata and no hints, every suggestion is general.

    IF neither the chunks nor the corpus hints supply a value, THEN the builder
    returns general parliamentary research prompts rather than naming anything.

    **Validates: Requirements 3.3**
    """
    builder = RecommendationBuilder()

    for hints in (None, CorpusHints(), CorpusHints(entities=["", "  "], speakers=[""])):
        items = builder.suggestions(
            chunks=chunks,
            corpus_hints=hints,
            exclude=exclude,
            count=count,
        )

        assert len(items) == count
        for item in items:
            assert _is_general(item.text), (
                f"Expected a general suggestion with no data available, got {item.text!r}"
            )


# ---------------------------------------------------------------------------
# Property 4: Related records are deduplicated, labelled, and bounded
# ---------------------------------------------------------------------------


@given(chunks=_related_chunks_st)
@settings(max_examples=300)
def test_related_records_are_deduplicated_labelled_and_bounded(
    chunks: list[Document],
) -> None:
    """Records are capped, one per transcript, labelled, and carry `start_s`.

    For ANY chunk list, `related_records()` returns at most
    `MAX_RELATED_RECORDS` items with distinct `transcript_id` values, taken in
    the relevance order given so the highest-scoring chunk wins its id. Each
    label is non-empty and follows the required precedence, and each `start_s`
    is exactly the source chunk's value — present when the chunk has one, None
    when it does not.

    **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
    """
    builder = RecommendationBuilder()

    records = builder.related_records(chunks=chunks)

    assert len(records) <= MAX_RELATED_RECORDS, (
        f"Expected at most {MAX_RELATED_RECORDS} records, got {len(records)}"
    )

    transcript_ids = [record.transcript_id for record in records]
    assert len(set(transcript_ids)) == len(transcript_ids), (
        f"Duplicate transcript_id in related records: {transcript_ids}"
    )

    first_by_id = _first_chunk_by_transcript_id(chunks)
    expected_ids = list(first_by_id)[:MAX_RELATED_RECORDS]
    assert transcript_ids == expected_ids, (
        f"Records not in relevance order of first appearance: {transcript_ids} != {expected_ids}"
    )
    assert len(records) == min(MAX_RELATED_RECORDS, len(first_by_id))

    for record in records:
        source = first_by_id[record.transcript_id]

        assert record.chunk_id == source.metadata["chunk_id"], (
            f"Record for transcript {record.transcript_id} was derived from chunk "
            f"{record.chunk_id}, not the highest-scoring chunk {source.metadata['chunk_id']}"
        )
        assert record.label.strip(), f"Empty label on record {record}"
        assert record.label == _expected_label(source), (
            f"Label {record.label!r} does not follow the required precedence, "
            f"expected {_expected_label(source)!r}"
        )
        assert record.start_s == source.metadata.get("start_s"), (
            f"start_s {record.start_s!r} does not match source chunk "
            f"{source.metadata.get('start_s')!r}"
        )
        if source.metadata.get("start_s") is None:
            assert record.start_s is None
        else:
            assert record.start_s is not None


@given(chunks=_related_chunks_st, limit=_limit_st)
@settings(max_examples=300)
def test_related_records_respect_an_explicit_limit(
    chunks: list[Document],
    limit: int,
) -> None:
    """An explicit `limit` bounds the result to the distinct transcripts available.

    **Validates: Requirements 4.1, 4.4**
    """
    builder = RecommendationBuilder()

    records = builder.related_records(chunks=chunks, limit=limit)

    distinct_ids = len({chunk.metadata["transcript_id"] for chunk in chunks})
    assert len(records) == min(limit, distinct_ids), (
        f"Expected {min(limit, distinct_ids)} records for limit {limit} over "
        f"{distinct_ids} distinct transcripts, got {len(records)}"
    )


@given(limit=_limit_st)
@settings(max_examples=100)
def test_empty_chunk_list_yields_no_related_records(limit: int) -> None:
    """WHERE no chunks are available, the related-record collection is empty.

    **Validates: Requirements 4.9**
    """
    builder = RecommendationBuilder()

    assert builder.related_records(chunks=[]) == []
    assert builder.related_records(chunks=[], limit=limit) == []
