"""Unit tests for app/correction/phonetics.py and app/correction/blocklist.py.

Tests the phonetic_key function (ASR substitution table) and the four
blocklist/stopword/title accessor functions.

Requirements: 3.4, 4.1, 4.10
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.correction.blocklist import is_blocked, is_stopword, is_title, is_word_stopword
from app.correction.phonetics import phonetic_key
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def snapshot() -> DatasetSnapshot:
    """A minimal DatasetSnapshot with known block/stop/title sets."""
    return DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=tuple(),
        record_count=0,
        loaded_at=datetime.now(timezone.utc),
        index=MatchIndex(),
        block_list=frozenset(["general", "page", "national", "nation"]),
        stopwords=frozenset(["the", "a", "an", "is", "of"]),
        word_stopwords=frozenset(["of", "for", "and", "the"]),
        title_prefixes=frozenset(["honorable", "honourable", "minister", "madam"]),
    )


# ---------------------------------------------------------------------------
# phonetic_key tests
# ---------------------------------------------------------------------------


class TestPhoneticKey:
    """Tests for the phonetic_key function — ASR substitution table."""

    def test_strips_spaces_hyphens_apostrophes(self):
        assert phonetic_key("Ningo-Prampram") == "ningoprampram"
        assert phonetic_key("Cape Coast") == "capecoast"
        assert phonetic_key("N'Kwanta") == "nkwanta"

    def test_ph_to_f(self):
        # "ph" → "f"
        assert "f" in phonetic_key("Phobia")
        assert "ph" not in phonetic_key("Phobia")

    def test_gh_not_at_end_to_g(self):
        # "gh" not at end → "g"
        assert phonetic_key("Ghana") == "gana"
        # "gh" at end stays
        assert phonetic_key("Yagh").endswith("gh") or phonetic_key("Yagh").endswith("g")

    def test_ck_to_k(self):
        assert "ck" not in phonetic_key("Beckham")

    def test_vowel_merging(self):
        # ei/ey → e, ou/oo → u, aa → a, ee → e, ii → i
        assert "u" in phonetic_key("Kou")
        assert "aa" not in phonetic_key("Baal")
        assert "ee" not in phonetic_key("Beep")

    def test_double_consonants_collapsed(self):
        assert "ll" not in phonetic_key("Ballast")
        assert "ss" not in phonetic_key("Bosso")

    def test_empty_string(self):
        assert phonetic_key("") == ""

    def test_single_char(self):
        assert phonetic_key("A") == "a"

    def test_koumasi_to_kumasi(self):
        # "Koumasi" → ou→u, collapses to "kumasi"
        assert phonetic_key("Koumasi") == "kumasi"
        assert phonetic_key("Kumasi") == "kumasi"

    def test_phonetic_equivalence(self):
        # Phonetically similar names should produce the same key
        assert phonetic_key("Kumasi") == phonetic_key("Koumasi")


# ---------------------------------------------------------------------------
# blocklist accessor tests
# ---------------------------------------------------------------------------


class TestIsBlocked:
    """Tests for is_blocked — Block_List membership."""

    def test_blocked_token_lowercase(self, snapshot):
        assert is_blocked("general", snapshot) is True

    def test_blocked_token_mixed_case(self, snapshot):
        assert is_blocked("General", snapshot) is True
        assert is_blocked("GENERAL", snapshot) is True

    def test_unblocked_token(self, snapshot):
        assert is_blocked("Kumasi", snapshot) is False

    def test_empty_string_not_blocked(self, snapshot):
        assert is_blocked("", snapshot) is False


class TestIsStopword:
    """Tests for is_stopword — stopword set membership."""

    def test_stopword_lowercase(self, snapshot):
        assert is_stopword("the", snapshot) is True

    def test_stopword_uppercase(self, snapshot):
        assert is_stopword("THE", snapshot) is True

    def test_non_stopword(self, snapshot):
        assert is_stopword("Ghana", snapshot) is False


class TestIsWordStopword:
    """Tests for is_word_stopword — word-stopword set membership."""

    def test_word_stopword_match(self, snapshot):
        assert is_word_stopword("of", snapshot) is True
        assert is_word_stopword("OF", snapshot) is True

    def test_non_word_stopword(self, snapshot):
        assert is_word_stopword("person", snapshot) is False


class TestIsTitle:
    """Tests for is_title — title-prefix set membership."""

    def test_title_lowercase(self, snapshot):
        assert is_title("honorable", snapshot) is True

    def test_title_mixed_case(self, snapshot):
        assert is_title("Honorable", snapshot) is True
        assert is_title("HONOURABLE", snapshot) is True

    def test_minister_is_title(self, snapshot):
        assert is_title("Minister", snapshot) is True

    def test_non_title(self, snapshot):
        assert is_title("president", snapshot) is False

    def test_madam_is_title(self, snapshot):
        assert is_title("Madam", snapshot) is True
