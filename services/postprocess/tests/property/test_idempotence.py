"""Property tests for correction idempotence.

Validates: Requirements 1.7

These tests verify that applying the Correction_Engine to its own output
produces the same output. Formally: f(f(x)) == f(x) where f is the
correction function.

This is a fundamental property of a well-behaved correction pipeline:
once text or words have been corrected, running the same correction again
should not change anything further. Corrections should be stable (fixed
points of the function).

Properties tested:
1. correct_words idempotence: running correct_words on its own output
   produces identical words, corrections, and entities.
2. correct_text idempotence: running correct_text on its own output
   produces identical text.
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
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def word_list_for_idempotence(draw: st.DrawFn) -> list[dict]:
    """Generate a list of word dicts for idempotence testing.

    Uses a broad word pool including words that may trigger corrections
    and words that won't, so we exercise both correction and passthrough
    paths.
    """
    size = draw(st.integers(min_value=1, max_value=15))
    words: list[dict] = []
    cursor = 0.0

    word_pool = [
        "the", "of", "and", "in", "to", "for",
        "hello", "world", "test", "data",
        "Kumasi", "kumase", "Accra", "acra", "Tamale",
        "ndc", "npp", "cpp",
        "president", "minister", "parliament", "Ghana",
        "Cape", "Coast", "Ningo", "Prampram",
        "speaker", "committee", "house", "region",
        "budget", "policy", "national", "economic",
        "Bawumia", "bawumiah", "Akufo", "Addo",
    ]

    for _ in range(size):
        word_text = draw(st.sampled_from(word_pool))
        duration = draw(st.floats(min_value=0.1, max_value=1.0))
        gap = draw(st.floats(min_value=0.0, max_value=0.2))

        start = cursor + gap
        end = start + duration
        cursor = end

        words.append({
            "word": word_text,
            "start": round(start, 3),
            "end": round(end, 3),
            "confidence": draw(st.floats(min_value=0.5, max_value=1.0)),
        })

    return words


@st.composite
def transcript_text_for_idempotence(draw: st.DrawFn) -> str:
    """Generate transcript text for idempotence testing.

    Produces sentences mixing words that may trigger corrections with
    words that won't. This exercises the correct_text path.
    """
    word_pool = [
        "the", "of", "and", "in", "to", "for",
        "hello", "world", "test", "people",
        "Kumasi", "kumase", "Accra", "acra", "Tamale",
        "ndc", "npp", "cpp",
        "president", "minister", "parliament", "Ghana",
        "Cape", "Coast", "Ningo", "Prampram",
        "speaker", "committee", "national", "economic",
        "Bawumia", "bawumiah", "Akufo", "Addo",
    ]

    num_words = draw(st.integers(min_value=1, max_value=20))
    selected = draw(
        st.lists(
            st.sampled_from(word_pool),
            min_size=num_words,
            max_size=num_words,
        )
    )
    return " ".join(selected)


# ---------------------------------------------------------------------------
# Fixture: a realistic Ghana dataset
# ---------------------------------------------------------------------------


def _build_idempotence_snapshot() -> tuple[MatchIndex, DatasetSnapshot]:
    """Build a fixed dataset with realistic Ghana entities.

    Includes multi-word entities, parties, persons, and single-word
    locations to exercise all correction paths.
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
            canonical="CPP",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["cpp"],
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
        version="test-idempotence",
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
_FIXED_INDEX, _FIXED_SNAPSHOT = _build_idempotence_snapshot()


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(words=word_list_for_idempotence())
@settings(max_examples=100)
def test_correct_words_idempotence(words: list[dict]) -> None:
    """Applying correct_words to its own output produces the same output.

    For ANY valid word list, running correct_words twice should produce
    the same result as running it once. The first pass corrects everything
    that needs correcting; the second pass should find nothing new to
    correct since all outputs are now either identity matches or
    already in their canonical form.

    Formally: correct_words(correct_words(x)) == correct_words(x)

    We compare:
    - The word text in each output position
    - The locationCorrected flags
    - The entityKind and entityType annotations

    **Validates: Requirements 1.7**
    """
    # First pass
    result1 = correct_words(
        words,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        word_accept_threshold=0.90,
        min_confidence=0.75,
    )

    # Second pass: feed the first-pass output back in
    result2 = correct_words(
        result1.words,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        word_accept_threshold=0.90,
        min_confidence=0.75,
    )

    # The word texts should be identical
    words1 = [w.get("word", "") for w in result1.words]
    words2 = [w.get("word", "") for w in result2.words]

    assert words1 == words2, (
        f"correct_words is not idempotent on word text.\n"
        f"First pass output:  {words1}\n"
        f"Second pass output: {words2}\n"
        f"Original input: {[w['word'] for w in words]}"
    )

    # The word count should be identical
    assert len(result1.words) == len(result2.words), (
        f"correct_words is not idempotent on word count.\n"
        f"First pass count:  {len(result1.words)}\n"
        f"Second pass count: {len(result2.words)}"
    )

    # The correction flags should be stable
    flags1 = [
        (w.get("locationCorrected"), w.get("entityKind"), w.get("entityType"))
        for w in result1.words
    ]
    flags2 = [
        (w.get("locationCorrected"), w.get("entityKind"), w.get("entityType"))
        for w in result2.words
    ]

    assert flags1 == flags2, (
        f"correct_words is not idempotent on correction flags.\n"
        f"First pass flags:  {flags1}\n"
        f"Second pass flags: {flags2}"
    )

    # The second pass should produce no NEW corrections
    assert len(result2.corrections) == 0, (
        f"Second pass produced {len(result2.corrections)} new corrections "
        f"(should be 0 for idempotent output).\n"
        f"Second-pass corrections: {result2.corrections}\n"
        f"Original input: {[w['word'] for w in words]}"
    )


@given(text=transcript_text_for_idempotence())
@settings(max_examples=100)
def test_correct_text_idempotence(text: str) -> None:
    """Applying correct_text to its own output produces the same text.

    For ANY valid transcript text, running correct_text twice should
    produce the same text as running it once. The first pass applies all
    applicable corrections; the second pass should find the text already
    in its corrected canonical form and make no further changes.

    Formally: correct_text(correct_text(x).text).text == correct_text(x).text

    **Validates: Requirements 1.7**
    """
    # First pass
    result1 = correct_text(
        text,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        min_confidence=0.75,
    )

    # Second pass: feed the corrected text back in
    result2 = correct_text(
        result1.text,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        min_confidence=0.75,
    )

    assert result1.text == result2.text, (
        f"correct_text is not idempotent.\n"
        f"Original input:    '{text}'\n"
        f"First pass output: '{result1.text}'\n"
        f"Second pass output: '{result2.text}'\n"
        f"First pass corrections: {[(c.original, c.replacement) for c in result1.corrections]}\n"
        f"Second pass corrections: {[(c.original, c.replacement) for c in result2.corrections]}"
    )

    # The second pass should produce no corrections
    assert len(result2.corrections) == 0, (
        f"Second pass produced {len(result2.corrections)} corrections "
        f"(should be 0 for idempotent output).\n"
        f"Second-pass corrections: {[(c.original, c.replacement) for c in result2.corrections]}\n"
        f"Original input: '{text}'"
    )
