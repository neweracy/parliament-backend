"""Property tests for the correction no-op property.

Validates: Requirements 1.8

These tests verify that for all generated transcripts containing no entity
from any Dataset_Source and no spoken year, the corrected transcript is
byte-identical to the input transcript.

This is a fundamental property of a well-behaved correction engine: if the
input contains nothing that should be corrected, the output must be identical
to the input. The engine should never introduce spurious changes on clean
text.

Properties tested:
1. correct_text no-op: text with no entities or year patterns passes through unchanged.
2. correct_words no-op: word list with no entities or year patterns passes through unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from app.correction.engine import correct_text, correct_words
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Safe word pool — common English words that will NOT trigger any corrections
# ---------------------------------------------------------------------------

SAFE_WORDS = [
    "hello", "world", "today", "weather", "happy", "cloud",
    "building", "morning", "evening",
    "water", "bread", "chair", "window", "door", "floor",
    "computer", "keyboard", "science", "library",
]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def safe_transcript_text(draw: st.DrawFn) -> str:
    """Generate a transcript from safe words only.

    The generated text contains no Ghana entity names, no block-list words
    that could interact with the engine, and no year-related words.
    """
    num_words = draw(st.integers(min_value=1, max_value=20))
    selected = draw(
        st.lists(
            st.sampled_from(SAFE_WORDS),
            min_size=num_words,
            max_size=num_words,
        )
    )
    return " ".join(selected)


@st.composite
def safe_word_list(draw: st.DrawFn) -> list[dict]:
    """Generate a word list from safe words only.

    Each word dict has word, start, end, confidence fields matching
    the format expected by correct_words.
    """
    size = draw(st.integers(min_value=1, max_value=20))
    words: list[dict] = []
    cursor = 0.0

    for _ in range(size):
        word_text = draw(st.sampled_from(SAFE_WORDS))
        duration = draw(st.floats(min_value=0.2, max_value=0.5))
        gap = draw(st.floats(min_value=0.0, max_value=0.1))

        start = cursor + gap
        end = start + duration
        cursor = end

        words.append({
            "word": word_text,
            "start": round(start, 3),
            "end": round(end, 3),
            "confidence": 0.95,
        })

    return words


# ---------------------------------------------------------------------------
# Fixture: a realistic Ghana dataset (same entities as idempotence tests)
# ---------------------------------------------------------------------------


def _build_noop_snapshot() -> tuple[MatchIndex, DatasetSnapshot]:
    """Build a fixed dataset with realistic Ghana entities.

    The entities here represent what the engine WOULD correct if encountered.
    None of the safe words in the pool should match any of these entities.
    """
    records = [
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["kumase", "kumsi"],
            source="cities",
            source_rank=1,
            active=True,
        ),
        EntityRecord(
            canonical="Accra",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["acra", "akra"],
            source="cities",
            source_rank=1,
            active=True,
        ),
        EntityRecord(
            canonical="Tamale",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["tamaale", "tamali"],
            source="cities",
            source_rank=1,
            active=True,
        ),
        EntityRecord(
            canonical="Cape Coast",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["cape coast", "capecoast"],
            source="cities",
            source_rank=1,
            active=True,
        ),
        EntityRecord(
            canonical="Ningo-Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=["ningo prampram", "ningoprampram", "ningo-prampram"],
            source="supplementary",
            source_rank=2,
            active=True,
        ),
        EntityRecord(
            canonical="NDC",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["ndc"],
            source="all_parties",
            source_rank=5,
            active=True,
        ),
        EntityRecord(
            canonical="NPP",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["npp"],
            source="all_parties",
            source_rank=5,
            active=True,
        ),
        EntityRecord(
            canonical="Nana Akufo-Addo",
            entity_kind=EntityKind.person,
            entity_type=EntityType.president,
            aliases=["nana akufo addo", "akufo addo", "akufo-addo"],
            source="all_persons",
            source_rank=3,
            active=True,
        ),
        EntityRecord(
            canonical="Mahamudu Bawumia",
            entity_kind=EntityKind.person,
            entity_type=EntityType.president,
            aliases=["bawumia", "bawumiah", "mahamudu bawumia"],
            source="all_persons",
            source_rank=3,
            active=True,
        ),
    ]

    records.sort(
        key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical)
    )
    index = build_index(records)

    snapshot = DatasetSnapshot(
        version="test-noop",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(timezone.utc),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset({"the", "of", "and", "in", "to", "for", "with"}),
        word_stopwords=frozenset({"the", "of", "and", "in", "to", "for", "with", "a"}),
        title_prefixes=frozenset({"hon", "honorable", "honourable", "mr", "mrs", "dr"}),
    )

    return index, snapshot


# Build once — the snapshot is immutable
_FIXED_INDEX, _FIXED_SNAPSHOT = _build_noop_snapshot()


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(text=safe_transcript_text())
@settings(max_examples=100)
def test_correct_text_noop(text: str) -> None:
    """Correcting entity-free, year-free text produces byte-identical output.

    For ANY transcript composed entirely of safe words (no Ghana entities,
    no year patterns), running correct_text should return the input text
    unchanged, with zero corrections.

    **Validates: Requirements 1.8**
    """
    result = correct_text(
        text,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        min_confidence=0.75,
    )

    assert result.text == text, (
        f"correct_text altered entity-free input.\n"
        f"Input:  '{text}'\n"
        f"Output: '{result.text}'\n"
        f"Corrections: {[(c.original, c.replacement) for c in result.corrections]}"
    )

    assert len(result.corrections) == 0, (
        f"correct_text produced corrections on entity-free input.\n"
        f"Input: '{text}'\n"
        f"Corrections: {[(c.original, c.replacement) for c in result.corrections]}"
    )


@given(words=safe_word_list())
@settings(max_examples=100)
def test_correct_words_noop(words: list[dict]) -> None:
    """Correcting entity-free, year-free words produces byte-identical output.

    For ANY word list composed entirely of safe words (no Ghana entities,
    no year patterns), running correct_words should return the same words
    unchanged, with zero corrections and no entity annotations.

    **Validates: Requirements 1.8**
    """
    result = correct_words(
        words,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        word_accept_threshold=0.90,
        min_confidence=0.75,
    )

    # Word count must be identical
    assert len(result.words) == len(words), (
        f"correct_words changed word count on entity-free input.\n"
        f"Input count:  {len(words)}\n"
        f"Output count: {len(result.words)}\n"
        f"Input words:  {[w['word'] for w in words]}\n"
        f"Output words: {[w['word'] for w in result.words]}"
    )

    # Each word text must be byte-identical
    input_texts = [w["word"] for w in words]
    output_texts = [w["word"] for w in result.words]

    assert output_texts == input_texts, (
        f"correct_words altered word texts on entity-free input.\n"
        f"Input:  {input_texts}\n"
        f"Output: {output_texts}"
    )

    # No corrections should be produced
    assert len(result.corrections) == 0, (
        f"correct_words produced corrections on entity-free input.\n"
        f"Input: {[w['word'] for w in words]}\n"
        f"Corrections: {result.corrections}"
    )

    # No entity annotations should be present on any word
    for i, w in enumerate(result.words):
        assert w.get("locationCorrected") is None or w.get("locationCorrected") is False, (
            f"Word at index {i} was annotated as corrected on entity-free input.\n"
            f"Word: '{w['word']}'\n"
            f"Full input: {[wd['word'] for wd in words]}"
        )
