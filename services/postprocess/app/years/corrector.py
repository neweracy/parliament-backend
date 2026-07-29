"""
Year/Date correction for the Postprocessing_Service.

Ported from lib/location-correction/year-correction.js (correctYears, correctYearsInText).

Two public functions:
- correct_years(words)      — operates on Word dicts, preserves timing
- correct_years_in_text(text) — operates on plain text, returns corrected string
"""

from __future__ import annotations

from .patterns import MATCHERS, MatchResult


def correct_years(words: list[dict]) -> tuple[list[dict], int]:
    """
    Correct year/date expressions in a list of Word dicts.

    Iterates through the word list. At each position, tries each matcher in
    MATCHERS precedence order. On match, merges the consumed words into one
    output word preserving timing from first/last consumed word.

    Identity guard: if the formatted result equals the joined input words
    (case-insensitive), don't count it as a correction.

    Returns:
        (corrected_words, conversion_count)
    """
    if not words:
        return ([], 0)

    result: list[dict] = list(words)
    consumed: set[int] = set()
    corrections = 0

    i = 0
    while i < len(result):
        if i in consumed:
            i += 1
            continue

        # Extract word strings for pattern matching
        word_strings = [w.get("word", "") for w in result]

        match: MatchResult | None = None
        for _name, matcher_fn in MATCHERS:
            match = matcher_fn(word_strings, i)
            if match is not None:
                break

        if match is not None:
            # Gather the original words for the identity guard
            original_words = []
            for j in range(i, i + match.words_consumed):
                if j < len(result):
                    original_words.append(result[j].get("word", ""))
            original_joined = " ".join(original_words)

            # Identity guard: if conversion equals input, skip
            if match.formatted.lower() == original_joined.lower():
                i += 1
                continue

            # Merge into the first word's slot
            first_word = result[i]
            last_word = result[i + match.words_consumed - 1]

            merged = {**first_word}
            merged["word"] = match.formatted
            merged["yearCorrected"] = True

            # Extend end time to last consumed word
            if "end" in last_word:
                merged["end"] = last_word["end"]

            result[i] = merged
            corrections += 1

            # Mark subsequent words as consumed
            for j in range(i + 1, i + match.words_consumed):
                consumed.add(j)

        i += 1

    # Remove consumed words from result
    final_words = [w for idx, w in enumerate(result) if idx not in consumed]

    return (final_words, corrections)


def correct_years_in_text(text: str) -> tuple[str, int]:
    """
    Correct year/date expressions in plain text.

    Splits text into tokens, creates pseudo-word dicts (just the ``word`` field),
    runs the same matching logic, and returns the corrected text.

    Returns:
        (corrected_text, conversion_count)
    """
    if not text or not text.strip():
        return (text or "", 0)

    tokens = text.split()
    fake_words = [{"word": t} for t in tokens]
    corrected_words, count = correct_years(fake_words)
    new_text = " ".join(w["word"] for w in corrected_words)
    return (new_text, count)
