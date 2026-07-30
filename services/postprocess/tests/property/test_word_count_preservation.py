"""Property tests for non-joining Word-count preservation.

Validates: Requirements 5.4, 15.2

These tests verify that when `correct_words` processes a Word list and NO
multi-token joins occur, the returned Word count equals the input Word count.

The key insight: if the dataset contains only single-word canonicals (no
multi-word entities), then no n-gram joining can ever fire, so the word
count must be preserved. Single-token corrections may change the *text*
of a word, but never the count.

Properties tested:
1. With a dataset of single-word-only entities, the output word count
   always equals the input word count regardless of input content.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from app.correction.engine import correct_words
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex, build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def word_list_no_joins(draw: st.DrawFn) -> list[dict]:
    """Generate a list of word dicts with monotonic timing.

    Uses a broad word pool — the fixture dataset is constructed so that
    no multi-word entities exist, meaning joins are impossible regardless
    of the input words.
    """
    size = draw(st.integers(min_value=1, max_value=20))
    words: list[dict] = []
    cursor = 0.0

    # Broad word pool: some triggering single-token corrections, some not
    word_pool = [
        "the", "of", "and", "in", "to", "for", "with",
        "hello", "world", "test", "data", "people",
        "Kumasi", "kumase", "Accra", "acra", "Tamale",
        "ndc", "npp", "cpp",
        "president", "minister", "parliament", "Ghana",
        "speaker", "member", "committee", "house",
        "region", "district", "assembly", "chief",
        "yesterday", "tomorrow", "today", "morning",
        "fiscal", "budget", "economic", "policy",
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


# ---------------------------------------------------------------------------
# Fixture: single-word-only dataset (no multi-word entities → no joins)
# ---------------------------------------------------------------------------


def _build_no_join_snapshot() -> tuple[MatchIndex, DatasetSnapshot]:
    """Build a dataset with ONLY single-word canonical entities.

    Since all canonicals are single words, the n-gram joining paths (n=2, n=3)
    can never produce a match that merges multiple tokens into one output word.
    This guarantees that len(output) == len(input) for any input.
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
            canonical="Bolgatanga",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["bolga", "bolgat"],
            source="cities",
            source_rank=1,
            active=True,
        ),
        EntityRecord(
            canonical="Sunyani",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["sunyane", "sunyanee"],
            source="cities",
            source_rank=1,
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
    ]

    records.sort(
        key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical)
    )
    index = build_index(records)

    snapshot = DatasetSnapshot(
        version="test-no-join",
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
_NO_JOIN_INDEX, _NO_JOIN_SNAPSHOT = _build_no_join_snapshot()


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(words=word_list_no_joins())
@settings(max_examples=300)
def test_word_count_preserved_when_no_joins(words: list[dict]) -> None:
    """Output word count equals input word count when no joins are possible.

    With a dataset containing only single-word canonicals, multi-token
    joining (n=2, n=3 windows) can never fire because there are no
    multi-word entries in the canonical_map or fused_map to match against.
    Therefore, the correction engine may only replace individual word text
    (single-token corrections) but never merge tokens, so:

        len(result.words) == len(input_words)

    **Validates: Requirements 5.4, 15.2**
    """
    input_count = len(words)

    result = correct_words(
        words,
        _NO_JOIN_INDEX,
        _NO_JOIN_SNAPSHOT,
        word_accept_threshold=0.90,
        min_confidence=0.75,
    )

    output_count = len(result.words)

    assert output_count == input_count, (
        f"Word count changed: input={input_count}, output={output_count}. "
        f"Input words: {[w['word'] for w in words]}, "
        f"Output words: {[w.get('word') for w in result.words]}"
    )
