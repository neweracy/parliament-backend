"""Property tests for alignment Word-count preservation.

Validates: Requirements 11.9, 15.2

These tests verify that the LLM_Refiner alignment step adds and removes no
Words, whatever the refined token sequence. Both ``apply_aligned`` and
``apply_aligned_with_map`` must preserve len(words) for arbitrary inputs.

Properties tested:
1. apply_aligned never changes the Word count, regardless of the refined
   token sequence length or content.
2. apply_aligned_with_map never changes the Word count, regardless of the
   refined token sequence length or content.
"""

from __future__ import annotations

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from app.llm.align import apply_aligned, apply_aligned_with_map, lcs_pairs


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Pool of realistic token strings plus some edge cases
_TOKEN_POOL = [
    "the", "of", "and", "in", "to", "for", "with", "is", "was",
    "Kumasi", "Accra", "Tamale", "Ghana", "parliament",
    "president", "minister", "honorable", "speaker",
    "nineteen", "ninety", "two", "thousand", "twenty",
    "hello", "world", "test", "data", "correction",
    "Mr", "Mrs", "Dr", "Hon", "committee", "house",
    "fiscal", "budget", "economic", "policy", "region",
    "Ningo-Prampram", "Ofori-Atta", "Ken", "B.B.", "Carboo",
]


@st.composite
def word_dicts(draw: st.DrawFn) -> list[dict]:
    """Generate a list of 1-20 Word dicts with monotonic timing.

    Each Word has a ``word`` field (1-3 whitespace-separated tokens to test
    multi-token entries), plus ``start``, ``end``, and ``confidence``.
    """
    size = draw(st.integers(min_value=1, max_value=20))
    words: list[dict] = []
    cursor = 0.0

    for _ in range(size):
        # 1-3 tokens per Word entry (multi-token simulates merged words)
        n_tokens = draw(st.integers(min_value=1, max_value=3))
        tokens = [draw(st.sampled_from(_TOKEN_POOL)) for _ in range(n_tokens)]
        word_text = " ".join(tokens)

        gap = draw(st.floats(min_value=0.0, max_value=0.2))
        duration = draw(st.floats(min_value=0.1, max_value=1.0))
        start = cursor + gap
        end = start + duration
        cursor = end

        words.append({
            "word": word_text,
            "start": round(start, 3),
            "end": round(end, 3),
            "confidence": draw(st.floats(min_value=0.3, max_value=1.0)),
        })

    return words


@st.composite
def refined_tokens(draw: st.DrawFn) -> list[str]:
    """Generate an arbitrary refined token sequence (1-60 tokens).

    The length may differ from the original token count — this is the
    key scenario where alignment must still preserve word count.
    """
    size = draw(st.integers(min_value=1, max_value=60))
    return [draw(st.sampled_from(_TOKEN_POOL)) for _ in range(size)]


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(words=word_dicts(), refined=refined_tokens())
@settings(max_examples=500)
def test_apply_aligned_preserves_word_count(
    words: list[dict], refined: list[str]
) -> None:
    """apply_aligned never changes the Word count.

    For ANY list of Word dicts and ANY refined token sequence (regardless of
    length, content, or whether it's shorter/longer/same as the original
    tokens), applying ``apply_aligned`` NEVER changes the Word count.

    Formally: ``len(apply_aligned(words, refined)) == len(words)``

    **Validates: Requirements 11.9, 15.2**
    """
    input_count = len(words)
    # Deep copy so the property test doesn't accumulate mutations
    words_copy = copy.deepcopy(words)

    result = apply_aligned(words_copy, refined)

    assert len(result) == input_count, (
        f"apply_aligned changed Word count: input={input_count}, "
        f"output={len(result)}. "
        f"Input word texts: {[w['word'] for w in words]}, "
        f"Refined tokens ({len(refined)}): {refined[:20]}..."
    )


@given(words=word_dicts(), refined=refined_tokens())
@settings(max_examples=500)
def test_apply_aligned_with_map_preserves_word_count(
    words: list[dict], refined: list[str]
) -> None:
    """apply_aligned_with_map never changes the Word count.

    For ANY list of Word dicts and ANY refined token sequence (regardless of
    length, content, or whether it's shorter/longer/same as the original
    tokens), applying ``apply_aligned_with_map`` with a properly constructed
    token_map NEVER changes the Word count.

    Formally: ``len(apply_aligned_with_map(words, refined, token_map)) == len(words)``

    **Validates: Requirements 11.9, 15.2**
    """
    input_count = len(words)
    # Deep copy so the property test doesn't accumulate mutations
    words_copy = copy.deepcopy(words)

    # Build the token_map from the words (same logic as the chunker uses)
    token_map: list[int] = []
    for idx, w in enumerate(words_copy):
        token_count = len(w.get("word", "").split()) or 1
        for _ in range(token_count):
            token_map.append(idx)

    result = apply_aligned_with_map(words_copy, refined, token_map)

    assert len(result) == input_count, (
        f"apply_aligned_with_map changed Word count: input={input_count}, "
        f"output={len(result)}. "
        f"Input word texts: {[w['word'] for w in words]}, "
        f"Refined tokens ({len(refined)}): {refined[:20]}..., "
        f"Token map length: {len(token_map)}"
    )
