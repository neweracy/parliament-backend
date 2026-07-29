"""Correction_Engine — correct_single, correct_text, correct_words, sort key, party display.

Provides the single-token correction entry point that short-circuits through
the strategy chain in the documented order, the deterministic tie-break sort
key for choosing among competing corrections across overlapping spans, the
party display heuristic wrapper, the ``correct_text`` function that ports
the JavaScript ``correctLocations`` algorithm, and the ``correct_words``
function that ports the word-level n-gram joining loop from
``formatTranscriptionResponse``.

Requirements: 3.1, 3.2, 3.9, 3.10, 3.11, 3.12, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.correction.blocklist import is_stopword, is_title, is_word_stopword
from app.correction.scoring import JOINED_CONFIDENCE, STRATEGY_RANK
from app.correction.strategies import (
    MIN_CANDIDATE_LENGTH,
    MatchResult,
    get_party_display,
    match_exact,
    match_fused,
    match_fuzzy,
    match_initials,
    match_phonetic,
    match_substring,
)
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex


def correct_single(
    text: str,
    span_len: int,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
) -> MatchResult | None:
    """Run the short-circuiting strategy chain for a single text span.

    Behaviour:
      1. Lowercase the text.
      2. If the lowercased text is a stopword → return None immediately.
      3. If len(text_lower) < MIN_CANDIDATE_LENGTH (4): try only
         ``match_exact`` and ``match_fused``, then return.
      4. Otherwise, try each strategy in order, returning on first hit:
         exact → fused → initials → phonetic → fuzzy → substring.
      5. If no strategy matches, return None.

    Parameters
    ----------
    text : str
        The raw text span to correct (not yet lowercased).
    span_len : int
        The number of tokens this span covers (used externally for
        tie-breaking, not consumed here).
    index : MatchIndex
        The derived lookup structures built from the Dataset_Cache.
    snapshot : DatasetSnapshot
        The immutable dataset snapshot (provides stopword/block lists).

    Returns
    -------
    MatchResult | None
        The best match if any strategy succeeds, otherwise None.
    """
    text_lower = text.lower()

    # Stopword guard — checked before any strategy (Requirement 4.7)
    if is_stopword(text_lower, snapshot):
        return None

    # Short inputs (< 4 chars): only exact and fused are tried
    if len(text_lower) < MIN_CANDIDATE_LENGTH:
        result = match_exact(text_lower, index)
        if result is not None:
            return result
        result = match_fused(text_lower, index)
        if result is not None:
            return result
        return None

    # Full strategy chain — short-circuit on first hit
    result = match_exact(text_lower, index)
    if result is not None:
        return result

    result = match_fused(text_lower, index)
    if result is not None:
        return result

    result = match_initials(text_lower, index)
    if result is not None:
        return result

    result = match_phonetic(text_lower, index)
    if result is not None:
        return result

    result = match_fuzzy(text_lower, index, snapshot)
    if result is not None:
        return result

    result = match_substring(text_lower, index)
    if result is not None:
        return result

    return None


def correction_sort_key(result: MatchResult, span_len: int) -> tuple:
    """Deterministic sort key for selecting among competing corrections.

    Lower value = better match. Applied when multiple candidates exist
    for overlapping spans.

    sort_key = (-round(confidence, 6), -span_len, STRATEGY_RANK[strategy], canonical)

    Tie-break order (most significant first):
      1. Higher confidence wins (negated so lower tuple value = better).
      2. Longer span wins (negated).
      3. Lower strategy rank wins (exact < fused < joined < ... < substring).
      4. Lexicographic canonical name for full determinism.

    Parameters
    ----------
    result : MatchResult
        The match result to compute the sort key for.
    span_len : int
        The number of tokens the matched span covers.

    Returns
    -------
    tuple
        A tuple suitable for ``min()`` or ``sorted()`` comparisons.
    """
    return (
        -round(result.confidence, 6),
        -span_len,
        STRATEGY_RANK.get(result.strategy, 99),
        result.canonical,
    )


def apply_party_display(
    result: MatchResult, original_text: str, index: MatchIndex
) -> MatchResult:
    """Apply party display heuristic — abbreviation vs full name.

    For party entities, determines whether to show the abbreviation
    (when the original input is short, i.e. ``len(original) <= len(abbr) + 1``)
    or the canonical full name.

    Non-party entities are returned unchanged.

    Parameters
    ----------
    result : MatchResult
        The match result to potentially transform.
    original_text : str
        The original text that was matched (pre-lowercasing).
    index : MatchIndex
        The match index containing the party abbreviation map.

    Returns
    -------
    MatchResult
        Either the original result (non-party or no abbreviation found)
        or a new MatchResult with the display-appropriate canonical name.
    """
    if result.entity_kind != "party":
        return result

    display = get_party_display(original_text, result.canonical, index)
    return MatchResult(
        canonical=display,
        confidence=result.confidence,
        strategy=result.strategy,
        entity_kind=result.entity_kind,
        entity_type=result.entity_type,
    )


# ---------------------------------------------------------------------------
# Token representation for character-offset tokenization
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z\u00C0-\u00FF'\-]+")


@dataclass
class _Token:
    """One whitespace-delimited token with its character offsets."""

    word: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# TextCorrection and TextCorrectionResult
# ---------------------------------------------------------------------------


@dataclass
class TextCorrection:
    """One correction applied to the text."""

    start_char: int  # Character offset of the original span start
    end_char: int  # Character offset of the original span end
    original: str  # The original text that was replaced
    replacement: str  # The corrected replacement text
    entity_kind: str  # EntityKind value
    entity_type: str  # EntityType value
    strategy: str  # MatchStrategy value
    confidence: float  # Match confidence


@dataclass
class TextCorrectionResult:
    """Result of correct_text containing the corrected text and metadata."""

    text: str  # The corrected text
    corrections: list[TextCorrection]  # Applied corrections
    entities_found: list[tuple[str, str, str]]  # (canonical, kind, type) including identity matches


# ---------------------------------------------------------------------------
# Title-person matching (Level 2 strategy)
# ---------------------------------------------------------------------------


def _match_title_person(
    tokens: list[_Token],
    title_index: int,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    min_confidence: float,
) -> tuple[MatchResult, int] | None:
    """Try to match person name tokens following a title token.

    Tries windows of 3, 2, 1 tokens after the title. On match, returns
    the MatchResult and the number of name tokens consumed.

    This is a simplified version for task 6.5 — the full title-person
    path with surname/phonetic/fuzzy fallback is task 6.6.
    """
    threshold = min(min_confidence, 0.65)
    max_lookahead = min(3, len(tokens) - title_index - 1)

    if max_lookahead < 1:
        return None

    # Try window sizes from largest to smallest
    for win_size in range(max_lookahead, 0, -1):
        name_tokens = tokens[title_index + 1 : title_index + 1 + win_size]
        phrase = " ".join(t.word for t in name_tokens)

        # Skip if single-token window is a stopword
        if win_size == 1 and is_stopword(phrase.lower(), snapshot):
            continue

        # Skip if the last token in the window is a stopword
        if win_size > 1 and is_stopword(name_tokens[-1].word.lower(), snapshot):
            continue

        match = correct_single(phrase, win_size, index, snapshot)
        if match and match.entity_kind == "person" and match.confidence >= threshold:
            return (match, win_size)

    # Surname-only fallback: try single token after title via surname_map
    last_token = tokens[title_index + 1] if title_index + 1 < len(tokens) else None
    if last_token:
        surname = last_token.word.lower()
        candidates = index.surname_map.get(surname)
        if candidates:
            canonical = candidates[0]
            kind = index.entity_kind_map.get(canonical)
            if kind == "person":
                entity_type = index.entity_type_map.get(canonical, "person")
                result = MatchResult(
                    canonical=canonical,
                    confidence=0.90,
                    strategy="title_person",
                    entity_kind="person",
                    entity_type=entity_type,
                )
                return (result, 1)

        # Phonetic then fuzzy on single token after title
        phonetic_match = match_phonetic(surname, index)
        if phonetic_match and phonetic_match.entity_kind == "person" and phonetic_match.confidence >= threshold:
            return (phonetic_match, 1)

        fuzzy_match = match_fuzzy(surname, index, snapshot)
        if fuzzy_match and fuzzy_match.entity_kind == "person" and fuzzy_match.confidence >= threshold:
            return (fuzzy_match, 1)

    return None


# ---------------------------------------------------------------------------
# correct_text — port of JS correctLocations
# ---------------------------------------------------------------------------


def correct_text(
    text: str,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    *,
    min_confidence: float = 0.75,
) -> TextCorrectionResult:
    """Correct all entity references in a transcript text.

    Ports the JavaScript ``correctLocations`` function. Scans 1–4 word
    n-grams at each position, applies title-person branch first, then the
    main strategy chain, and returns the corrected text with metadata.

    Algorithm:
      1. Tokenize text by character offsets using regex [A-Za-zÀ-ÿ'-]+
      2. For each position, check title-person branch first
      3. Then try n-gram windows (4→1) with joined fallback for n > 1
      4. Record identity matches in entities_found
      5. Apply corrections end-to-start

    Parameters
    ----------
    text : str
        The full transcript text to correct.
    index : MatchIndex
        The derived lookup structures from the Dataset_Cache.
    snapshot : DatasetSnapshot
        The immutable dataset snapshot (block lists, stopwords, etc.).
    min_confidence : float
        Minimum confidence threshold to accept a correction (default 0.75).

    Returns
    -------
    TextCorrectionResult
        The corrected text, list of corrections applied, and entities found.
    """
    if not text or not text.strip():
        return TextCorrectionResult(text=text, corrections=[], entities_found=[])

    # --- Step 1: Tokenize with character offsets ---
    tokens: list[_Token] = []
    for m in _TOKEN_RE.finditer(text):
        tokens.append(_Token(word=m.group(), start=m.start(), end=m.end()))

    if not tokens:
        return TextCorrectionResult(text=text, corrections=[], entities_found=[])

    corrections: list[TextCorrection] = []
    entities_found: list[tuple[str, str, str]] = []
    consumed: set[int] = set()

    i = 0
    while i < len(tokens):
        if i in consumed:
            i += 1
            continue

        # --- Step 2: Title-person branch (checked first) ---
        if is_title(tokens[i].word, snapshot):
            title_result = _match_title_person(tokens, i, index, snapshot, min_confidence)
            if title_result:
                match, tokens_consumed = title_result

                # Name tokens start after the title
                name_start = i + 1
                name_slice = tokens[name_start : name_start + tokens_consumed]

                if name_slice:
                    original = text[name_slice[0].start : name_slice[-1].end]

                    # Double-title guard: strip leading word from canonical if it
                    # matches the title token to avoid duplication
                    corrected_name = match.canonical
                    title_word = tokens[i].word.lower()
                    canonical_first_word = corrected_name.split()[0].lower() if corrected_name.split() else ""
                    if title_word == canonical_first_word:
                        # Strip the first word from the canonical
                        parts = corrected_name.split(None, 1)
                        corrected_name = parts[1] if len(parts) > 1 else corrected_name

                    is_identity = corrected_name.lower() == original.lower()

                    # Record entity in entities_found regardless
                    entity_kind = match.entity_kind or "person"
                    entity_type = match.entity_type or "person"
                    entities_found.append((match.canonical, entity_kind, entity_type))

                    if not is_identity:
                        # Emit correction for the name tokens only (title stays as-is)
                        corrections.append(
                            TextCorrection(
                                start_char=name_slice[0].start,
                                end_char=name_slice[-1].end,
                                original=original,
                                replacement=corrected_name,
                                entity_kind=entity_kind,
                                entity_type=entity_type,
                                strategy=match.strategy,
                                confidence=match.confidence,
                            )
                        )

                    # Mark title and name tokens as consumed
                    consumed.add(i)
                    for j in range(tokens_consumed):
                        consumed.add(name_start + j)

                    # Advance past consumed tokens
                    i += tokens_consumed + 1
                    continue

        # --- Step 3: Main n-gram windows (4→1) ---
        best_match: MatchResult | None = None
        best_ngram_size = 0
        best_confidence = 0.0

        max_n = min(4, len(tokens) - i)
        for n in range(max_n, 0, -1):
            # Skip if any token in this window is already consumed
            if any(i + j in consumed for j in range(n)):
                continue

            token_slice = tokens[i : i + n]
            phrase = " ".join(t.word for t in token_slice)

            # N-gram stopword edge guards (only for n > 1)
            if n > 1:
                first_word = token_slice[0].word.lower().rstrip(".")
                last_word = token_slice[-1].word.lower().rstrip(".")
                first_is_initial = len(token_slice[0].word) <= 2 and re.match(
                    r"^[a-z]\.?$", token_slice[0].word, re.IGNORECASE
                )
                first_is_title_prefix = is_title(token_slice[0].word, snapshot)
                if not first_is_initial and not first_is_title_prefix and is_stopword(first_word, snapshot):
                    continue
                if is_stopword(last_word, snapshot):
                    continue

            # Strategy A: try the phrase as-is via correct_single
            match = correct_single(phrase, n, index, snapshot)

            # Strategy B: joined match (for n > 1 when correct_single fails)
            if match is None and n > 1:
                joined_text = "".join(t.word for t in token_slice).lower()
                joined_text_stripped = re.sub(r"[\s\-']", "", joined_text)
                canonical = index.fused_map.get(joined_text_stripped)
                if canonical:
                    entity_kind = index.entity_kind_map.get(canonical, "location")
                    entity_type = index.entity_type_map.get(canonical, "supplementary")
                    match = MatchResult(
                        canonical=canonical,
                        confidence=JOINED_CONFIDENCE,
                        strategy="joined",
                        entity_kind=entity_kind,
                        entity_type=entity_type,
                    )

            # Apply party display heuristic
            if match is not None:
                match = apply_party_display(match, phrase, index)

            # Accept if it meets the confidence threshold
            if match and match.confidence >= min_confidence:
                is_identity = match.canonical.lower() == phrase.lower()

                if is_identity:
                    # Already correctly spelled — record as recognized entity
                    entities_found.append(
                        (match.canonical, match.entity_kind, match.entity_type)
                    )
                    for j in range(n):
                        consumed.add(i + j)
                    # Identity match found — don't try smaller windows
                    best_match = None
                    break

                if match.confidence > best_confidence:
                    best_match = match
                    best_ngram_size = n
                    best_confidence = match.confidence

        # --- Step 4: Record the best match as a correction ---
        if best_match:
            token_slice = tokens[i : i + best_ngram_size]
            original = text[token_slice[0].start : token_slice[-1].end]

            entity_kind = best_match.entity_kind or "location"
            entity_type = best_match.entity_type or "supplementary"

            corrections.append(
                TextCorrection(
                    start_char=token_slice[0].start,
                    end_char=token_slice[-1].end,
                    original=original,
                    replacement=best_match.canonical,
                    entity_kind=entity_kind,
                    entity_type=entity_type,
                    strategy=best_match.strategy,
                    confidence=best_match.confidence,
                )
            )
            entities_found.append((best_match.canonical, entity_kind, entity_type))
            for j in range(best_ngram_size):
                consumed.add(i + j)
        elif i not in consumed:
            consumed.add(i)

        i += 1

    # --- Step 5: Apply corrections end-to-start to preserve offsets ---
    result = text
    sorted_corrections = sorted(corrections, key=lambda c: c.start_char, reverse=True)
    for c in sorted_corrections:
        result = result[: c.start_char] + c.replacement + result[c.end_char :]

    return TextCorrectionResult(
        text=result,
        corrections=corrections,
        entities_found=entities_found,
    )


# ---------------------------------------------------------------------------
# WordCorrectionResult and correct_words — word-level n-gram joining
# ---------------------------------------------------------------------------

# Minimum token length for word-level matching (shorter tokens are skipped
# unless they match a party abbreviation of length 3-4)
_WORD_MIN_TOKEN_LENGTH = 3


@dataclass
class WordCorrectionResult:
    """Result of correct_words.

    Attributes
    ----------
    words : list[dict]
        Output word dicts (may be fewer than input if tokens were joined).
    corrections : list[tuple[str, str, str, float, str, str]]
        Each tuple is (original, corrected, strategy, confidence, kind, type).
    entities_found : list[tuple[str, str, str]]
        Each tuple is (canonical, kind, type) for recognized entities
        including identity matches.
    """

    words: list[dict]
    corrections: list[tuple[str, str, str, float, str, str]]
    entities_found: list[tuple[str, str, str]]


def _match_title_person_words(
    word_dicts: list[dict],
    title_index: int,
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    min_confidence: float,
) -> tuple[MatchResult, int] | None:
    """Try to match person name tokens following a title in word dicts.

    Similar to _match_title_person but operates on word dicts rather
    than _Token objects. Returns (MatchResult, name_tokens_consumed) or None.
    """
    threshold = min(min_confidence, 0.65)
    max_lookahead = min(3, len(word_dicts) - title_index - 1)

    if max_lookahead < 1:
        return None

    for win_size in range(max_lookahead, 0, -1):
        name_words = word_dicts[title_index + 1: title_index + 1 + win_size]
        phrase = " ".join(wd.get("word", "") for wd in name_words)

        if win_size == 1 and is_stopword(phrase.lower(), snapshot):
            continue
        if win_size > 1 and is_stopword(
            name_words[-1].get("word", "").lower(), snapshot
        ):
            continue

        match = correct_single(phrase, win_size, index, snapshot)
        if match and match.entity_kind == "person" and match.confidence >= threshold:
            return (match, win_size)

    # Surname-only fallback on single token after title
    if title_index + 1 < len(word_dicts):
        last_word = word_dicts[title_index + 1].get("word", "")
        surname = last_word.lower()
        candidates = index.surname_map.get(surname)
        if candidates:
            canonical = candidates[0]
            kind = index.entity_kind_map.get(canonical)
            if kind == "person":
                entity_type = index.entity_type_map.get(canonical, "person")
                result = MatchResult(
                    canonical=canonical,
                    confidence=0.90,
                    strategy="title_person",
                    entity_kind="person",
                    entity_type=entity_type,
                )
                return (result, 1)

        phonetic_match = match_phonetic(surname, index)
        if (
            phonetic_match
            and phonetic_match.entity_kind == "person"
            and phonetic_match.confidence >= threshold
        ):
            return (phonetic_match, 1)

        fuzzy_match = match_fuzzy(surname, index, snapshot)
        if (
            fuzzy_match
            and fuzzy_match.entity_kind == "person"
            and fuzzy_match.confidence >= threshold
        ):
            return (fuzzy_match, 1)

    return None


def correct_words(
    words: list[dict],
    index: MatchIndex,
    snapshot: DatasetSnapshot,
    *,
    word_accept_threshold: float = 0.90,
    min_confidence: float = 0.75,
) -> WordCorrectionResult:
    """Correct all entity references in a transcript word list.

    Ports the word-level n-gram joining loop from the JavaScript
    ``formatTranscriptionResponse`` function. Scans windows of 3→2→1
    tokens at each position.

    Four guards:
      a. Word-stopword skip: reject windows containing a word-stopword
      b. Word accept threshold: only accept matches >= threshold (0.90)
      c. Whole-phrase equality: identity match records entity but no correction
      d. Single-token expansion: for n=1, only accept parties or corrections
         whose canonical has at most 2 whitespace tokens

    Parameters
    ----------
    words : list[dict]
        Word dicts (from Pydantic model_dump with by_alias=True), each
        having at minimum ``word``, ``start``, ``end``, ``confidence``,
        plus any extra provider fields.
    index : MatchIndex
        The derived lookup structures from the Dataset_Cache.
    snapshot : DatasetSnapshot
        The immutable dataset snapshot (block lists, stopwords, etc.).
    word_accept_threshold : float
        Minimum confidence to accept a match at word level (default 0.90).
    min_confidence : float
        Minimum confidence for title-person matching (default 0.75).

    Returns
    -------
    WordCorrectionResult
        The corrected words list (may be shorter due to joining),
        the corrections list, and entities_found.
    """
    if not words:
        return WordCorrectionResult(words=[], corrections=[], entities_found=[])

    output: list[dict] = []
    corrections: list[tuple[str, str, str, float, str, str]] = []
    entities_found: list[tuple[str, str, str]] = []

    i = 0
    while i < len(words):
        w = words[i]
        current_word = w.get("word", "") or ""

        # --- Title-person branch ---
        if is_title(current_word, snapshot):
            title_result = _match_title_person_words(
                words, i, index, snapshot, min_confidence
            )
            if title_result:
                match, name_count = title_result

                # Push the title word unchanged
                output.append(dict(w))

                # Determine the corrected name
                corrected_name = match.canonical
                # Double-title guard
                title_lower = current_word.lower()
                canonical_first = (
                    corrected_name.split()[0].lower()
                    if corrected_name.split()
                    else ""
                )
                if title_lower == canonical_first:
                    parts = corrected_name.split(None, 1)
                    corrected_name = parts[1] if len(parts) > 1 else corrected_name

                # Build the merged name word
                name_words = words[i + 1: i + 1 + name_count]
                original_phrase = " ".join(
                    nw.get("word", "") for nw in name_words
                )

                entity_kind = match.entity_kind or "person"
                entity_type = match.entity_type or "person"
                entities_found.append(
                    (match.canonical, entity_kind, entity_type)
                )

                is_identity = corrected_name.lower() == original_phrase.lower()

                if is_identity:
                    # Push name words unchanged
                    for nw in name_words:
                        output.append(dict(nw))
                else:
                    # Merge into one word
                    first_name = name_words[0]
                    last_name = name_words[-1]
                    merged = dict(first_name)
                    merged["word"] = corrected_name
                    merged["end"] = last_name.get("end", first_name.get("end"))
                    merged["locationCorrected"] = True
                    merged["entityKind"] = entity_kind
                    merged["entityType"] = entity_type
                    output.append(merged)

                    corrections.append((
                        original_phrase,
                        corrected_name,
                        match.strategy,
                        match.confidence,
                        entity_kind,
                        entity_type,
                    ))

                i += 1 + name_count
                continue

            # No person match — push title as-is
            output.append(dict(w))
            i += 1
            continue

        # --- Word-stopword skip ---
        if is_word_stopword(current_word, snapshot):
            output.append(dict(w))
            i += 1
            continue

        # --- Minimum token length guard ---
        if len(current_word) < _WORD_MIN_TOKEN_LENGTH:
            output.append(dict(w))
            i += 1
            continue

        # --- Windows 3→2→1 ---
        matched = False

        for n in (3, 2, 1):
            if i + n > len(words):
                continue

            window = words[i: i + n]
            window_words = [wd.get("word", "") or "" for wd in window]

            # Guard (a): word-stopword skip for any token in window
            if n > 1 and any(
                is_word_stopword(ww, snapshot) for ww in window_words
            ):
                continue

            phrase = " ".join(window_words)

            # Try correction via correct_single
            match = correct_single(phrase, n, index, snapshot)

            # For n > 1: also try joined (fused) match
            if match is None and n > 1:
                joined_text = "".join(ww.lower() for ww in window_words)
                joined_stripped = re.sub(r"[\s\-']", "", joined_text)
                canonical = index.fused_map.get(joined_stripped)
                if canonical:
                    ek = index.entity_kind_map.get(canonical, "location")
                    et = index.entity_type_map.get(canonical, "supplementary")
                    match = MatchResult(
                        canonical=canonical,
                        confidence=JOINED_CONFIDENCE,
                        strategy="joined",
                        entity_kind=ek,
                        entity_type=et,
                    )

            if match is None:
                continue

            # Apply party display heuristic
            match = apply_party_display(match, phrase, index)

            # Guard (b): word accept threshold
            if match.confidence < word_accept_threshold:
                continue

            # Guard (c): whole-phrase equality (identity match)
            # For n > 1: if the match canonical equals the joined phrase,
            # it's an identity match — record entity but don't correct.
            # For n = 1: skip identity check (JS behaviour — case normalization
            # like "ndc" → "NDC" IS treated as a correction)
            if n > 1 and match.canonical.lower() == phrase.lower():
                entities_found.append(
                    (match.canonical, match.entity_kind, match.entity_type)
                )
                # For identity matches, push words unchanged
                for wd in window:
                    output.append(dict(wd))
                i += n
                matched = True
                break

            # For n=1, check true identity (exact case match means no correction)
            if n == 1 and match.canonical == current_word:
                entities_found.append(
                    (match.canonical, match.entity_kind, match.entity_type)
                )
                output.append(dict(w))
                i += 1
                matched = True
                break

            # Guard (d): single-token expansion guard
            if n == 1:
                is_party = match.entity_kind == "party"
                canonical_token_count = len(match.canonical.split())
                if not is_party and canonical_token_count > 2:
                    # Reject: single word expanding to > 2 tokens
                    continue

            # --- Accept the match ---
            entity_kind = match.entity_kind or "location"
            entity_type = match.entity_type or "supplementary"

            if n > 1:
                # Merge multiple words into one
                first_w = window[0]
                last_w = window[-1]
                merged = dict(first_w)
                merged["word"] = match.canonical
                merged["end"] = last_w.get("end", first_w.get("end"))
                merged["locationCorrected"] = True
                merged["entityKind"] = entity_kind
                merged["entityType"] = entity_type
                output.append(merged)
            else:
                # Update single word in place
                corrected_w = dict(w)
                corrected_w["word"] = match.canonical
                corrected_w["locationCorrected"] = True
                corrected_w["entityKind"] = entity_kind
                corrected_w["entityType"] = entity_type
                output.append(corrected_w)

            original_phrase = phrase
            corrections.append((
                original_phrase,
                match.canonical,
                match.strategy,
                match.confidence,
                entity_kind,
                entity_type,
            ))
            entities_found.append(
                (match.canonical, entity_kind, entity_type)
            )

            i += n
            matched = True
            break

        if not matched:
            # No match in any window — pass through unchanged
            output.append(dict(w))
            i += 1

    return WordCorrectionResult(
        words=output,
        corrections=corrections,
        entities_found=entities_found,
    )
