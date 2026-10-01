"""English_Lexicon loader and membership check for the Lexicon_Gate.

The Lexicon_Gate (Requirement 2 of the ``correction-precision-gating`` spec)
identifies Spans that are ordinary English vocabulary — words the ASR is
unlikely to have mangled from a Ghanaian proper noun — and keeps them out of
Approximate_Strategy evaluation. This module owns two responsibilities:

* :class:`EnglishLexicon` — a frozen, process-shared set of lowercase word
  forms with a case-and-punctuation-insensitive :meth:`~EnglishLexicon.contains`
  check (Req 2.10). It also carries a :attr:`~EnglishLexicon.loaded` flag so the
  engine can tell an active lexicon from an inactive one produced by a load
  failure (Req 2.13).

* :func:`load_lexicon` — loads the bundled artifact once per process with no
  network access (Req 2.2). On absence, unreadability, a short list
  (< :data:`MIN_LEXICON_SIZE` forms), or a load that exceeds ``timeout_s``, it
  logs one ``lexicon.load_failed`` error and returns an inactive lexicon
  (Req 2.11) so the service keeps serving requests with the gate off.

This module implements the loader and the artifact handle only. The gate
*decision* — when to reject a Span — lives in the engine (task 2.4); this task
only makes the lexicon loadable and available.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path

import structlog

logger = structlog.get_logger("lexicon")

# Minimum number of single-token forms an artifact must yield to be usable
# (Req 2.4). A shorter list is treated as a load failure (Req 2.11).
MIN_LEXICON_SIZE: int = 25_000

# Leading/trailing punctuation stripped from a token before membership check
# (Req 2.10). Interior characters are left intact so multi-part tokens still
# compare faithfully.
_PUNCT: str = " \t\r\n.,;:!?\"'`()[]{}<>…—–-_/\\|@#$%^&*+=~"

# The bundled artifact: one lowercase, whitespace-free token per line.
DEFAULT_LEXICON_PATH: Path = Path(__file__).parent / "data" / "english_lexicon.txt"


class EnglishLexicon:
    """A process-shared set of ordinary lowercase English word forms.

    Instances are immutable after construction: the word set is a
    :class:`frozenset` and :attr:`loaded` never changes. One instance is loaded
    per process (Req 2.3) and shared across every request handled by that
    process, so membership checks are lock-free reads.

    :attr:`loaded` is ``True`` for a lexicon that met the size floor and
    ``False`` for the inactive lexicon returned on a load failure. The engine
    uses it to decide whether the Lexicon_Gate is active (Req 2.13).
    """

    __slots__ = ("_words", "loaded")

    def __init__(self, words: frozenset[str], loaded: bool) -> None:
        self._words = words
        self.loaded = loaded

    def contains(self, token: str) -> bool:
        """Return ``True`` when *token* is an ordinary English word form.

        The token is lowercased and stripped of leading and trailing
        punctuation before the membership check (Req 2.10), so ``"Thank,"``
        and ``"thank"`` compare identically. An empty result after stripping
        is never a member.
        """
        normalized = token.lower().strip(_PUNCT)
        if not normalized:
            return False
        return normalized in self._words

    def __len__(self) -> int:
        """Number of distinct word forms held (0 for an inactive lexicon)."""
        return len(self._words)

    def __contains__(self, token: str) -> bool:
        return self.contains(token)


def _inactive_lexicon() -> EnglishLexicon:
    """Return the inactive lexicon used on any load failure (Req 2.11, 2.13)."""
    return EnglishLexicon(words=frozenset(), loaded=False)


def load_lexicon(
    path: Path | str = DEFAULT_LEXICON_PATH,
    timeout_s: float = 10.0,
) -> EnglishLexicon:
    """Load the English_Lexicon from a bundled artifact, without any network.

    Reads *path* — one lowercase, whitespace-free token per line — and returns
    an active :class:`EnglishLexicon` when it yields at least
    :data:`MIN_LEXICON_SIZE` forms (Req 2.4). Each line is lowercased and
    stripped of surrounding whitespace; blank lines and lines that still
    contain interior whitespace after stripping are skipped so every stored
    form is a single token (Req 2.4).

    On any of the following the function logs exactly one ``lexicon.load_failed``
    error and returns an inactive lexicon (``loaded=False``, empty set) so the
    service continues serving requests with the Lexicon_Gate off (Req 2.11):

    * the artifact is absent or is not a regular file;
    * the artifact cannot be read (permission or decode error);
    * fewer than :data:`MIN_LEXICON_SIZE` usable forms are found;
    * reading does not finish within ``timeout_s`` seconds of the call.

    The read is performed with no network access (Req 2.2): it only opens a
    local file path.
    """
    lexicon_path = Path(path)
    started = time.monotonic()

    def _elapsed_over_budget() -> bool:
        return (time.monotonic() - started) > timeout_s

    try:
        if not lexicon_path.is_file():
            logger.error(
                "lexicon.load_failed",
                reason="absent",
                path=str(lexicon_path),
            )
            return _inactive_lexicon()

        words: set[str] = set()
        with lexicon_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                # Bound the load by wall-clock time (Req 2.11). Checking each
                # line keeps the guard cheap while still catching a stall on a
                # slow or oversized artifact.
                if _elapsed_over_budget():
                    logger.error(
                        "lexicon.load_failed",
                        reason="timeout",
                        path=str(lexicon_path),
                        timeout_s=timeout_s,
                    )
                    return _inactive_lexicon()

                token = line.strip().lower()
                if not token or any(ch.isspace() for ch in token):
                    continue
                words.add(token)

        if len(words) < MIN_LEXICON_SIZE:
            logger.error(
                "lexicon.load_failed",
                reason="short",
                path=str(lexicon_path),
                loaded_forms=len(words),
                minimum=MIN_LEXICON_SIZE,
            )
            return _inactive_lexicon()

        if _elapsed_over_budget():
            logger.error(
                "lexicon.load_failed",
                reason="timeout",
                path=str(lexicon_path),
                timeout_s=timeout_s,
            )
            return _inactive_lexicon()

        return EnglishLexicon(words=frozenset(words), loaded=True)

    except OSError:
        logger.error(
            "lexicon.load_failed",
            reason="unreadable",
            path=str(lexicon_path),
            exc_info=True,
        )
        return _inactive_lexicon()
    except UnicodeDecodeError:
        logger.error(
            "lexicon.load_failed",
            reason="unreadable",
            path=str(lexicon_path),
            exc_info=True,
        )
        return _inactive_lexicon()


def lexicon_gate_blocks_approximate(
    tokens: Sequence[str],
    span_conf: float | None,
    *,
    lexicon: EnglishLexicon | None,
    is_alias: bool,
    override_threshold: float,
    reject_unknown: bool = True,
) -> bool:
    """Decide whether the Lexicon_Gate rejects a Span for approximate matching.

    Pure decision function for the Lexicon_Gate (Req 2.5-2.8, 2.10, 2.13). The
    engine owns the gate *activation* (the ``lexicon_gate_enabled`` flag on and
    the context non-inert) and supplies the alias-membership result; this
    function evaluates the membership-and-confidence condition only.

    The gate rejects every Approximate_Strategy member for the Span when **all**
    of the following hold:

    * a lexicon is present and loaded successfully (Req 2.13);
    * the Span is absent from the Dataset_Cache alias set (``is_alias`` is
      ``False``; Req 2.5, 2.8);
    * every token is a member of the lexicon (Req 2.8) — for a single-token
      Span this is just that token (Req 2.5);
    * Span_Confidence is Unknown (``None``) **and** ``reject_unknown`` is
      ``True`` (Req 2.6, the ``deepgram`` policy) **or** Span_Confidence is
      known and greater than or equal to ``override_threshold`` (Req 2.5).

    A known Span_Confidence strictly below ``override_threshold`` permits
    approximate evaluation (Req 2.7), so this returns ``False`` for that case.
    Membership is evaluated on each token lowercased and stripped of leading and
    trailing punctuation, which :meth:`EnglishLexicon.contains` performs
    (Req 2.10).

    The Unknown-confidence outcome is provider-calibrated (task 2.12.1). When
    ``reject_unknown`` is ``True`` (the default and the ``deepgram`` policy) an
    Unknown-confidence Span is rejected exactly as Req 2.6 mandates. When it is
    ``False`` (the ``khaya``/``hybrid`` policy, active only when
    ``provider_profiles_enabled`` is on) an Unknown-confidence Span is **not**
    rejected on that basis — a provider that structurally cannot supply per-word
    confidence is not blanket-blocked — so approximate evaluation is permitted
    for it (returns ``False``).

    Parameters
    ----------
    tokens:
        The tokens making up the Span, in order. An empty sequence never blocks.
    span_conf:
        The Span_Confidence, or ``None`` for Unknown (Req 1.5).
    lexicon:
        The process-global English_Lexicon handle, or ``None``. A ``None`` or
        not-``loaded`` lexicon leaves the gate inactive (Req 2.13).
    is_alias:
        ``True`` when the Span (as a whole) is present in the Dataset_Cache
        alias set. An alias is never gated (Req 2.5, 2.8).
    override_threshold:
        The resolved Lexicon_Override_Threshold (Req 2.5, 2.7).
    reject_unknown:
        The provider-calibrated Unknown-confidence policy (task 2.12.1).
        ``True`` (default, ``deepgram``) rejects an Unknown-confidence Span per
        Req 2.6; ``False`` (``khaya``/``hybrid`` with ``provider_profiles_enabled``
        on) permits approximate evaluation for an Unknown-confidence Span.

    Returns
    -------
    bool
        ``True`` when the Lexicon_Gate rejects the Span for every
        Approximate_Strategy member; ``False`` otherwise.
    """
    if lexicon is None or not lexicon.loaded:
        return False
    if not tokens:
        return False
    if is_alias:
        return False
    if not all(lexicon.contains(token) for token in tokens):
        return False
    # Confidence condition: Unknown gates only under the ``reject_unknown``
    # policy (Req 2.6 for ``deepgram``; task 2.12.1 exempts ``khaya``/``hybrid``);
    # a known confidence gates only at or above the override (Req 2.5), and
    # below it permits approximate evaluation (Req 2.7).
    if span_conf is None:
        return reject_unknown
    return span_conf >= override_threshold
