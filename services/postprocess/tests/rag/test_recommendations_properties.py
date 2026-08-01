"""Property tests for RecommendationBuilder suggestion grounding.

Validates: Requirements 3.1, 3.3

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
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.recommendations import (
    _STATIC_SUGGESTIONS,
    CorpusHints,
    RecommendationBuilder,
    normalise_question,
)
from app.rag.retrieval import RetrievedChunk

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
def retrieved_chunks(draw: st.DrawFn, min_size: int = 0, max_size: int = 4) -> list[RetrievedChunk]:
    """Generate a relevance-ordered chunk list with sparse metadata."""
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    chunks: list[RetrievedChunk] = []
    for index in range(size):
        start_s = draw(
            st.one_of(
                st.none(),
                st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
            )
        )
        chunks.append(
            RetrievedChunk(
                chunk_id=draw(st.integers(min_value=1, max_value=10_000)),
                text=draw(st.sampled_from(["The House resolved.", "Debate continued."])),
                relevance_score=1.0 / (index + 1),
                transcript_id=draw(st.integers(min_value=1, max_value=500)),
                speaker=draw(_optional_value(_SPEAKERS)),
                start_s=start_s,
                end_s=None if start_s is None else start_s + 5.0,
                matched_entities=draw(st.lists(_entity_value(_ENTITIES), max_size=3)),
                record_title=draw(_optional_value(_RECORD_TITLES)),
                sitting_title=draw(_optional_value(_SITTING_TITLES)),
                date=draw(_optional_value(_DATES)),
            )
        )
    return chunks


@st.composite
def value_free_chunks(draw: st.DrawFn) -> list[RetrievedChunk]:
    """Generate chunks whose suggestion-bearing metadata is all absent or blank."""
    size = draw(st.integers(min_value=0, max_value=4))
    blank = st.one_of(st.none(), st.just(""), st.just("   "))
    return [
        RetrievedChunk(
            chunk_id=draw(st.integers(min_value=1, max_value=10_000)),
            text="The House resolved.",
            relevance_score=1.0,
            transcript_id=draw(st.integers(min_value=1, max_value=500)),
            speaker=draw(blank),
            start_s=None,
            end_s=None,
            matched_entities=draw(st.lists(st.sampled_from(["", "  "]), max_size=3)),
            record_title=draw(_optional_value(_RECORD_TITLES)),
            sitting_title=draw(blank),
            date=draw(blank),
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


def _chunk_values(chunks: list[RetrievedChunk]) -> set[str]:
    """Every value the builder may name from chunk metadata."""
    raw: list[str | None] = []
    for chunk in chunks:
        raw.extend([chunk.speaker, chunk.date, chunk.sitting_title])
        raw.extend(chunk.matched_entities or [])
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
    chunks: list[RetrievedChunk],
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
    chunks: list[RetrievedChunk],
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
    chunks: list[RetrievedChunk],
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
