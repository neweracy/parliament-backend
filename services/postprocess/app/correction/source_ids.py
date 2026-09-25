"""Stable Source_Word_Id / Source_Span_Id addressing (Req 1.2, 1.3, 2.7).

The foundation both persisted correction evidence and deterministic citation
navigation rest on. A correction is addressable by the raw ASR Source_Words it
changed, and a citation is addressable by the same identities, because the ids
are fixed within a transcript version.

Design (see design.md, Components and Interfaces > Python):

* Each raw ASR Source_Word gets a ``source_word_id`` derived deterministically
  from its zero-based index in the raw ASR word list — ``w0``, ``w1``, ... . The
  raw word list is fixed for a transcript version, so the scheme is stable
  within the version and distinct by construction (Req 1.2). No extra
  persistence is needed beyond the ordered ``word_timings`` the Baseline already
  stores.
* A ``source_span_id`` addresses the contiguous run of Source_Words a single
  correction covers, derived from its bounding word ids as ``s:{first}-{last}``,
  so it too is stable within the version.

These ids ride *alongside* the correction pipeline. They are assigned once,
immediately after ASR output is available and BEFORE any correction stage runs,
by observing the raw ASR word order. They never read or rewrite the ASR
``start``/``end`` of any Source_Word (Immutable_Source_Timing, Req 2.7): this
module derives ids from position only and does not touch timing. The existing
``GateContext``/``GateConfig`` threading through ``correct_single`` /
``correct_text`` / ``correct_words`` is unchanged — id assignment observes ASR
order, it does not change gating.

This module is intentionally pure and free of engine/pipeline state so it can be
unit- and property-tested in isolation and reused by the evidence builder (task
2.2) without widening any correction signature.
"""

from __future__ import annotations

from collections.abc import Sequence

# Prefix for a Source_Word_Id — ``w`` + the word's zero-based index in the raw
# ASR word list (``w0``, ``w1``, ...).
_WORD_ID_PREFIX = "w"

# Prefix for a Source_Span_Id — ``s:`` + the first and last covered word ids
# joined by ``-`` (``s:w0-w2``); a single-word span repeats the id (``s:w0-w0``).
_SPAN_ID_PREFIX = "s:"


def source_word_id(index: int) -> str:
    """Return the Source_Word_Id for the raw ASR word at *index* (Req 1.2).

    The id is ``w`` followed by the word's zero-based index in the raw ASR word
    list, e.g. ``w0``, ``w1``. Because the raw word list is fixed for a
    transcript version, the id is stable within the version and distinct from
    every other word's id by construction.

    Parameters
    ----------
    index:
        The zero-based index of the Source_Word in the raw ASR word list.

    Returns
    -------
    str
        The deterministic Source_Word_Id.

    Raises
    ------
    ValueError
        If *index* is negative — a Source_Word_Id addresses a position in the
        raw list and a negative position is never valid.
    """
    if index < 0:
        raise ValueError(f"source word index must be non-negative, got {index}")
    return f"{_WORD_ID_PREFIX}{index}"


def assign_source_word_ids(raw_words: Sequence[object]) -> list[str]:
    """Assign a stable Source_Word_Id to each raw ASR Source_Word (Req 1.2).

    Derives one id per word from its zero-based position in *raw_words*, in ASR
    order. The result is positional only: it never inspects a word's ``word``,
    ``start``, ``end``, or ``confidence`` and so cannot alter ASR timing
    (Immutable_Source_Timing, Req 2.7). It is called once, before any correction
    stage runs, over the raw ASR word list.

    Parameters
    ----------
    raw_words:
        The raw ASR Source_Words in ASR order. Only the count and ordering are
        used; the element type is irrelevant (dicts, Pydantic ``Word`` models,
        or any sequence element all work).

    Returns
    -------
    list[str]
        The Source_Word_Ids, one per Source_Word, in ASR order — index ``k``
        holds the id for ``raw_words[k]``.
    """
    return [source_word_id(i) for i in range(len(raw_words))]


def source_span_id(covered_word_ids: Sequence[str]) -> str:
    """Derive the Source_Span_Id for a contiguous run of covered Source_Words.

    The id is ``s:{first}-{last}`` from the bounding word ids of the covered run
    (e.g. ``s:w0-w2``); a single-word span repeats its id (``s:w0-w0``). Derived
    only from the covered word ids, so it is stable within the transcript
    version for the same covered run (Req 1.3, 2.1, 2.2).

    Parameters
    ----------
    covered_word_ids:
        The Source_Word_Ids the span covers, in ASR order. At least one id is
        required; the first and last bound the span.

    Returns
    -------
    str
        The deterministic Source_Span_Id.

    Raises
    ------
    ValueError
        If *covered_word_ids* is empty — a Source_Span_Id addresses at least one
        Source_Word.
    """
    if not covered_word_ids:
        raise ValueError("a Source_Span_Id must cover at least one Source_Word")
    first = covered_word_ids[0]
    last = covered_word_ids[-1]
    return f"{_SPAN_ID_PREFIX}{first}-{last}"


def source_span_id_from_indices(first_index: int, last_index: int) -> str:
    """Derive a Source_Span_Id directly from the bounding word indices.

    Convenience wrapper over :func:`source_word_id` + :func:`source_span_id` for
    a caller that has the first and last covered indices rather than the ids.
    ``first_index`` must not exceed ``last_index``.

    Parameters
    ----------
    first_index:
        Zero-based index of the first covered Source_Word.
    last_index:
        Zero-based index of the last covered Source_Word.

    Returns
    -------
    str
        The deterministic Source_Span_Id ``s:w{first}-w{last}``.

    Raises
    ------
    ValueError
        If either index is negative or ``first_index > last_index``.
    """
    if first_index > last_index:
        raise ValueError(
            f"first index {first_index} must not exceed last index {last_index}"
        )
    return source_span_id([source_word_id(first_index), source_word_id(last_index)])
