"""Context_Gate — Context_Signal detection for person-name corrections (Req 7).

A pure module — no engine state, no side effects — so every function here is
unit- and property-testable in isolation and reusable by the correction-
emission sites in ``app/correction/engine.py``. It mirrors the pure style of
``app/correction/capitalization.py`` and ``app/correction/confidence.py``.

What this module owns
---------------------
The **Context_Gate** requires evidence that a Span refers to a *person* before
an Approximate_Strategy match on a person entity is accepted (Req 7.2). A stray
ordinary word in the middle of a policy sentence that happens to phonetically
resemble an MP's name must not be rewritten into that MP unless the surrounding
text supports a person reading. This module answers one question purely:

    Is there a **Context_Signal** for this Span?

A Context_Signal is exactly one of the four forms enumerated in Req 7.1:

  (a) a **Title_Prefix** in the token immediately preceding the Span, with no
      intervening token other than punctuation ("Hon. Mensah");
  (b) the adjacent token pair **"Member" "for"** within the Context_Window;
  (c) a **constituency** entity matched within the Context_Window by a
      Deterministic_Strategy ("the Member for Tema East");
  (d) an uppercase **Capitalization_Prior** on the first Word of the Span.

Title_Prefix tokens and the words "Member"/"for" are compared case-insensitively
(Req 7.1). Signal (d) reuses :func:`app.correction.capitalization.span_cap_prior`.

The Context_Window (Req 7.4, 7.5)
---------------------------------
Signals (b) and (c) are searched within a **Context_Window**:

  * **Speaker_Turn** (Req 7.4). When the covered Words carry a speaker
    attribution (a ``speaker`` field), the window is the enclosing
    Speaker_Turn — the contiguous run of Words attributed to that same speaker,
    bounded by the first and last such Word around the Span with no
    interruption by a different speaker.
  * **Token window** (Req 7.5). When the Words lack a speaker attribution, or
    the Words list is empty, the window is ``Context_Window_Words`` tokens
    before and after the Span, truncated at the transcript start and end.

:func:`context_window` returns the ``range`` of Word indices forming the window.

Gate activation and baseline equivalence (Req 12.9)
---------------------------------------------------
This module computes signals purely; it does **not** decide activation. The
engine applies the Context_Gate only when the :class:`~app.correction.gates.GateContext`
is non-inert **and** ``context_gate_enabled`` is set — the same activation
pattern as the Confidence_Gate and Lexicon_Gate. When inert (every flag off) no
context requirement applies, so the flag-off output stays byte-for-byte Baseline
(Req 12.9, Property 12). See :func:`context_gate_rejects_person` for the single
gate decision the engine calls, and the engine for the wiring.

Signal (c) — constituency in window — is read-only and efficient: it consults
the same deterministic match functions (``match_exact``/``match_fused``/
``match_initials``) already built over the Dataset_Cache index, scanning only
the small window of tokens rather than the whole transcript, and stops at the
first constituency hit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.correction.blocklist import is_title
from app.correction.capitalization import CapPrior, span_cap_prior
from app.correction.strategies import (
    MatchResult,
    match_exact,
    match_fused,
    match_initials,
)
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex

# The constituency EntityType value (see app/models/entities.py). A person match
# needs a constituency *within the window* as one of its Context_Signals
# (Req 7.1c) — a constituency is a location subtype, so it is identified by
# ``entity_type`` rather than ``entity_kind``.
_CONSTITUENCY_TYPE = "constituency"

# Leading/trailing punctuation stripped from a token before a case-insensitive
# text comparison for the "Member"/"for" and constituency scans.
_EDGE_PUNCT = ".,;:!?\"'()[]{}"


def _token_text(word: dict[str, Any]) -> str:
    """The Word's surface text as a string, or ``""`` when absent/non-string."""
    value = word.get("word", "")
    return value if isinstance(value, str) else ""


def _is_punctuation_only(token: str) -> bool:
    """True when *token* holds no alphanumeric character (Req 7.1a)."""
    return not any(ch.isalnum() for ch in token)


def _speaker(word: dict[str, Any]) -> Any | None:
    """The Word's speaker attribution, or ``None`` when absent.

    Deepgram emits a ``speaker`` field (an integer label) on diarized output;
    it rides through the ``extra="allow"`` Word model untouched. Any non-``None``
    value is treated as a speaker label (Req 7.4).
    """
    return word.get("speaker")


def context_window(
    span_start_index: int,
    span_len: int,
    words: Sequence[dict[str, Any]],
    window_words: int,
) -> range:
    """The Context_Window Word indices for a Span (Req 7.4, 7.5).

    Parameters
    ----------
    span_start_index:
        Index in *words* of the Span's first covered Word.
    span_len:
        Number of Words the Span covers.
    words:
        The full Words list of the request.
    window_words:
        Context_Window_Words — the token count either side of the Span used for
        the fallback window (Req 7.5).

    Returns
    -------
    range
        The half-open range of Word indices forming the window, always
        including the Span's own indices.

        * **Speaker_Turn** (Req 7.4): when the Span's covered Words carry a
          speaker attribution, the range spans the enclosing Speaker_Turn — the
          maximal contiguous run of Words sharing the Span's speaker, extended
          outward from the Span until a differently-attributed Word interrupts
          it.
        * **Token window** (Req 7.5): when the covered Words omit a speaker
          attribution, or *words* is empty, the range is the *window_words*
          tokens before and after the Span, truncated at the transcript start
          and end.
    """
    n = len(words)
    if n == 0:
        return range(0, 0)

    # Clamp the Span bounds into the Words list. A Span derived from the
    # transcript-text path may not align 1:1 with the Words list; the clamp
    # keeps the window well-formed either way.
    start = max(0, min(span_start_index, n))
    end = max(start, min(span_start_index + max(span_len, 1), n))

    covered = words[start:end]

    # Speaker_Turn window (Req 7.4): only when the covered Words actually carry
    # a speaker attribution and it is consistent across the Span. A Span whose
    # covered Words disagree on speaker, or carry none, falls back to the token
    # window (Req 7.5).
    span_speaker = _consistent_speaker(covered)
    if span_speaker is not None:
        lo = start
        while lo - 1 >= 0 and _speaker(words[lo - 1]) == span_speaker:
            lo -= 1
        hi = end
        while hi < n and _speaker(words[hi]) == span_speaker:
            hi += 1
        return range(lo, hi)

    # Token window (Req 7.5): +/- window_words, truncated at bounds.
    lo = max(0, start - window_words)
    hi = min(n, end + window_words)
    return range(lo, hi)


def _consistent_speaker(covered_words: Sequence[dict[str, Any]]) -> Any | None:
    """The single speaker attribution shared by every covered Word, else ``None``.

    Returns the shared speaker label when every covered Word carries the same
    non-``None`` ``speaker`` (Req 7.4). Returns ``None`` when the covered set is
    empty, when any covered Word omits a speaker, or when the covered Words
    disagree — in which case the caller uses the token-window fallback (Req 7.5).
    """
    if not covered_words:
        return None
    first = _speaker(covered_words[0])
    if first is None:
        return None
    for word in covered_words[1:]:
        if _speaker(word) != first:
            return None
    return first


def _title_prefix_precedes(
    span_start_index: int,
    words: Sequence[dict[str, Any]],
    snapshot: DatasetSnapshot,
) -> bool:
    """Signal (a): a Title_Prefix immediately precedes the Span (Req 7.1a).

    Scans backward from the Word before the Span, skipping punctuation-only
    tokens (Req 7.1a allows only punctuation to intervene). The first
    non-punctuation token found must be a Title_Prefix; any other intervening
    token breaks the adjacency. Comparison is case-insensitive via
    :func:`app.correction.blocklist.is_title`.
    """
    j = span_start_index - 1
    while j >= 0:
        token = _token_text(words[j])
        if _is_punctuation_only(token):
            j -= 1
            continue
        return is_title(token, snapshot)
    return False


def _member_for_in_window(
    window: range,
    words: Sequence[dict[str, Any]],
) -> bool:
    """Signal (b): the adjacent pair "Member" "for" occurs in the window (Req 7.1b).

    Compares both tokens case-insensitively, ignoring leading/trailing
    punctuation on each so "Member" and "for," still match.
    """
    prev_is_member = False
    for idx in window:
        token = _token_text(words[idx]).strip(_EDGE_PUNCT).lower()
        if prev_is_member and token == "for":
            return True
        prev_is_member = token == "member"
    return False


def _constituency_in_window(
    window: range,
    words: Sequence[dict[str, Any]],
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    span_range: range,
) -> bool:
    """Signal (c): a constituency matches deterministically in the window (Req 7.1c).

    Scans the window's tokens (excluding the Span's own Words) and runs the
    Deterministic_Strategy chain (``exact`` -> ``fused`` -> ``initials``) on
    single tokens and adjacent token pairs. Accepts on the first match whose
    entity is a constituency (``entity_type == "constituency"``). Read-only and
    bounded to the window, so its cost is independent of the total alias count
    (Req 15.4) — the deterministic maps are O(1) dict lookups.
    """
    window_indices = [i for i in window if i not in span_range]
    for pos, idx in enumerate(window_indices):
        token = _token_text(words[idx]).strip(_EDGE_PUNCT)
        if not token:
            continue
        if _deterministic_constituency(token.lower(), index):
            return True
        # Adjacent token pair, when the next index is also in the window and
        # contiguous in the transcript (idx + 1).
        if pos + 1 < len(window_indices) and window_indices[pos + 1] == idx + 1:
            next_token = _token_text(words[idx + 1]).strip(_EDGE_PUNCT)
            if next_token:
                phrase = f"{token} {next_token}".lower()
                if _deterministic_constituency(phrase, index):
                    return True
    return False


def _deterministic_constituency(text_lower: str, index: MatchIndex) -> bool:
    """True when *text_lower* matches a constituency by a Deterministic_Strategy.

    Runs only the deterministic key lookups (``exact`` -> ``fused`` ->
    ``initials``); the approximate strategies are deliberately excluded because
    Req 7.1c requires a *Deterministic_Strategy* constituency match.
    """
    for matcher in (match_exact, match_fused, match_initials):
        result: MatchResult | None = matcher(text_lower, index)
        if result is not None and result.entity_type == _CONSTITUENCY_TYPE:
            return True
    return False


def has_context_signal(
    span_start_index: int,
    span_len: int,
    words: Sequence[dict[str, Any]],
    snapshot: DatasetSnapshot,
    index: MatchIndex,
    window_words: int,
    *,
    cap_prior: CapPrior | None = None,
) -> bool:
    """True when any Context_Signal is present for the Span (Req 7.1).

    Parameters
    ----------
    span_start_index:
        Index in *words* of the Span's first covered Word.
    span_len:
        Number of Words the Span covers.
    words:
        The full Words list of the request. May be empty (Req 7.5): the token
        window then covers nothing and only the Capitalization_Prior signal (d)
        can fire — and for an empty Words list ``span_cap_prior`` is Unknown
        (Req 6.5), so no signal fires, matching the "no support" reading.
    snapshot:
        The Dataset_Cache snapshot (title-prefix set, block lists).
    index:
        The Match_Index (deterministic maps for the constituency signal).
    window_words:
        Context_Window_Words for the token-window fallback (Req 7.5).
    cap_prior:
        The precomputed Span Capitalization_Prior for signal (d), or ``None`` to
        compute it here from the Span's covered Words via
        :func:`app.correction.capitalization.span_cap_prior`. Callers that have
        already computed the prior pass it to avoid recomputation.

    Returns
    -------
    bool
        ``True`` when at least one of the four Req 7.1 signals is present:
        (a) a Title_Prefix immediately precedes the Span; (b) "Member" "for"
        occurs in the Context_Window; (c) a constituency matches
        deterministically in the Context_Window; or (d) the Span's first Word
        carries an uppercase Capitalization_Prior.

    Notes
    -----
    The four signals are checked cheapest-first and short-circuit: the
    adjacency check (a) and the uppercase-prior check (d) are O(1)/O(span_len);
    the "Member for" scan (b) and the constituency scan (c) are bounded to the
    Context_Window.
    """
    # (a) Title_Prefix immediately preceding — cheapest, most decisive signal.
    if _title_prefix_precedes(span_start_index, words, snapshot):
        return True

    # (d) Uppercase Capitalization_Prior on the Span's first Word. Compute from
    # the covered Words when the caller did not supply it (Req 7.1d, 6.4).
    if cap_prior is None:
        n = len(words)
        start = max(0, min(span_start_index, n))
        end = max(start, min(span_start_index + max(span_len, 1), n))
        covered = words[start:end]
        prev_word = words[start - 1] if start - 1 >= 0 else None
        cap_prior = span_cap_prior(covered, is_first=start == 0, prev_word=prev_word)
    if cap_prior is CapPrior.UPPERCASE:
        return True

    # (b) and (c) search the Context_Window (Req 7.4/7.5).
    window = context_window(span_start_index, span_len, words, window_words)
    if _member_for_in_window(window, words):
        return True

    span_range = range(
        max(0, span_start_index),
        max(0, span_start_index) + max(span_len, 1),
    )
    return _constituency_in_window(window, words, index, snapshot, span_range)


def context_gate_rejects_person(
    match: MatchResult,
    span_start_index: int,
    span_len: int,
    words: Sequence[dict[str, Any]],
    snapshot: DatasetSnapshot,
    index: MatchIndex,
    window_words: int,
    *,
    is_deterministic: bool,
    cap_prior: CapPrior | None = None,
) -> bool:
    """The Context_Gate decision for one match (Req 7.2, 7.3, 7.7).

    Returns ``True`` when the Context_Gate rejects *match* — i.e. the caller
    must preserve the Span unchanged and evaluate no further Approximate_Strategy
    for it (Req 7.8, 7.9).

    The gate rejects **only** an Approximate_Strategy match on a **person**
    entity for which no Context_Signal is present (Req 7.2). It never rejects:

    * a Deterministic_Strategy match — accepted without a signal (Req 7.3);
    * a match on a non-person entity (location, constituency, party) — accepted
      without a signal (Req 7.7).

    Parameters
    ----------
    match:
        The candidate match produced for the Span.
    is_deterministic:
        ``True`` when *match* came from a Deterministic_Strategy (the caller
        passes ``strategies.is_deterministic(match.strategy)``). Deterministic
        matches are exempt (Req 7.3).
    cap_prior:
        Optional precomputed Span Capitalization_Prior, forwarded to
        :func:`has_context_signal`.

    Notes
    -----
    This function does **not** consult any feature flag: the engine calls it only
    when the GateContext is non-inert and ``context_gate_enabled`` is set, so
    with every flag off no context requirement applies and the output stays
    Baseline (Req 12.9). A preceding Title_Prefix is itself Context_Signal (a),
    so a title-prefixed person match passes the gate and keeps its normal
    confidence — the gate neither lowers the threshold nor changes the assigned
    confidence (Req 7.6, 7.10).
    """
    if is_deterministic:
        return False
    if match.entity_kind != "person":
        return False
    return not has_context_signal(
        span_start_index,
        span_len,
        words,
        snapshot,
        index,
        window_words,
        cap_prior=cap_prior,
    )
