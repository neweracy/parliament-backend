"""
Parsing helpers for the Year/Date pattern matchers.

Extracted from patterns.py to mirror the JS ``years/parsers.js`` structure.

Exports: clean, parse_century_prefix, parse_two_digit_suffix,
         ordinal_suffix, capitalize_month, is_month,
         parse_ordinal_day, parse_cardinal_day, parse_day_words,
         parse_year_at_position
"""

from __future__ import annotations

import re
from typing import Optional

from .numbers import MONTHS, MONTH_NAMES, ONES, ORDINALS, TENS


def clean(word: str) -> str:
    """Lowercase and strip punctuation for matching."""
    return re.sub(r"[.,;:!?]", "", (word or "").lower()).strip()


def parse_century_prefix(word: str) -> Optional[int]:
    """
    Parse a century prefix — the value used as the high portion of a year.

    E.g. "nineteen" → 19, "twenty" → 20, "eighteen" → 18.
    Returns None if the word is not a valid century prefix.
    """
    w = clean(word)

    _CENTURY_TEENS: dict[str, int] = {
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
    }

    if w in _CENTURY_TEENS:
        return _CENTURY_TEENS[w]
    if w in TENS:
        return TENS[w]
    return None


def parse_two_digit_suffix(
    words: list[str], start_idx: int
) -> Optional[tuple[int, int, bool]]:
    """
    Parse a two-digit suffix from one or two words.

    Returns (value, words_consumed, padded) or None.
    """
    if start_idx >= len(words):
        return None

    w1 = clean(words[start_idx])

    if w1 in ("oh", "o"):
        if start_idx + 1 < len(words):
            w2 = clean(words[start_idx + 1])
            if w2 in ONES and ONES[w2] <= 9:
                return (ONES[w2], 2, True)
        return None

    if w1 in ONES and ONES[w1] <= 19:
        return (ONES[w1], 1, ONES[w1] < 10)

    if w1 in TENS:
        if "-" in w1:
            parts = w1.split("-")
            if len(parts) == 2 and parts[0] in TENS and parts[1] in ONES:
                return (TENS[parts[0]] + ONES[parts[1]], 1, False)
        if start_idx + 1 < len(words):
            w2 = clean(words[start_idx + 1])
            if w2 in ONES and ONES[w2] <= 9:
                return (TENS[w1] + ONES[w2], 2, False)
        return (TENS[w1], 1, False)

    return None


def ordinal_suffix(n: int) -> str:
    """Returns the ordinal suffix for a day number."""
    if 11 <= n <= 13:
        return "th"
    last = n % 10
    if last == 1:
        return "st"
    if last == 2:
        return "nd"
    if last == 3:
        return "rd"
    return "th"


def capitalize_month(word: str) -> str:
    """Capitalize a month name: 'march' → 'March'."""
    w = clean(word)
    if w in MONTHS:
        return MONTH_NAMES[MONTHS[w]]
    return word


def is_month(word: str) -> bool:
    """Check if a word is a month name."""
    return clean(word) in MONTHS


def parse_ordinal_day(word: str) -> Optional[int]:
    """
    Parse an ordinal day from a word.
    Returns the day number (1–31) or None.
    """
    w = clean(word)
    if w in ORDINALS:
        return ORDINALS[w][0]
    m = re.match(r"^(\d{1,2})(st|nd|rd|th)$", w)
    if m:
        num = int(m.group(1))
        if 1 <= num <= 31:
            return num
    return None


def parse_cardinal_day(word: str) -> Optional[int]:
    """
    Parse a cardinal day (1–31) from a word.
    """
    w = clean(word)
    if re.match(r"^\d{1,2}$", w):
        num = int(w)
        if 1 <= num <= 31:
            return num
    if w in ONES and 1 <= ONES[w] <= 19:
        return ONES[w]
    if w in TENS and TENS[w] in (20, 30):
        return TENS[w]
    return None


def parse_day_words(
    words: list[str], start_idx: int
) -> Optional[tuple[int, int, bool]]:
    """
    Parse a compound day from one or two words.

    Returns (day, words_consumed, is_ordinal) or None.
    """
    if start_idx >= len(words):
        return None

    w1 = clean(words[start_idx])

    ord_day = parse_ordinal_day(words[start_idx])
    if ord_day is not None:
        return (ord_day, 1, True)

    if w1 in TENS and TENS[w1] in (20, 30):
        if start_idx + 1 < len(words):
            w2 = clean(words[start_idx + 1])
            compound = w1 + "-" + w2
            if compound in ORDINALS:
                return (ORDINALS[compound][0], 2, True)
            if w2 in ORDINALS and ORDINALS[w2][0] <= 9:
                return (TENS[w1] + ORDINALS[w2][0], 2, True)
            if w2 in ONES and 1 <= ONES[w2] <= 9:
                return (TENS[w1] + ONES[w2], 2, False)
        return (TENS[w1], 1, False)

    card = parse_cardinal_day(words[start_idx])
    if card is not None:
        return (card, 1, False)

    return None


def parse_year_at_position(words: list[str], idx: int) -> Optional[tuple[int, int]]:
    """
    Try to parse a year starting at a position using year patterns.
    Returns (year_int, words_consumed) or None.
    """
    # Lazy import to avoid circular dependency
    from .patterns import match_century_suffix, match_two_thousand

    result = match_two_thousand(words, idx)
    if result is not None:
        return (int(result.formatted), result.words_consumed)

    result = match_century_suffix(words, idx)
    if result is not None:
        return (int(result.formatted), result.words_consumed)

    if idx < len(words):
        w = clean(words[idx])
        if re.match(r"^\d{4}$", w):
            num = int(w)
            if 1900 <= num <= 2099:
                return (num, 1)
    return None
