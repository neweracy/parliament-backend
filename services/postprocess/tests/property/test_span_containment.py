"""Property tests for span containment.

Validates: Requirements 5.7

These tests verify that the total span covered by returned Words from
correct_words never exceeds the span from the first input Word's `start`
to the last input Word's `end`. This is the "N-gram windows and
joined-span timing" invariant from the design: merging tokens takes the
`start` from the first and the `end` from the last joined Word, so the
output time range can only shrink or stay the same — never grow.

Properties tested:
1. Output span start >= input span start (first output word starts no
   earlier than the first input word).
2. Output span end <= input span end (last output word ends no later
   than the last input word).
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

# Generate monotonically increasing start/end times for Words
@st.composite
def monotonic_word_list(draw: st.DrawFn) -> list[dict]:
    """Generate a list of word dicts with monotonically increasing timing.

    Each word has start < end, and start[i] >= end[i-1] to maintain
    monotonic ordering (no overlapping words).
    """
    size = draw(st.integers(min_value=1, max_value=15))
    words: list[dict] = []
    cursor = draw(st.floats(min_value=0.0, max_value=10.0))

    # A pool of word strings that include some that could trigger
    # corrections (names, locations) and some that won't
    word_pool = [
        "the", "of", "and", "in", "to",
        "Kumasi", "Accra", "Tamale", "Cape", "Coast",
        "president", "minister", "Ghana", "parliament",
        "Nana", "Akufo", "Addo", "Bawumia",
        "ndc", "npp", "cpp",
        "hello", "world", "test", "data",
    ]

    word_text = draw(
        st.lists(
            st.sampled_from(word_pool),
            min_size=size,
            max_size=size,
        )
    )

    for i in range(size):
        duration = draw(st.floats(min_value=0.1, max_value=1.5))
        gap = draw(st.floats(min_value=0.0, max_value=0.3))

        start = cursor + gap
        end = start + duration
        cursor = end

        words.append({
            "word": word_text[i],
            "start": round(start, 3),
            "end": round(end, 3),
            "confidence": draw(
                st.floats(min_value=0.5, max_value=1.0)
            ),
        })

    return words


def _build_fixed_snapshot() -> tuple[MatchIndex, DatasetSnapshot]:
    """Build a small fixed dataset that can trigger some corrections.

    Uses a handful of known Ghana entities so that correct_words has
    real matches to exercise the n-gram joining and span merging paths.
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
            aliases=["nana akufo addo", "akufo addo"],
            source="all_persons",
            source_rank=3,
            active=True,
        ),
        EntityRecord(
            canonical="Mahamudu Bawumia",
            entity_kind=EntityKind.person,
            entity_type=EntityType.president,
            aliases=["bawumia", "bawumiah"],
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
        version="test-span",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(timezone.utc),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset({"the", "of", "and", "in", "to"}),
        word_stopwords=frozenset({"the", "of", "and", "in", "to", "a"}),
        title_prefixes=frozenset({"hon", "honorable", "honourable", "mr", "mrs", "dr"}),
    )

    return index, snapshot


# Build once for all tests — the snapshot is immutable
_FIXED_INDEX, _FIXED_SNAPSHOT = _build_fixed_snapshot()


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(words=monotonic_word_list())
@settings(max_examples=300)
def test_span_containment_output_within_input_bounds(
    words: list[dict],
) -> None:
    """Output span never exceeds input span boundaries.

    For ANY valid list of Words with monotonically increasing start/end
    times, after running correct_words, the output satisfies:
      - output_words[0].start >= input_words[0].start
      - output_words[-1].end <= input_words[-1].end

    This proves that the n-gram joining logic (which merges multiple
    tokens into one with start from the first and end from the last)
    cannot produce timing that extends beyond the original input span.

    **Validates: Requirements 5.7**
    """
    input_start = words[0]["start"]
    input_end = words[-1]["end"]

    result = correct_words(
        words,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        word_accept_threshold=0.90,
        min_confidence=0.75,
    )

    output_words = result.words

    # If output is empty (shouldn't happen for non-empty input, but guard)
    if not output_words:
        return

    output_start = output_words[0].get("start")
    output_end = output_words[-1].get("end")

    # Start of output must not precede start of input
    if output_start is not None:
        assert output_start >= input_start, (
            f"Output span start ({output_start}) is earlier than "
            f"input span start ({input_start}). "
            f"Input words: {[w['word'] for w in words]}, "
            f"Output words: {[w.get('word') for w in output_words]}"
        )

    # End of output must not exceed end of input
    if output_end is not None:
        assert output_end <= input_end, (
            f"Output span end ({output_end}) exceeds "
            f"input span end ({input_end}). "
            f"Input words: {[w['word'] for w in words]}, "
            f"Output words: {[w.get('word') for w in output_words]}"
        )
