"""
Year/Date pattern matchers for the Year_Corrector.

Ported from lib/location-correction/year-correction.js.

Seven matchers in precedence order:
1. Ordinal-of-month  — "seventh of July nineteen ninety two" → "7th July 1992"
2. Month-day         — "June nineteen ninety two" → "June 1992"
3. Numeric day-month-year — "3 March 2024" → "3rd March, 2024"
4. Two-thousand      — "two thousand and sixteen" → "2016"
5. Century-suffix    — "nineteen ninety two" → "1992"
6. Split-numeric-year — "19" + "92" → "1992"
7. Decade            — "the nineteen seventies" → "the 1970s"

Guards:
- Month-year guard: month preceding a year emits "Month Year" format
- Twenty-single-digit guard: "twenty six" alone is NOT a year
- Year-range guards: result must be in 1300–2099
- Numeric-day guard: "3 March" alone (no year) is left unchanged
- Identity guard: if conversion equals the input text, skip it
- Decade-last ordering: decades tried last (broadest pattern)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .parsers import (
    capitalize_month as _capitalize_month,
    clean as _clean,
    is_month as _is_month,
    ordinal_suffix as _ordinal_suffix,
    parse_century_prefix as _parse_century_prefix,
    parse_day_words as _parse_day_words,
    parse_two_digit_suffix as _parse_two_digit_suffix,
    parse_year_at_position as _parse_year_at_position,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Result of a successful pattern match."""

    formatted: str
    words_consumed: int
    entity_type: str = "year"


# ---------------------------------------------------------------------------
# Pattern matchers — each returns MatchResult or None
# ---------------------------------------------------------------------------


def match_two_thousand(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: "two thousand [and] X" → 2000 + X

    E.g. "two thousand and sixteen" → 2016
         "two thousand twenty four" → 2024
         "two thousand" → 2000
    """
    if idx >= len(words):
        return None
    w1 = _clean(words[idx])
    if w1 != "two":
        return None
    if idx + 1 >= len(words):
        return None

    w2 = _clean(words[idx + 1])
    if w2 != "thousand":
        return None

    next_idx = idx + 2
    consumed = 2

    # Skip optional "and"
    if next_idx < len(words) and _clean(words[next_idx]) == "and":
        next_idx += 1
        consumed += 1

    # Try to parse the suffix
    suffix = _parse_two_digit_suffix(words, next_idx)
    if suffix is not None:
        value, suffix_consumed, _padded = suffix
        year = 2000 + value
        if 2000 <= year <= 2099:
            return MatchResult(
                formatted=str(year),
                words_consumed=consumed + suffix_consumed,
                entity_type="year",
            )

    # Just "two thousand" = 2000
    return MatchResult(formatted="2000", words_consumed=consumed, entity_type="year")


def match_century_suffix(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: "nineteen/eighteen/twenty XX" → 19XX/18XX/20XX

    E.g. "nineteen sixty" → 1960
         "nineteen ninety two" → 1992
         "twenty twenty four" → 2024

    Guard: "twenty" + single digit (1-9) is ambiguous — "twenty six" usually
    means the number 26, not the year 2006. Only accept "twenty" as a century
    prefix when the suffix is >= 10.
    """
    if idx >= len(words):
        return None
    prefix = _parse_century_prefix(_clean(words[idx]))
    if prefix is None:
        return None

    # Need a suffix (otherwise "twenty" alone is just a number, not a year)
    suffix = _parse_two_digit_suffix(words, idx + 1)
    if suffix is None:
        return None

    value, suffix_consumed, _padded = suffix

    # Twenty-single-digit guard: when the prefix is "twenty" (= 20),
    # reject single-digit suffixes to avoid "twenty six" → 2006.
    if prefix == 20 and value < 10:
        return None

    year = prefix * 100 + value

    # Year-range guard: validate as a plausible year (1300–2099)
    if year < 1300 or year > 2099:
        return None

    return MatchResult(
        formatted=str(year),
        words_consumed=1 + suffix_consumed,
        entity_type="year",
    )


def match_decade(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: Decade references — "nineteen seventies" → "1970s"

    Also: "eighties" → "80s" (context-dependent, only after a century prefix)
    """
    if idx >= len(words):
        return None
    prefix = _parse_century_prefix(_clean(words[idx]))
    if prefix is None:
        return None
    if idx + 1 >= len(words):
        return None

    next_word = _clean(words[idx + 1])
    decade_map = {
        "twenties": 20,
        "thirties": 30,
        "forties": 40,
        "fifties": 50,
        "sixties": 60,
        "seventies": 70,
        "eighties": 80,
        "nineties": 90,
    }

    if next_word in decade_map:
        year = prefix * 100 + decade_map[next_word]
        return MatchResult(
            formatted=f"{year}s",
            words_consumed=2,
            entity_type="decade",
        )

    return None


def match_split_numeric_year(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: Bare numeric-sounding fragments that Deepgram sometimes outputs.

    E.g. "19 92" (two separate tokens) → "1992"
    """
    if idx >= len(words):
        return None
    w1 = _clean(words[idx])
    if idx + 1 >= len(words):
        return None
    w2 = _clean(words[idx + 1])

    # Both must be pure digits
    if not re.match(r"^\d+$", w1) or not re.match(r"^\d+$", w2):
        return None

    combined = w1 + w2
    if len(combined) != 4:
        return None

    num = int(combined)
    # Must be a 4-digit year in plausible range
    if 1900 <= num <= 2099:
        return MatchResult(
            formatted=str(num),
            words_consumed=2,
            entity_type="year",
        )

    return None


def match_ordinal_of_month(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: "Ordinal [of] Month [Year]"

    E.g. "fifth of March twenty twenty four" → "5th March 2024"
         "the twenty first of January two thousand and sixteen" → "21st January 2016"
         "seventh of July" → "7th July"
    """
    if idx >= len(words):
        return None

    # Parse day (ordinal or compound ordinal)
    day_result = _parse_day_words(words, idx)
    if day_result is None:
        return None
    day, day_consumed, is_ordinal = day_result
    if not is_ordinal:
        return None
    if day < 1 or day > 31:
        return None

    pos = idx + day_consumed

    # Optional "of"
    if pos < len(words) and _clean(words[pos]) == "of":
        pos += 1

    # Month
    if pos >= len(words) or not _is_month(words[pos]):
        return None
    month = _capitalize_month(words[pos])
    pos += 1

    # Optional year
    year_str = ""
    year_consumed = 0
    if pos < len(words):
        year_result = _parse_year_at_position(words, pos)
        if year_result is not None:
            year_val, year_consumed = year_result
            year_str = f" {year_val}"

    formatted = f"{day}{_ordinal_suffix(day)} {month}{year_str}"
    total_consumed = (pos - idx) + year_consumed

    entity_type = "full-date" if year_str else "partial-date"
    return MatchResult(
        formatted=formatted,
        words_consumed=total_consumed,
        entity_type=entity_type,
    )


def match_month_day(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: "Month Ordinal/Cardinal [Year]" or "Month Year" (month-year guard).

    E.g. "March fifth twenty twenty four" → "March 5th, 2024"
         "January seven twenty twenty five" → "January 7th, 2025"
         "December twenty first" → "December 21st"
         "june nineteen ninety two" → "June 1992" (month-year guard)
    """
    if idx >= len(words):
        return None

    # Must start with a month
    if not _is_month(words[idx]):
        return None
    month = _capitalize_month(words[idx])

    pos = idx + 1

    # Month-year guard: Before parsing as "Month Day", check if the next token
    # starts a year (e.g. "june nineteen ninety two" → "June 1992").
    if pos < len(words):
        year_direct = _parse_year_at_position(words, pos)
        if year_direct is not None:
            year_val, year_consumed = year_direct
            formatted = f"{month} {year_val}"
            total_consumed = 1 + year_consumed
            return MatchResult(
                formatted=formatted,
                words_consumed=total_consumed,
                entity_type="month-year",
            )

    # Parse day (ordinal or cardinal)
    if pos >= len(words):
        return None
    day_result = _parse_day_words(words, pos)
    if day_result is None:
        return None
    day, day_consumed, _is_ordinal = day_result
    if day < 1 or day > 31:
        return None

    pos += day_consumed

    # Optional year
    year_str = ""
    year_consumed = 0
    if pos < len(words):
        year_result = _parse_year_at_position(words, pos)
        if year_result is not None:
            year_val, year_consumed = year_result
            year_str = f", {year_val}"

    formatted = f"{month} {day}{_ordinal_suffix(day)}{year_str}"
    total_consumed = (pos - idx) + year_consumed

    entity_type = "full-date" if year_str else "partial-date"
    return MatchResult(
        formatted=formatted,
        words_consumed=total_consumed,
        entity_type=entity_type,
    )


def match_numeric_day_month_year(words: list[str], idx: int) -> Optional[MatchResult]:
    """
    Pattern: "Day Month Year" with numeric day.

    E.g. "7 July 1992" (already partially numeric from ASR)

    Numeric-day guard: Only triggers if followed by a year to avoid false
    positives. "3 March" alone is left unchanged.
    """
    if idx >= len(words):
        return None
    w1 = _clean(words[idx])

    # Must be a 1-2 digit number
    if not re.match(r"^\d{1,2}$", w1):
        return None
    day = int(w1)
    if day < 1 or day > 31:
        return None

    # Next must be a month
    if idx + 1 >= len(words) or not _is_month(words[idx + 1]):
        return None
    month = _capitalize_month(words[idx + 1])

    # Numeric-day guard: Must have a year after to confirm this is a date
    # (avoid "3 March" alone being too aggressive)
    if idx + 2 >= len(words):
        return None
    year_result = _parse_year_at_position(words, idx + 2)
    if year_result is None:
        return None

    year_val, year_consumed = year_result
    formatted = f"{day}{_ordinal_suffix(day)} {month}, {year_val}"
    total_consumed = 2 + year_consumed

    return MatchResult(
        formatted=formatted,
        words_consumed=total_consumed,
        entity_type="full-date",
    )


# ---------------------------------------------------------------------------
# Precedence-ordered matcher list
# ---------------------------------------------------------------------------

# The matchers in their precedence order.  The caller (corrector.py) iterates
# this list at each position and takes the first match.
# Decade is last because it is the broadest pattern.
MATCHERS: list[tuple[str, type(match_two_thousand)]] = [
    ("ordinal_of_month", match_ordinal_of_month),
    ("month_day", match_month_day),
    ("numeric_day_month_year", match_numeric_day_month_year),
    ("two_thousand", match_two_thousand),
    ("century_suffix", match_century_suffix),
    ("split_numeric_year", match_split_numeric_year),
    ("decade", match_decade),
]
