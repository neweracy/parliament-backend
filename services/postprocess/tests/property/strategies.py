"""Hypothesis input strategies for the property tests (Req 16.15).

These strategies generate the inputs Req 16.15 mandates, all keyed off the
shared fixture in :mod:`tests.property.fixtures`:

* **tokens** of 1 to 30 characters drawn from the fixture alias set, the bundled
  English_Lexicon, and random lowercase letter sequences (:func:`tokens`);
* **Word_Confidence** values either omitted (``None``) or in the closed interval
  [0.0, 1.0] (:func:`confidences`);
* **start/end** pairs in the closed interval [0.0, 36000.0] seconds with
  ``end >= start`` (:func:`start_end_pairs`);
* a single **Word** dict carrying ``word``/``start``/``end`` and optionally
  ``confidence`` (and optionally a ``punctuated_word`` provider extra)
  (:func:`words`);
* a **Words list** of 1 to 200 Words with non-decreasing ``start`` values so
  timing-order invariants can be checked (:func:`word_lists`).

Every strategy is composable: :func:`words` builds on :func:`tokens`,
:func:`confidences`, and :func:`start_end_pairs`, and :func:`word_lists` builds
on :func:`words`. They intentionally do NOT wrap themselves in a
:class:`app.models.request.Word` model so a test can feed the plain dicts
straight into ``correct_words`` (which accepts word dicts) or construct the
model itself.
"""

from __future__ import annotations

from functools import lru_cache

from hypothesis import strategies as st

from tests.property.fixtures import (
    build_property_snapshot,
    load_property_lexicon,
    property_alias_set,
)

# Bounds mandated by Req 16.15.
MIN_TOKEN_LEN = 1
MAX_TOKEN_LEN = 30
MIN_WORDS = 1
MAX_WORDS = 200
MIN_TIME = 0.0
MAX_TIME = 36_000.0

# A fixed, sorted sample of the bundled lexicon. The full lexicon is >=25,000
# forms; sampling a bounded, deterministically-ordered slice keeps the strategy
# space tractable while still drawing real English words (Req 16.15). Sorted so
# the sample is identical on every run.
_LEXICON_SAMPLE_SIZE = 500


# Fallback pool of ordinary English words, used only if the bundled lexicon is
# unavailable at generation time. Keeps the token strategy meaningful in that
# degenerate case.
_COMMON_ENGLISH_WORDS: frozenset[str] = frozenset(
    {
        "thank",
        "later",
        "district",
        "member",
        "general",
        "nation",
        "national",
        "station",
        "house",
        "report",
        "debate",
        "question",
        "answer",
        "morning",
        "today",
        "people",
        "government",
        "committee",
        "budget",
        "policy",
    }
)


@lru_cache(maxsize=1)
def _alias_token_pool() -> tuple[str, ...]:
    """Individual whitespace-delimited tokens drawn from the fixture alias set.

    The alias set keys are lowercased alias/canonical strings; splitting on
    whitespace yields single tokens (1-30 chars) suitable for the token
    strategy, filtered to the length bounds.
    """
    parts: set[str] = set()
    for key in property_alias_set():
        for part in key.split():
            if MIN_TOKEN_LEN <= len(part) <= MAX_TOKEN_LEN:
                parts.add(part)
    return tuple(sorted(parts))


@lru_cache(maxsize=1)
def _lexicon_token_pool() -> tuple[str, ...]:
    """A fixed sample of lexicon word forms within the length bounds."""
    lexicon = load_property_lexicon()
    # EnglishLexicon has no public iterator; read its private word set. Fall
    # back to a curated common-word set if the lexicon is inactive/unavailable,
    # so the pool is never empty.
    lex_words = getattr(lexicon, "_words", frozenset())
    usable = sorted(w for w in lex_words if MIN_TOKEN_LEN <= len(w) <= MAX_TOKEN_LEN)
    if not usable:
        usable = sorted(_COMMON_ENGLISH_WORDS)
    return tuple(usable[:_LEXICON_SAMPLE_SIZE])


# Random letter-sequence tokens (1-30 lowercase ascii letters).
_random_letters = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122),
    min_size=MIN_TOKEN_LEN,
    max_size=MAX_TOKEN_LEN,
)


def alias_tokens() -> st.SearchStrategy[str]:
    """Strategy: a single token drawn from the fixture alias set."""
    return st.sampled_from(_alias_token_pool())


def lexicon_tokens() -> st.SearchStrategy[str]:
    """Strategy: a single token drawn from the bundled English_Lexicon sample."""
    return st.sampled_from(_lexicon_token_pool())


def random_tokens() -> st.SearchStrategy[str]:
    """Strategy: a random 1-30-char lowercase letter sequence."""
    return _random_letters


def tokens() -> st.SearchStrategy[str]:
    """Strategy: a 1-30-char token from the alias set, lexicon, or random letters.

    The three sources are combined with equal weight so generated Words draw a
    mix of known aliases (fuzzy/phonetic targets), ordinary English words
    (lexicon-gate targets), and arbitrary noise (Req 16.15).
    """
    return st.one_of(alias_tokens(), lexicon_tokens(), random_tokens())


def confidences() -> st.SearchStrategy[float | None]:
    """Strategy: a Word_Confidence omitted (``None``) or in [0.0, 1.0] (Req 16.15)."""
    return st.one_of(
        st.none(),
        st.floats(
            min_value=0.0,
            max_value=1.0,
            allow_nan=False,
            allow_infinity=False,
        ),
    )


@st.composite
def start_end_pairs(draw: st.DrawFn) -> tuple[float, float]:
    """Strategy: a ``(start, end)`` pair in [0, 36000] s with ``end >= start``."""
    start = draw(
        st.floats(
            min_value=MIN_TIME,
            max_value=MAX_TIME,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    end = draw(
        st.floats(
            min_value=start,
            max_value=MAX_TIME,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    return (round(start, 3), round(end, 3))


@st.composite
def words(draw: st.DrawFn, *, with_punctuated: bool = False) -> dict:
    """Strategy: one Word dict with ``word``/``start``/``end`` and optional confidence.

    Draws the token from :func:`tokens`, the timings from :func:`start_end_pairs`,
    and the confidence from :func:`confidences` (omitted entirely when ``None``,
    matching the provider-omitted case). When ``with_punctuated`` is set, a
    ``punctuated_word`` provider extra is attached (the token capitalized, with
    an occasional terminal punctuation mark) so capitalization-prior tests can
    draw it.
    """
    token = draw(tokens())
    start, end = draw(start_end_pairs())
    word: dict = {"word": token, "start": start, "end": end}

    confidence = draw(confidences())
    if confidence is not None:
        word["confidence"] = confidence

    if with_punctuated:
        terminal = draw(st.sampled_from(["", "", ".", "?", "!"]))
        word["punctuated_word"] = token.capitalize() + terminal

    return word


@st.composite
def word_lists(
    draw: st.DrawFn,
    *,
    min_size: int = MIN_WORDS,
    max_size: int = MAX_WORDS,
    monotonic: bool = True,
    with_punctuated: bool = False,
) -> list[dict]:
    """Strategy: a list of 1-200 Word dicts (Req 16.15).

    When ``monotonic`` is true (the default) the Words carry non-decreasing
    ``start`` values with ``end >= start`` per Word, so timing-order invariants
    (Property 4) hold on the generated input. Timings are laid out sequentially
    within [0, 36000] s; the whole list stays inside the bound by construction.

    When ``monotonic`` is false, each Word draws independent timings from
    :func:`start_end_pairs`, useful for tests that must not assume ordering.
    """
    size = draw(st.integers(min_value=min_size, max_value=max_size))

    if not monotonic:
        return [draw(words(with_punctuated=with_punctuated)) for _ in range(size)]

    result: list[dict] = []
    cursor = draw(
        st.floats(min_value=MIN_TIME, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    # Reserve headroom so the sequence never exceeds MAX_TIME.
    max_step = max(0.001, (MAX_TIME - cursor) / (size + 1))
    for _ in range(size):
        token = draw(tokens())
        gap = draw(
            st.floats(min_value=0.0, max_value=max_step, allow_nan=False, allow_infinity=False)
        )
        duration = draw(
            st.floats(
                min_value=0.0,
                max_value=max_step,
                allow_nan=False,
                allow_infinity=False,
            )
        )
        start = min(cursor + gap, MAX_TIME)
        end = min(start + duration, MAX_TIME)
        cursor = end

        word: dict = {"word": token, "start": round(start, 3), "end": round(end, 3)}
        confidence = draw(confidences())
        if confidence is not None:
            word["confidence"] = confidence
        if with_punctuated:
            terminal = draw(st.sampled_from(["", "", ".", "?", "!"]))
            word["punctuated_word"] = token.capitalize() + terminal
        result.append(word)

    return result


def snapshot():  # noqa: ANN201 - returns DatasetSnapshot, kept import-light
    """Return the shared property-test DatasetSnapshot (convenience re-export)."""
    return build_property_snapshot()
