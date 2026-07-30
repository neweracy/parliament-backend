"""Unit tests for app/llm/prompt.py — system prompt construction."""

from __future__ import annotations

from app.llm.prompt import (
    build_system_prompt,
    build_user_content,
    estimate_prompt_tokens,
)
from app.models.entities import EntityKind, EntityRecord, EntityType


class TestBuildSystemPrompt:
    """Tests for build_system_prompt."""

    def test_contains_all_13_rules(self):
        """The system prompt must contain all 13 correction rules."""
        prompt = build_system_prompt([])
        for i in range(1, 14):
            assert f"{i}." in prompt, f"Rule {i} not found in prompt"

    def test_rule_11_numeric_years(self):
        """Rule 11 must mention leaving numeric years unchanged."""
        prompt = build_system_prompt([])
        assert "Do NOT convert spoken numbers into numeric year format" in prompt

    def test_rule_12_person_name_parts(self):
        """Rule 12 must mention never replacing parts of a person's name."""
        prompt = build_system_prompt([])
        assert "Do NOT replace parts of a person's name with a location name" in prompt
        assert "Dankwa" in prompt
        assert "Dramani" in prompt

    def test_rule_10_common_english_words(self):
        """Rule 10 must mention common English words like 'general'."""
        prompt = build_system_prompt([])
        assert '"general" must NOT become "Central"' in prompt
        assert '"nation" must NOT become "Nanton"' in prompt

    def test_empty_records_produces_valid_prompt(self):
        """An empty record list produces a prompt with 'No specific entity data'."""
        prompt = build_system_prompt([])
        assert "<REFERENCE_DATA>" in prompt
        assert "No specific entity data retrieved" in prompt

    def test_location_records_in_reference(self):
        """Location records appear in the LOCATIONS section."""
        records = [
            EntityRecord(
                canonical="Kumasi",
                entity_kind=EntityKind.location,
                entity_type=EntityType.city,
                region="Ashanti",
            ),
        ]
        prompt = build_system_prompt(records)
        assert "<LOCATIONS>" in prompt
        assert "Kumasi" in prompt
        assert "city" in prompt
        assert "Ashanti" in prompt

    def test_person_records_in_reference(self):
        """Person records appear in the OFFICIALS_AND_MPS section."""
        records = [
            EntityRecord(
                canonical="John Mahama",
                entity_kind=EntityKind.person,
                entity_type=EntityType.president,
                role="President",
                aliases=["Mahama"],
            ),
        ]
        prompt = build_system_prompt(records)
        assert "<OFFICIALS_AND_MPS>" in prompt
        assert "John Mahama" in prompt
        assert "[President]" in prompt
        assert "aliases: Mahama" in prompt

    def test_party_records_in_reference(self):
        """Party records appear in the POLITICAL_PARTIES section."""
        records = [
            EntityRecord(
                canonical="National Democratic Congress",
                entity_kind=EntityKind.party,
                entity_type=EntityType.party,
                aliases=["NDC"],
            ),
        ]
        prompt = build_system_prompt(records)
        assert "<POLITICAL_PARTIES>" in prompt
        assert "National Democratic Congress" in prompt
        assert "(NDC)" in prompt

    def test_preamble_present(self):
        """The system prompt includes the Hansard preamble."""
        prompt = build_system_prompt([])
        assert "Ghanaian parliamentary transcripts" in prompt
        assert "post-processing assistant" in prompt


class TestBuildUserContent:
    """Tests for build_user_content."""

    def test_basic_format(self):
        """User content wraps text with [Segment N]: prefix."""
        result = build_user_content("hello world", 1)
        assert result == "[Segment 1]: hello world"

    def test_segment_numbering(self):
        """Segment number is correctly placed."""
        result = build_user_content("some text", 42)
        assert result == "[Segment 42]: some text"


class TestEstimatePromptTokens:
    """Tests for estimate_prompt_tokens."""

    def test_returns_positive_integer(self):
        """Token estimate is a positive integer for non-empty input."""
        count = estimate_prompt_tokens("Hello world", "Test content")
        assert isinstance(count, int)
        assert count > 0

    def test_empty_input(self):
        """Empty inputs produce a small but non-negative count."""
        count = estimate_prompt_tokens("", "")
        assert isinstance(count, int)
        assert count >= 0

    def test_longer_input_more_tokens(self):
        """A longer prompt produces a higher token estimate."""
        short = estimate_prompt_tokens("short", "text")
        long_text = " ".join(["word"] * 100)
        long_count = estimate_prompt_tokens(long_text, long_text)
        assert long_count > short

    def test_approximation_ratio(self):
        """Token estimate is roughly 1.3x the word count."""
        words = " ".join(["word"] * 100)
        count = estimate_prompt_tokens(words, "")
        # 101 words (100 + ""), * 1.3 ≈ 131
        assert 100 < count < 200
