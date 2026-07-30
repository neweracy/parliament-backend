"""Unit tests for app/llm/align.py — LCS alignment functions.

Validates Requirements 11.3 and 11.9:
- Token alignment preserves Word count
- Changes only at anchor pairs and inside equal-sized gaps
- Only the first token position of a multi-token entry may write
"""

from app.llm.align import apply_aligned, apply_aligned_with_map, lcs_pairs


class TestLcsPairs:
    """Tests for the lcs_pairs function."""

    def test_identical_sequences(self):
        original = ["the", "quick", "brown", "fox"]
        refined = ["the", "quick", "brown", "fox"]
        pairs = lcs_pairs(original, refined)
        assert pairs == [(0, 0), (1, 1), (2, 2), (3, 3)]

    def test_empty_sequences(self):
        assert lcs_pairs([], []) == []
        assert lcs_pairs(["hello"], []) == []
        assert lcs_pairs([], ["hello"]) == []

    def test_case_insensitive_matching(self):
        original = ["the", "ndc", "party"]
        refined = ["the", "NDC", "party"]
        pairs = lcs_pairs(original, refined)
        # All match case-insensitively
        assert pairs == [(0, 0), (1, 1), (2, 2)]

    def test_insertion_in_refined(self):
        original = ["the", "quick", "fox"]
        refined = ["the", "very", "quick", "brown", "fox"]
        pairs = lcs_pairs(original, refined)
        # "the" matches "the", "quick" matches "quick", "fox" matches "fox"
        assert pairs == [(0, 0), (1, 2), (2, 4)]

    def test_deletion_in_refined(self):
        original = ["the", "very", "quick", "brown", "fox"]
        refined = ["the", "quick", "fox"]
        pairs = lcs_pairs(original, refined)
        assert pairs == [(0, 0), (2, 1), (4, 2)]

    def test_single_token_difference(self):
        original = ["hello", "world"]
        refined = ["hello", "earth"]
        pairs = lcs_pairs(original, refined)
        # Only "hello" matches
        assert pairs == [(0, 0)]


class TestApplyAligned:
    """Tests for the apply_aligned function (equal token counts)."""

    def test_no_changes_when_identical(self):
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "quick", "start": 0.1, "end": 0.3, "confidence": 0.95},
            {"word": "fox", "start": 0.3, "end": 0.5, "confidence": 0.90},
        ]
        refined = ["the", "quick", "fox"]
        result = apply_aligned(words, refined)
        assert result is words  # Same reference
        assert all("bedrockCorrected" not in w for w in result)

    def test_single_word_correction(self):
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80},
            {"word": "party", "start": 0.3, "end": 0.5, "confidence": 0.95},
        ]
        refined = ["the", "NDC", "party"]
        result = apply_aligned(words, refined)
        assert result[0]["word"] == "the"
        assert "bedrockCorrected" not in result[0]
        assert result[1]["word"] == "NDC"
        assert result[1]["bedrockCorrected"] is True
        assert result[2]["word"] == "party"
        assert "bedrockCorrected" not in result[2]

    def test_preserves_word_count(self):
        words = [
            {"word": "helo", "start": 0.0, "end": 0.2, "confidence": 0.70},
            {"word": "wrld", "start": 0.2, "end": 0.4, "confidence": 0.65},
        ]
        refined = ["hello", "world"]
        result = apply_aligned(words, refined)
        assert len(result) == 2
        assert result[0]["word"] == "hello"
        assert result[1]["word"] == "world"

    def test_preserves_other_fields(self):
        words = [
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80,
             "speaker": 1, "punctuated_word": "ndc"},
        ]
        refined = ["NDC"]
        result = apply_aligned(words, refined)
        assert result[0]["word"] == "NDC"
        assert result[0]["start"] == 0.1
        assert result[0]["end"] == 0.3
        assert result[0]["confidence"] == 0.80
        assert result[0]["speaker"] == 1
        assert result[0]["bedrockCorrected"] is True

    def test_multi_token_entry(self):
        """A merged word like 'Samuel Okudzeto Ablakwa' expands to 3 tokens."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "Samuel Okudzeto Ablakwa", "start": 0.1, "end": 0.8,
             "confidence": 0.85, "locationCorrected": True},
            {"word": "said", "start": 0.8, "end": 1.0, "confidence": 0.99},
        ]
        # 5 tokens total: "the", "Samuel", "Okudzeto", "Ablakwa", "said"
        refined = ["the", "Samuel", "Okudzeto", "Ablakwa", "said"]
        result = apply_aligned(words, refined)
        # No change needed — all same
        assert len(result) == 3
        assert result[1]["word"] == "Samuel Okudzeto Ablakwa"
        assert "bedrockCorrected" not in result[1]

    def test_multi_token_entry_correction(self):
        """Correcting a multi-token entry writes the joined corrected text."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "Samul Okudeto Ablawa", "start": 0.1, "end": 0.8,
             "confidence": 0.70},
            {"word": "said", "start": 0.8, "end": 1.0, "confidence": 0.99},
        ]
        # "the", "Samul", "Okudeto", "Ablawa", "said" → corrected
        refined = ["the", "Samuel", "Okudzeto", "Ablakwa", "said"]
        result = apply_aligned(words, refined)
        assert len(result) == 3
        assert result[1]["word"] == "Samuel Okudzeto Ablakwa"
        assert result[1]["bedrockCorrected"] is True
        assert result[2]["word"] == "said"
        assert "bedrockCorrected" not in result[2]

    def test_mismatched_token_count_bails_safely(self):
        """If refined has different token count, return words unchanged."""
        words = [
            {"word": "hello", "start": 0.0, "end": 0.2, "confidence": 0.99},
            {"word": "world", "start": 0.2, "end": 0.4, "confidence": 0.95},
        ]
        # 3 tokens vs 2 words — should bail
        refined = ["hello", "beautiful", "world"]
        result = apply_aligned(words, refined)
        assert len(result) == 2
        assert result[0]["word"] == "hello"
        assert result[1]["word"] == "world"


class TestApplyAlignedWithMap:
    """Tests for apply_aligned_with_map (LCS-based, unequal token counts)."""

    def test_preserves_word_count_on_insertion(self):
        """LLM added a token — word count must stay the same."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80},
            {"word": "party", "start": 0.3, "end": 0.5, "confidence": 0.95},
        ]
        token_map = [0, 1, 2]  # 1:1 mapping
        refined = ["the", "NDC", "political", "party"]  # inserted "political"
        result = apply_aligned_with_map(words, refined, token_map)
        assert len(result) == 3  # Word count preserved

    def test_preserves_word_count_on_deletion(self):
        """LLM removed a token — word count must stay the same."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "very", "start": 0.1, "end": 0.2, "confidence": 0.95},
            {"word": "ndc", "start": 0.2, "end": 0.4, "confidence": 0.80},
            {"word": "party", "start": 0.4, "end": 0.6, "confidence": 0.95},
        ]
        token_map = [0, 1, 2, 3]
        refined = ["the", "NDC", "party"]  # "very" was removed
        result = apply_aligned_with_map(words, refined, token_map)
        assert len(result) == 4  # Word count preserved

    def test_anchor_correction(self):
        """Corrections at anchor points (LCS-matched positions) are applied."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80},
            {"word": "party", "start": 0.3, "end": 0.5, "confidence": 0.95},
        ]
        token_map = [0, 1, 2]
        # Same count but using the LCS path explicitly:
        # "the" and "party" are anchors (case-insensitive match)
        # "ndc" matches "NDC" as an anchor too (case-insensitive)
        # but the original text differs, so correction applies
        refined = ["the", "NDC", "party"]
        result = apply_aligned_with_map(words, refined, token_map)
        assert result[1]["word"] == "NDC"
        assert result[1]["bedrockCorrected"] is True

    def test_equal_sized_gap_correction(self):
        """Equal-sized gaps between anchors get word-for-word correction."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "hnorable", "start": 0.1, "end": 0.4, "confidence": 0.70},
            {"word": "member", "start": 0.4, "end": 0.6, "confidence": 0.99},
        ]
        token_map = [0, 1, 2]
        # "the" and "member" are anchors; between them is a gap of 1 on each side
        refined = ["the", "Honorable", "member"]
        result = apply_aligned_with_map(words, refined, token_map)
        assert result[1]["word"] == "Honorable"
        assert result[1]["bedrockCorrected"] is True

    def test_unequal_gap_skipped(self):
        """Unequal gaps are skipped — original text preserved."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "foo", "start": 0.1, "end": 0.2, "confidence": 0.90},
            {"word": "bar", "start": 0.2, "end": 0.4, "confidence": 0.90},
            {"word": "member", "start": 0.4, "end": 0.6, "confidence": 0.99},
        ]
        token_map = [0, 1, 2, 3]
        # "the" and "member" are anchors.
        # Gap between: original has 2 tokens ("foo", "bar"),
        # refined has 3 ("alpha", "beta", "gamma") — unequal gap sizes
        refined = ["the", "alpha", "beta", "gamma", "member"]
        result = apply_aligned_with_map(words, refined, token_map)
        # Unequal gap → skipped, originals preserved
        assert result[1]["word"] == "foo"
        assert "bedrockCorrected" not in result[1]
        assert result[2]["word"] == "bar"
        assert "bedrockCorrected" not in result[2]

    def test_multi_token_entry_first_position_only(self):
        """Only the first token position of a multi-token entry may write."""
        words = [
            {"word": "the", "start": 0.0, "end": 0.1, "confidence": 0.99},
            {"word": "Ken Ofori-Atta", "start": 0.1, "end": 0.6,
             "confidence": 0.85, "locationCorrected": True},
            {"word": "said", "start": 0.6, "end": 0.8, "confidence": 0.99},
        ]
        # token_map: "the"→0, "Ken"→1, "Ofori-Atta"→1, "said"→2
        token_map = [0, 1, 1, 2]
        # LLM corrected "Ken" → "Kennedy" at the anchor for that position
        refined = ["the", "Kennedy", "Ofori-Atta", "said"]
        result = apply_aligned_with_map(words, refined, token_map)
        # The first token of the entry should be writable
        assert result[1]["word"] == "Kennedy"
        assert result[1]["bedrockCorrected"] is True

    def test_empty_words(self):
        """Empty words list is handled gracefully."""
        words: list[dict] = []
        token_map: list[int] = []
        refined = ["hello"]
        result = apply_aligned_with_map(words, refined, token_map)
        assert result == []

    def test_all_fields_preserved(self):
        """Non-word fields on a corrected entry are preserved."""
        words = [
            {"word": "ndc", "start": 0.1, "end": 0.3, "confidence": 0.80,
             "speaker": 2, "punctuated_word": "ndc."},
        ]
        token_map = [0]
        refined = ["NDC"]
        result = apply_aligned_with_map(words, refined, token_map)
        assert result[0]["word"] == "NDC"
        assert result[0]["start"] == 0.1
        assert result[0]["end"] == 0.3
        assert result[0]["confidence"] == 0.80
        assert result[0]["speaker"] == 2
        assert result[0]["punctuated_word"] == "ndc."
        assert result[0]["bedrockCorrected"] is True
