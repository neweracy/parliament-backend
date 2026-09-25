"""Smoke test for the shared property-test fixture and strategies (task 6.3.1).

This test does NOT implement any correctness property. It only verifies that the
shared fixture (:mod:`tests.property.fixtures`) and the hypothesis input
strategies (:mod:`tests.property.strategies`) import cleanly and produce
spec-conformant data, so the property tests in tasks 1.x-6.2 and the regression
tests in 6.3.2 can rely on them.

Asserted here (Req 16.15):

* the fixture snapshot holds >=20 canonical entities;
* it holds >=10 Block_List entries;
* the bundled English_Lexicon is active (loaded);
* the strategies produce Words with confidence ``None`` or in [0, 1],
  ``end >= start``, timings in [0, 36000] s, and tokens of length 1-30; and
* a generated Words list holds 1-200 Words with non-decreasing ``start`` values.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings

from tests.property.fixtures import (
    FIXTURE_BLOCK_LIST,
    build_property_snapshot,
    load_property_lexicon,
    property_alias_set,
    property_canonicals,
)
from tests.property.strategies import (
    MAX_TIME,
    MAX_TOKEN_LEN,
    MAX_WORDS,
    MIN_TOKEN_LEN,
    MIN_WORDS,
    confidences,
    start_end_pairs,
    tokens,
    word_lists,
    words,
)

# ---------------------------------------------------------------------------
# Fixture-shape assertions (Req 16.15)
# ---------------------------------------------------------------------------


def test_snapshot_holds_at_least_twenty_canonical_entities() -> None:
    """The fixture snapshot holds >=20 canonical entities (Req 16.15)."""
    snapshot = build_property_snapshot()
    canonicals = property_canonicals()

    assert snapshot.record_count == len(snapshot.records)
    assert len(canonicals) >= 20, (
        f"fixture must hold >=20 canonical entities, has {len(canonicals)}"
    )
    # Every record is a canonical entity; the record count matches the set of
    # canonicals (no duplicate canonicals in the curated set).
    assert snapshot.record_count == len(canonicals)


def test_snapshot_holds_at_least_ten_block_list_entries() -> None:
    """The fixture snapshot holds >=10 Block_List entries (Req 16.15)."""
    snapshot = build_property_snapshot()

    assert snapshot.block_list == FIXTURE_BLOCK_LIST
    assert len(snapshot.block_list) >= 10, (
        f"fixture must hold >=10 Block_List entries, has {len(snapshot.block_list)}"
    )
    # Block_List tokens are stored lowercase (the engine lowercases before the
    # membership check) so they exercise the guard case-insensitively.
    assert all(token == token.lower() for token in snapshot.block_list)


def test_snapshot_entities_carry_aliases() -> None:
    """At least some canonical entities carry aliases (Req 16.15)."""
    snapshot = build_property_snapshot()
    with_aliases = [r for r in snapshot.records if r.aliases]
    assert with_aliases, "fixture entities must carry aliases"
    # The alias set (canonical_map keys) is richer than the canonical set.
    assert len(property_alias_set()) > snapshot.record_count


def test_bundled_lexicon_is_active() -> None:
    """The bundled English_Lexicon loads and is active (Req 16.15)."""
    lexicon = load_property_lexicon()
    assert lexicon.loaded is True
    assert len(lexicon) >= 25_000
    # Membership check works on ordinary words.
    assert lexicon.contains("thank")
    assert lexicon.contains("later")


def test_snapshot_is_cached_singleton() -> None:
    """The snapshot is built once and shared (immutable singleton)."""
    assert build_property_snapshot() is build_property_snapshot()


# ---------------------------------------------------------------------------
# Strategy conformance (Req 16.15)
# ---------------------------------------------------------------------------


@given(token=tokens())
@settings(max_examples=200)
def test_tokens_respect_length_bounds(token: str) -> None:
    """Generated tokens are 1-30 characters (Req 16.15)."""
    assert MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN


@given(confidence=confidences())
@settings(max_examples=200)
def test_confidences_are_none_or_unit_interval(confidence: float | None) -> None:
    """Generated confidences are omitted (``None``) or in [0, 1] (Req 16.15)."""
    assert confidence is None or 0.0 <= confidence <= 1.0


@given(pair=start_end_pairs())
@settings(max_examples=200)
def test_start_end_pairs_are_ordered_and_bounded(pair: tuple[float, float]) -> None:
    """Generated ``(start, end)`` pairs are ordered and within [0, 36000] s."""
    start, end = pair
    assert 0.0 <= start <= MAX_TIME
    assert 0.0 <= end <= MAX_TIME
    assert end >= start


@given(word=words())
@settings(max_examples=200)
def test_single_word_is_spec_conformant(word: dict) -> None:
    """A generated Word carries a valid token, ordered timings, and unit confidence."""
    assert set(word).issuperset({"word", "start", "end"})
    assert MIN_TOKEN_LEN <= len(word["word"]) <= MAX_TOKEN_LEN
    assert 0.0 <= word["start"] <= MAX_TIME
    assert 0.0 <= word["end"] <= MAX_TIME
    assert word["end"] >= word["start"]
    if "confidence" in word:
        assert 0.0 <= word["confidence"] <= 1.0


@given(word_list=word_lists(max_size=50))
@settings(max_examples=100)
def test_word_lists_are_bounded_and_monotonic(word_list: list[dict]) -> None:
    """A generated Words list holds 1-N Words with non-decreasing ``start`` values."""
    assert MIN_WORDS <= len(word_list) <= 50
    assert len(word_list) <= MAX_WORDS

    prev_start = -1.0
    for word in word_list:
        assert MIN_TOKEN_LEN <= len(word["word"]) <= MAX_TOKEN_LEN
        assert 0.0 <= word["start"] <= MAX_TIME
        assert 0.0 <= word["end"] <= MAX_TIME
        assert word["end"] >= word["start"]
        assert word["start"] >= prev_start, "start values must be non-decreasing"
        prev_start = word["start"]
        if "confidence" in word:
            assert 0.0 <= word["confidence"] <= 1.0


@given(word_list=word_lists(min_size=1, max_size=200))
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_word_lists_reach_full_range(word_list: list[dict]) -> None:
    """The Words-list strategy can produce up to 200 Words (Req 16.15)."""
    assert 1 <= len(word_list) <= 200


@given(word=words(with_punctuated=True))
@settings(max_examples=100)
def test_words_can_carry_punctuated_word_extra(word: dict) -> None:
    """The Word strategy can attach a ``punctuated_word`` provider extra."""
    assert "punctuated_word" in word
    assert isinstance(word["punctuated_word"], str)


@given(token=tokens(), word=words(), word_list=word_lists(max_size=5))
@settings(max_examples=25)
def test_strategies_are_composable_and_materialise(
    token: str,
    word: dict,
    word_list: list[dict],
) -> None:
    """The token/Word/Words-list strategies compose and materialise together."""
    assert isinstance(token, str)
    assert "word" in word
    assert 1 <= len(word_list) <= 5
