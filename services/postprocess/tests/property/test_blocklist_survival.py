"""Property tests for Block_List survival.

Validates: Requirements 4.1, 15.5

These tests verify that every Block_List token survives the Correction_Engine
unchanged, regardless of what arbitrary surrounding context it is placed in.
The Block_List is the set of tokens (e.g. "general", "page", "nation",
"national") that must NEVER be corrected by the engine.

The key scenario guarded against: a Block_List token like "general" might
fuzzy-match against a location like "Genaral" or similar — the Block_List
guard inside match_fuzzy must prevent that correction.

Properties tested:
1. Block_List tokens survive correct_words unchanged for any surrounding
   word context.
2. Block_List tokens survive correct_text unchanged for any surrounding
   text context.
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
# Block_List tokens under test
# ---------------------------------------------------------------------------

BLOCK_LIST_TOKENS = ["general", "page", "nation", "national"]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A pool of arbitrary words that could surround a Block_List token
_CONTEXT_WORDS = [
    "the", "of", "and", "in", "to", "is", "was", "that", "for", "it",
    "Kumasi", "Accra", "Tamale", "Ghana", "president", "minister",
    "parliament", "committee", "report", "debate", "house", "speaker",
    "members", "today", "yesterday", "morning", "evening",
    "said", "asked", "noted", "moved", "seconded",
    "first", "second", "third", "new", "old",
    "Mr", "Mrs", "Dr", "Honourable", "Chairman",
]


@st.composite
def words_with_blocklist_token(draw: st.DrawFn) -> tuple[list[dict], list[str]]:
    """Generate a word list containing one or more Block_List tokens at arbitrary positions.

    Returns a tuple of (word_dicts, expected_blocklist_tokens_in_order) where
    the second element lists the block_list tokens that appear in the input,
    in the order they appear, so we can verify they survive unchanged.
    """
    # Decide how many total words (including block_list tokens)
    total_size = draw(st.integers(min_value=1, max_value=12))

    # Pick how many block_list tokens to insert (at least 1)
    num_blocked = draw(st.integers(min_value=1, max_value=min(3, total_size)))

    # Generate positions for the block_list tokens
    positions = sorted(draw(
        st.lists(
            st.integers(min_value=0, max_value=total_size - 1),
            min_size=num_blocked,
            max_size=num_blocked,
            unique=True,
        )
    ))

    # Pick which block_list tokens to use at those positions
    blocked_tokens = draw(
        st.lists(
            st.sampled_from(BLOCK_LIST_TOKENS),
            min_size=num_blocked,
            max_size=num_blocked,
        )
    )

    # Build the word list with timing
    words: list[dict] = []
    blocked_in_order: list[str] = []
    cursor = 0.0
    blocked_idx = 0

    for i in range(total_size):
        if blocked_idx < num_blocked and i == positions[blocked_idx]:
            # This position gets a block_list token
            word_text = blocked_tokens[blocked_idx]
            blocked_in_order.append(word_text)
            blocked_idx += 1
        else:
            # This position gets a random context word
            word_text = draw(st.sampled_from(_CONTEXT_WORDS))

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

    return words, blocked_in_order


@st.composite
def text_with_blocklist_token(draw: st.DrawFn) -> tuple[str, list[str]]:
    """Generate a text string containing one or more Block_List tokens in arbitrary context.

    Returns (text, list_of_blocklist_tokens_in_text).
    """
    # Decide total token count
    total_size = draw(st.integers(min_value=1, max_value=15))

    # Pick how many block_list tokens to insert
    num_blocked = draw(st.integers(min_value=1, max_value=min(3, total_size)))

    # Generate positions for block_list tokens
    positions = set(draw(
        st.lists(
            st.integers(min_value=0, max_value=total_size - 1),
            min_size=num_blocked,
            max_size=num_blocked,
            unique=True,
        )
    ))

    # Pick which block_list tokens to use
    blocked_tokens = draw(
        st.lists(
            st.sampled_from(BLOCK_LIST_TOKENS),
            min_size=num_blocked,
            max_size=num_blocked,
        )
    )

    # Build the text
    tokens: list[str] = []
    blocked_in_order: list[str] = []
    blocked_idx = 0

    for i in range(total_size):
        if i in positions:
            token = blocked_tokens[blocked_idx]
            blocked_in_order.append(token)
            blocked_idx += 1
        else:
            token = draw(st.sampled_from(_CONTEXT_WORDS))
        tokens.append(token)

    text = " ".join(tokens)
    return text, blocked_in_order


# ---------------------------------------------------------------------------
# Fixed dataset with entities that could potentially match Block_List tokens
# ---------------------------------------------------------------------------


def _build_blocklist_test_snapshot() -> tuple[MatchIndex, DatasetSnapshot]:
    """Build a dataset containing entities that could fuzzy-match Block_List tokens.

    This proves the Block_List guard works: without it, "general" might match
    "Genaral" (a hypothetical misspelling) or a location like "General Santos",
    "page" might match "Paga", "nation" might match "Nanton", etc.
    """
    records = [
        # Locations that are phonetically or edit-distance close to Block_List tokens
        EntityRecord(
            canonical="Paga",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["paga", "pagah"],
            source="cities",
            source_rank=1,
            active=True,
        ),
        EntityRecord(
            canonical="Nanton",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["nanton", "nantown"],
            source="cities",
            source_rank=1,
            active=True,
        ),
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
        # A person entity — does NOT include block_list words in aliases
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
    ]

    records.sort(
        key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical)
    )
    index = build_index(records)

    snapshot = DatasetSnapshot(
        version="test-blocklist",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(timezone.utc),
        index=index,
        block_list=frozenset(BLOCK_LIST_TOKENS),
        stopwords=frozenset({"the", "of", "and", "in", "to", "is", "was", "that", "for", "it"}),
        word_stopwords=frozenset({"the", "of", "and", "in", "to", "is", "was", "that", "for", "it", "a"}),
        title_prefixes=frozenset({"hon", "honorable", "honourable", "mr", "mrs", "dr"}),
    )

    return index, snapshot


# Build once — the snapshot is immutable
_FIXED_INDEX, _FIXED_SNAPSHOT = _build_blocklist_test_snapshot()


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(data=words_with_blocklist_token())
@settings(max_examples=300)
def test_blocklist_tokens_survive_correct_words(
    data: tuple[list[dict], list[str]],
) -> None:
    """Block_List tokens survive correct_words unchanged for arbitrary context.

    For ANY word list containing Block_List tokens at any position with
    any surrounding context words, after running correct_words every
    Block_List token appears unchanged in the output word list.

    This proves the Block_List guard prevents fuzzy matching from
    incorrectly "correcting" common words like "general", "page",
    "nation", or "national" to entity names.

    **Validates: Requirements 4.1, 15.5**
    """
    words, blocked_tokens_in_order = data

    result = correct_words(
        words,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        word_accept_threshold=0.90,
        min_confidence=0.75,
    )

    output_words = result.words

    # Collect all output word texts
    output_texts = [w.get("word", "") for w in output_words]

    # Every Block_List token from the input must appear unchanged in output
    for blocked_token in blocked_tokens_in_order:
        assert blocked_token in output_texts, (
            f"Block_List token '{blocked_token}' was not found in output words. "
            f"Input contained: {[w['word'] for w in words]}, "
            f"Output: {output_texts}"
        )
        # Remove the first occurrence so duplicates are checked independently
        output_texts.remove(blocked_token)


@given(data=text_with_blocklist_token())
@settings(max_examples=300)
def test_blocklist_tokens_survive_correct_text(
    data: tuple[str, list[str]],
) -> None:
    """Block_List tokens survive correct_text unchanged for arbitrary context.

    For ANY text containing Block_List tokens at any position with any
    surrounding context words, after running correct_text every Block_List
    token appears unchanged in the output text.

    This proves the Block_List guard prevents the text-level correction
    from altering protected common words.

    **Validates: Requirements 4.1, 15.5**
    """
    text, blocked_tokens_in_text = data

    result = correct_text(
        text,
        _FIXED_INDEX,
        _FIXED_SNAPSHOT,
        min_confidence=0.75,
    )

    corrected_text = result.text

    # Every Block_List token from the input must appear in the corrected text
    # We check as whole words to avoid substring false positives
    corrected_words = corrected_text.split()

    for blocked_token in blocked_tokens_in_text:
        assert blocked_token in corrected_words, (
            f"Block_List token '{blocked_token}' was not found in corrected text words. "
            f"Input: '{text}', "
            f"Output: '{corrected_text}', "
            f"Output words: {corrected_words}"
        )
        # Remove the first occurrence so duplicates are checked independently
        corrected_words.remove(blocked_token)
