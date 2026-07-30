"""Unit tests for app/years/corrector.py — Year_Corrector.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.8, 4.8, 15.1
"""

from __future__ import annotations

from app.years.corrector import correct_years, correct_years_in_text


# ---------------------------------------------------------------------------
# 1. Basic year conversions via correct_years_in_text
# ---------------------------------------------------------------------------


class TestBasicYearConversions:
    """Spoken years → four-digit numeric form."""

    def test_nineteen_ninety_two(self):
        result, count = correct_years_in_text("nineteen ninety two")
        assert result == "1992"
        assert count == 1

    def test_two_thousand_and_sixteen(self):
        result, count = correct_years_in_text("two thousand and sixteen")
        assert result == "2016"
        assert count == 1

    def test_two_thousand_twenty_four(self):
        result, count = correct_years_in_text("two thousand twenty four")
        assert result == "2024"
        assert count == 1

    def test_two_thousand_alone(self):
        result, count = correct_years_in_text("two thousand")
        assert result == "2000"
        assert count == 1


# ---------------------------------------------------------------------------
# 2. Date conversions
# ---------------------------------------------------------------------------


class TestDateConversions:
    """Spoken dates → numeric date format."""

    def test_ordinal_of_month_year(self):
        result, count = correct_years_in_text(
            "seventh of July nineteen ninety two"
        )
        assert result == "7th July 1992"
        assert count == 1

    def test_month_year_guard(self):
        """Month preceding a year emits 'Month Year' format."""
        result, count = correct_years_in_text("june nineteen ninety two")
        assert result == "June 1992"
        assert count == 1


# ---------------------------------------------------------------------------
# 3. Decade
# ---------------------------------------------------------------------------


class TestDecade:
    """Spoken decades → numeric decade form."""

    def test_the_nineteen_seventies(self):
        result, count = correct_years_in_text("the nineteen seventies")
        assert result == "the 1970s"
        assert count == 1


# ---------------------------------------------------------------------------
# 4. Guards — must NOT convert
# ---------------------------------------------------------------------------


class TestGuards:
    """Patterns that must not trigger conversion."""

    def test_twenty_six_unchanged(self):
        """Twenty-single-digit guard: 'twenty six' is not a year."""
        result, count = correct_years_in_text("twenty six")
        assert result == "twenty six"
        assert count == 0

    def test_numeric_day_guard_no_year(self):
        """'3 March' alone is left unchanged (no year follows)."""
        result, count = correct_years_in_text("3 March")
        assert result == "3 March"
        assert count == 0

    def test_non_matching_number_spans_unchanged(self):
        """Ordinary number words that don't form a year stay unchanged."""
        result, count = correct_years_in_text("I have three dogs")
        assert result == "I have three dogs"
        assert count == 0

    def test_random_words_unchanged(self):
        """Non-numeric text is untouched."""
        result, count = correct_years_in_text("hello world today")
        assert result == "hello world today"
        assert count == 0

    def test_twenty_nine_unchanged(self):
        """Another twenty-single-digit guard case."""
        result, count = correct_years_in_text("twenty nine")
        assert result == "twenty nine"
        assert count == 0


# ---------------------------------------------------------------------------
# 5. Timing via correct_years — Word dicts with start/end
# ---------------------------------------------------------------------------


class TestTimingPreservation:
    """When words are merged, start = first word's start, end = last word's end."""

    def test_merged_words_timing(self):
        """Span timing preserved on merged Words."""
        words = [
            {"word": "nineteen", "start": 1.0, "end": 1.5, "confidence": 0.9},
            {"word": "ninety", "start": 1.5, "end": 2.0, "confidence": 0.9},
            {"word": "two", "start": 2.0, "end": 2.3, "confidence": 0.9},
        ]
        result, count = correct_years(words)
        assert count == 1
        assert len(result) == 1
        assert result[0]["word"] == "1992"
        assert result[0]["start"] == 1.0
        assert result[0]["end"] == 2.3

    def test_year_corrected_flag_set(self):
        """yearCorrected: True is set on merged words."""
        words = [
            {"word": "nineteen", "start": 0.0, "end": 0.5, "confidence": 0.9},
            {"word": "ninety", "start": 0.5, "end": 1.0, "confidence": 0.9},
            {"word": "two", "start": 1.0, "end": 1.3, "confidence": 0.9},
        ]
        result, _count = correct_years(words)
        assert result[0]["yearCorrected"] is True

    def test_extra_fields_preserved(self):
        """Extra fields from the first word are preserved."""
        words = [
            {
                "word": "two",
                "start": 0.0,
                "end": 0.3,
                "confidence": 0.95,
                "speaker": 1,
                "customField": "test",
            },
            {"word": "thousand", "start": 0.3, "end": 0.8, "confidence": 0.9},
            {"word": "and", "start": 0.8, "end": 1.0, "confidence": 0.9},
            {"word": "sixteen", "start": 1.0, "end": 1.5, "confidence": 0.9},
        ]
        result, count = correct_years(words)
        assert count == 1
        assert result[0]["word"] == "2016"
        assert result[0]["speaker"] == 1
        assert result[0]["customField"] == "test"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 1.5

    def test_non_converted_words_preserved(self):
        """Words around a year conversion stay in place with original timing."""
        words = [
            {"word": "in", "start": 0.0, "end": 0.2, "confidence": 0.9},
            {"word": "nineteen", "start": 0.3, "end": 0.7, "confidence": 0.9},
            {"word": "ninety", "start": 0.7, "end": 1.1, "confidence": 0.9},
            {"word": "two", "start": 1.1, "end": 1.4, "confidence": 0.9},
            {"word": "we", "start": 1.5, "end": 1.7, "confidence": 0.9},
        ]
        result, count = correct_years(words)
        assert count == 1
        assert len(result) == 3
        assert result[0]["word"] == "in"
        assert result[0]["start"] == 0.0
        assert result[1]["word"] == "1992"
        assert result[1]["start"] == 0.3
        assert result[1]["end"] == 1.4
        assert result[2]["word"] == "we"
        assert result[2]["start"] == 1.5


# ---------------------------------------------------------------------------
# 6. Conversion count
# ---------------------------------------------------------------------------


class TestConversionCount:
    """Conversion count matches the number of actual corrections applied."""

    def test_multiple_corrections_counted(self):
        """Two year conversions in one text are both counted."""
        result, count = correct_years_in_text(
            "nineteen ninety two and two thousand and sixteen"
        )
        assert "1992" in result
        assert "2016" in result
        assert count == 2

    def test_identity_conversions_not_counted(self):
        """Identity conversions (formatted == input) are NOT counted."""
        # If the text is already numeric, no correction happens
        result, count = correct_years_in_text("1992")
        assert result == "1992"
        assert count == 0

    def test_no_conversions_yields_zero(self):
        """Text with no year patterns yields count 0."""
        _result, count = correct_years_in_text("the quick brown fox")
        assert count == 0

    def test_empty_text(self):
        """Empty input returns empty string and zero count."""
        result, count = correct_years_in_text("")
        assert result == ""
        assert count == 0

    def test_empty_word_list(self):
        """Empty word list returns empty list and zero count."""
        result, count = correct_years([])
        assert result == []
        assert count == 0
