"""Span_Confidence derivation and the Confidence_Gate (Req 1).

A pure module with three responsibilities, all free of engine state so they can
be unit- and property-tested in isolation and reused by later tasks:

* :func:`span_confidence` — the minimum clamped Word_Confidence over the covered
  Words that carry one, or ``UNKNOWN`` (``None``) when none does (Req 1.3, 1.4,
  1.5).
* :func:`align_span_to_words` — pair each Span token to the Word at the same
  sequence position, matching case-insensitively and ignoring leading/trailing
  punctuation; ``None`` (Unknown) when any token has no aligned Word (Req 1.7,
  1.8).
* :func:`confidence_gate_blocks_approximate` — the gate decision: ``True`` iff
  Span_Confidence is known and greater than or equal to the
  High_Confidence_Threshold (Req 1.2, 1.6, 1.11).

Gate activation (resolving the task 2.1 open question)
------------------------------------------------------
Requirement 1 describes the Confidence_Gate as an always-considered behaviour —
it is *not* itself behind one of the five feature flags of Req 12. But Req 12.9
(Property 12) requires that when **every** flag is off the engine reproduces
Baseline output exactly, and rejecting a high-confidence Span from approximate
matching is a genuine behaviour change from Baseline.

The design (design.md section 2, "Span Confidence") and the task both flag this
ambiguity and instruct: if it is ambiguous, make the Confidence_Gate active
**only when the GateContext is non-inert** (i.e. at least one flag is on), so
Property 12 holds. That is the resolution implemented here and in the engine:

* The engine consults the gate **only** when ``not gate_context.is_inert``.
* An inert GateContext (all flags off) takes the Baseline path: the full
  strategy chain runs and no approximate strategy is excluded.

This keeps the gate a first-class always-considered gate whenever the gating
mechanism is engaged at all (any flag on), while guaranteeing byte-for-byte
Baseline equivalence when the whole mechanism is disabled. The engine owns the
``is_inert`` check; this module stays a pure decision function so the same
:func:`confidence_gate_blocks_approximate` can be reused unchanged if a future
task chooses a different activation key.

The Unknown sentinel is ``None`` throughout (Req 1.5), shared with
``app.correction.gates`` so a Span_Confidence value flows through the
``span_confidence_hook`` unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Unknown Span_Confidence sentinel (Req 1.5). ``None`` is used consistently
# across the correction package (see ``app.correction.gates.SpanConfidenceHook``).
UNKNOWN: None = None

# Characters stripped from the leading and trailing edges of a token before an
# alignment comparison (Req 1.7). Mirrors the punctuation the tokenizer in the
# engine already discards; kept deliberately conservative so internal
# separators (hyphens, apostrophes) inside a token are preserved.
_EDGE_PUNCT = ".,;:!?\"'()[]{}<>\u201c\u201d\u2018\u2019\u2026\u2014\u2013-"


def _clamp_unit(value: float) -> float:
    """Clamp *value* to the closed interval [0.0, 1.0]."""
    return max(0.0, min(1.0, float(value)))


def _normalize_token(token: str) -> str:
    """Lowercase *token* and strip leading/trailing punctuation (Req 1.7).

    Internal separators are preserved so a hyphenated or apostrophised name
    aligns to the Word carrying the same surface form.
    """
    return token.strip(_EDGE_PUNCT).lower()


def span_confidence(covered_words: Sequence[dict[str, Any]]) -> float | None:
    """Span_Confidence: min clamped Word_Confidence over covered Words (Req 1.3-1.5).

    Parameters
    ----------
    covered_words:
        The Word dicts the Span covers. Each may carry a ``confidence`` key.

    Returns
    -------
    float | None
        The minimum clamped Word_Confidence across only the covered Words that
        carry a numeric ``confidence`` (Req 1.3, 1.4). ``UNKNOWN`` (``None``)
        when no covered Word carries one, including an empty sequence (Req 1.5).

    Notes
    -----
    A Word whose ``confidence`` is absent, ``None``, or non-numeric is treated
    as carrying no confidence (Req 14.13) and is excluded from the minimum. A
    numeric value outside [0.0, 1.0] is clamped rather than rejected (Req 14.7).
    ``bool`` is excluded explicitly — although ``bool`` is a subclass of
    ``int`` in Python, a boolean is never a valid confidence.
    """
    present: list[float] = []
    for word in covered_words:
        value = word.get("confidence")
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            present.append(_clamp_unit(value))
    if not present:
        return UNKNOWN
    return min(present)


def align_span_to_words(
    span_tokens: Sequence[str],
    words: Sequence[dict[str, Any]],
    start_index: int,
) -> list[dict[str, Any]] | None:
    """Align Span tokens to Words by sequence position (Req 1.7, 1.8).

    Pairs each Span token with the Word at the same sequence position beginning
    at *start_index*: the k-th Span token pairs with ``words[start_index + k]``.
    A pair matches when the two surface forms are equal after lowercasing and
    stripping leading/trailing punctuation (Req 1.7).

    Parameters
    ----------
    span_tokens:
        The tokens making up the Span, in order.
    words:
        The full Words list of the request.
    start_index:
        The index in *words* at which the Span begins.

    Returns
    -------
    list[dict] | None
        The list of aligned Word dicts (one per Span token, in order) when
        every token aligns. ``None`` (Unknown) when the Span tokens and the
        Words list diverge in count, position, or matched text — that is, when
        the Span runs past the end of *words*, when *start_index* is out of
        range, when *span_tokens* is empty, or when any token's normalized form
        does not equal the normalized form of the Word at its position
        (Req 1.8).
    """
    if not span_tokens:
        return None
    if start_index < 0:
        return None
    if start_index + len(span_tokens) > len(words):
        return None

    aligned: list[dict[str, Any]] = []
    for offset, token in enumerate(span_tokens):
        word = words[start_index + offset]
        word_text = word.get("word", "")
        if not isinstance(word_text, str):
            return None
        if _normalize_token(token) != _normalize_token(word_text):
            return None
        aligned.append(word)
    return aligned


def confidence_gate_blocks_approximate(
    conf: float | None,
    high_threshold: float,
) -> bool:
    """The Confidence_Gate decision (Req 1.2, 1.6, 1.11).

    Parameters
    ----------
    conf:
        The Span_Confidence, or ``UNKNOWN`` (``None``).
    high_threshold:
        The resolved High_Confidence_Threshold.

    Returns
    -------
    bool
        ``True`` iff Span_Confidence is known and greater than or equal to
        *high_threshold*, in which case every Approximate_Strategy member is
        excluded for that Span (Req 1.2, 1.11). ``False`` when Span_Confidence
        is Unknown, so every Approximate_Strategy the remaining gates permit is
        evaluated (Req 1.6).
    """
    return conf is not None and conf >= high_threshold
