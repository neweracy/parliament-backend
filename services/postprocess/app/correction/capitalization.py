"""Capitalization_Prior and Punctuated_Word semantics (Req 6).

A pure module — no engine state, no side effects — so every function here is
unit- and property-testable in isolation and reusable by the Context_Gate
(task 4.3, ``app/correction/context.py``) and by the correction-emission sites
in ``app/correction/engine.py`` (``correct_words``).

What this module owns
---------------------
1. **The Capitalization_Prior signal** (Req 6.1-6.5). Deepgram supplies each
   Word's capitalised, punctuated surface form in the ``punctuated_word`` field.
   A Word whose ``punctuated_word`` begins with an uppercase cased letter is a
   weak proper-noun signal — *weak* because at a sentence boundary every word is
   capitalised, so the signal there carries no information. :func:`word_cap_prior`
   reads ``punctuated_word`` and discounts the sentence-initial position to
   ``UNKNOWN`` (Req 6.3); :func:`span_cap_prior` lifts the per-Word priors to a
   single per-Span prior that is ``UPPERCASE`` only when *every* covered Word is
   ``UPPERCASE`` (Req 6.4).

2. **The Punctuated_Word passthrough/merge/replace helpers** (Req 6.6-6.9).
   :func:`merged_punctuated_word` and :func:`replaced_punctuated_word` compute
   the ``punctuated_word`` a corrected Word should carry: the corrected text
   plus any terminal ``.?!`` inherited from the source Word(s), or ``None`` when
   no covered Word carried a ``punctuated_word`` string (Req 6.7, 6.8, 6.9).
   Passthrough (Req 6.6) needs no helper — an uncorrected Word is copied whole,
   so its ``punctuated_word`` rides through verbatim.

Boundary with the Context_Gate (task 4.3) and baseline equivalence (Req 12.9)
-----------------------------------------------------------------------------
This task (4.1) is deliberately scoped to the *pure prior computation* and the
*Punctuated_Word field semantics*. It does **not** apply the prior to any gate
decision: the Context_Gate (task 4.3) consumes :func:`span_cap_prior` as one of
its Context_Signals (an uppercase prior on a Span's first Word, Req 7.1). Wiring
the prior into a gate decision belongs there, behind ``context_gate_enabled``.

The merge/replace helpers *are* consumed by ``correct_words`` in this task, but
their application there is gated behind a non-inert :class:`~app.correction.gates.GateContext`
(at least one feature flag on). With every flag off the engine keeps its
existing behaviour — a merged Word inherits the first source Word's
``punctuated_word`` unchanged — so the all-flags-off Words list stays
byte-for-byte Baseline (Req 12.9, Property 12). The helpers themselves are pure
and flag-agnostic; the engine owns the gating.

The Unknown sentinel here is the :class:`CapPrior.UNKNOWN` enum member, mirroring
the ``None`` Unknown sentinel of ``app.correction.confidence`` — both mean "no
signal available", but a Capitalization_Prior is a tri-state (uppercase /
lowercase / unknown) so it is modelled as an enum rather than an optional bool.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

# Terminal sentence punctuation (Req 6.3, 6.7, 6.8). A Punctuated_Word ending in
# one of these closes a sentence, so the *next* Word is sentence-initial and
# carries no proper-noun capitalization signal.
_TERMINAL_PUNCT = ".?!"


class CapPrior(StrEnum):
    """The Capitalization_Prior of a Word or Span (Req 6.1, 6.2).

    * ``UPPERCASE`` — the first cased letter of the Punctuated_Word is uppercase,
      away from a sentence boundary: a genuine proper-noun signal.
    * ``LOWERCASE`` — the first cased letter is lowercase.
    * ``UNKNOWN`` — no case signal is available: the field is absent, is not a
      string, holds no cased letter, or the Word is sentence-initial (Req 6.2,
      6.3, 6.5).

    A :class:`StrEnum` so the value serialises to its own name and compares
    equal to the plain string, matching the design (design.md section 7).
    """

    UPPERCASE = "uppercase"
    LOWERCASE = "lowercase"
    UNKNOWN = "unknown"


def _first_cased_prior(text: str) -> CapPrior:
    """The prior from the first cased letter of *text*, ignoring position.

    Returns ``UPPERCASE``/``LOWERCASE`` for the first character that has a case
    distinction (``str.isupper``/``str.islower`` differ), or ``UNKNOWN`` when no
    character in *text* is cased — digits, punctuation, and caseless scripts all
    yield ``UNKNOWN`` (Req 6.1, 6.2).
    """
    for ch in text:
        if ch.isupper():
            return CapPrior.UPPERCASE
        if ch.islower():
            return CapPrior.LOWERCASE
    return CapPrior.UNKNOWN


def _punctuated_word(word: dict[str, Any]) -> str | None:
    """The Word's ``punctuated_word`` when it is a non-empty string, else ``None``.

    A missing field, a ``None`` value, or a non-string value (Req 6.2) all
    resolve to ``None`` — "no Punctuated_Word string" — which the callers treat
    as an absent signal.
    """
    value = word.get("punctuated_word")
    if isinstance(value, str):
        return value
    return None


def _ends_sentence(punctuated: str | None) -> bool:
    """True when *punctuated* is a string ending in ``.``, ``?``, or ``!`` (Req 6.3)."""
    return bool(punctuated) and punctuated[-1] in _TERMINAL_PUNCT


def word_cap_prior(
    word: dict[str, Any],
    prev_word: dict[str, Any] | None,
    is_first: bool,
) -> CapPrior:
    """The Capitalization_Prior of a single Word (Req 6.1, 6.2, 6.3).

    Parameters
    ----------
    word:
        The Word dict. Its ``punctuated_word`` field carries the provider's
        capitalised form.
    prev_word:
        The immediately preceding Word dict, or ``None`` when there is none.
        Used only to detect a sentence boundary (Req 6.3).
    is_first:
        ``True`` when *word* is the first Word of the Words list (Req 6.3).

    Returns
    -------
    CapPrior
        ``UNKNOWN`` when *word* is sentence-initial — it is the first Word
        (*is_first*), the previous Word's ``punctuated_word`` ends with terminal
        punctuation, or the previous Word carries no ``punctuated_word`` string
        (Req 6.3) — regardless of *word*'s own case. Otherwise ``UNKNOWN`` when
        *word* has no ``punctuated_word`` string or that string has no cased
        letter (Req 6.2), else ``UPPERCASE``/``LOWERCASE`` from the first cased
        letter of that string (Req 6.1).

    Notes
    -----
    The sentence-initial discount (Req 6.3) is applied *before* reading the
    Word's own case: a capitalised word at the start of a sentence is capitalised
    by convention, not because it is a proper noun, so it must contribute no
    proper-noun signal.
    """
    # Sentence-initial discount (Req 6.3): applied first, and independent of the
    # Word's own case. The Word is sentence-initial when it is the first Word,
    # when the previous Word ended a sentence, or when the previous Word carried
    # no Punctuated_Word string (so no boundary information is available).
    if is_first:
        return CapPrior.UNKNOWN
    prev_punctuated = _punctuated_word(prev_word) if prev_word is not None else None
    if prev_punctuated is None or _ends_sentence(prev_punctuated):
        return CapPrior.UNKNOWN

    # Not sentence-initial: read this Word's own Punctuated_Word (Req 6.1, 6.2).
    punctuated = _punctuated_word(word)
    if punctuated is None:
        return CapPrior.UNKNOWN
    return _first_cased_prior(punctuated)


def span_cap_prior(
    covered_words: Sequence[dict[str, Any]],
    is_first: bool,
    prev_word: dict[str, Any] | None = None,
) -> CapPrior:
    """The Capitalization_Prior of a Span from its covered Words (Req 6.4, 6.5).

    Parameters
    ----------
    covered_words:
        The Word dicts the Span covers, in sequence order.
    is_first:
        ``True`` when the Span's first covered Word is the first Word of the
        whole Words list (Req 6.3 propagates to the Span's leading Word).
    prev_word:
        The Word immediately preceding the Span in the full Words list, or
        ``None`` when there is none. It supplies the sentence-boundary context
        for the Span's first covered Word (Req 6.3): a Span that opens a new
        sentence carries no proper-noun signal. Defaults to ``None`` so a caller
        that cannot supply it treats the Span as sentence-initial, which is the
        conservative (signal-suppressing) choice.

    Returns
    -------
    CapPrior
        ``UPPERCASE`` only when *every* covered Word has an ``UPPERCASE`` prior
        (Req 6.4) — a whole capitalised Span is the proper-noun Context_Signal.
        ``UNKNOWN`` for an empty *covered_words* (Req 6.5) or when any covered
        Word's prior is not ``UPPERCASE``.

    Notes
    -----
    The per-Word priors are computed relative to the full-list context: the
    first covered Word uses *prev_word* (and *is_first*) to judge its
    sentence-initial status, and each subsequent covered Word uses its in-Span
    predecessor. A mid-sentence Span whose covered Words are all uppercase —
    e.g. "Ama Sarpong" after a non-terminal word — carries the uppercase prior
    (Req 6.4); the same Span opening a sentence is Unknown (Req 6.3).
    """
    if not covered_words:
        return CapPrior.UNKNOWN

    prev: dict[str, Any] | None = prev_word
    for offset, word in enumerate(covered_words):
        prior = word_cap_prior(word, prev, is_first and offset == 0)
        if prior is not CapPrior.UPPERCASE:
            return CapPrior.UNKNOWN
        prev = word
    return CapPrior.UPPERCASE


def _terminal_punct(punctuated: str | None) -> str:
    """The single terminal ``.?!`` of *punctuated*, or ``""`` when there is none."""
    if _ends_sentence(punctuated):
        return punctuated[-1]  # type: ignore[index]  # _ends_sentence guards None/empty
    return ""


def merged_punctuated_word(
    corrected_text: str,
    merged_words: Sequence[dict[str, Any]],
) -> str | None:
    """The Punctuated_Word for a Word merged from several source Words (Req 6.7, 6.9).

    Parameters
    ----------
    corrected_text:
        The corrected surface text the merged Word will carry.
    merged_words:
        The source Word dicts the correction merged into one, in sequence order.

    Returns
    -------
    str | None
        *corrected_text* followed by any terminal ``.?!`` that ended the *last*
        merged Word's ``punctuated_word`` (Req 6.7), when at least one merged
        Word carried a ``punctuated_word`` string. ``None`` when no merged Word
        carried one, so the caller omits the field (Req 6.9).
    """
    if not any(_punctuated_word(w) is not None for w in merged_words):
        return None
    last_punctuated = _punctuated_word(merged_words[-1]) if merged_words else None
    return corrected_text + _terminal_punct(last_punctuated)


def replaced_punctuated_word(
    corrected_text: str,
    replaced_word: dict[str, Any],
) -> str | None:
    """The Punctuated_Word for a single replaced Word (Req 6.8, 6.9).

    Parameters
    ----------
    corrected_text:
        The corrected surface text the Word will carry.
    replaced_word:
        The single source Word dict whose text the correction replaced.

    Returns
    -------
    str | None
        *corrected_text* followed by any terminal ``.?!`` that ended the Word's
        previous ``punctuated_word`` (Req 6.8), when the Word carried a
        ``punctuated_word`` string. ``None`` when it did not, so the caller
        omits the field (Req 6.9).
    """
    prior_punctuated = _punctuated_word(replaced_word)
    if prior_punctuated is None:
        return None
    return corrected_text + _terminal_punct(prior_punctuated)
