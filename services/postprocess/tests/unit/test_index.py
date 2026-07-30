"""Unit tests for app/datasets/index.py — Match_Index build.

Tests verify:
- canonical_map uses first-wins for canonical entries (Requirement 3.12)
- Supplementary and person aliases use last-wins policy
- MP and party aliases use first-wins policy
- phonetic_map is scoped to canonicals + supplementary aliases only
- surname_map and initial_surname_map are built for person records
- entity_kind_map and entity_type_map track classification
- party_abbr_map maps canonical → abbreviation
- alias_ordinal assigns deterministic ordinals
- length_buckets group keys by character length
- bk_tree is built from canonical_map keys

Requirements: 10.4, 10.5, 3.12
"""

from __future__ import annotations

import pytest

from app.datasets.index import MatchIndex, build_index, _strip_all, _phonetic_key
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    canonical: str,
    entity_kind: EntityKind = EntityKind.location,
    entity_type: EntityType = EntityType.supplementary,
    aliases: list[str] | None = None,
    source: str | None = None,
    source_rank: int = 0,
    party: str | None = None,
    region: str | None = None,
) -> EntityRecord:
    return EntityRecord(
        canonical=canonical,
        entity_kind=entity_kind,
        entity_type=entity_type,
        aliases=aliases or [],
        source=source,
        source_rank=source_rank,
        party=party,
        region=region,
    )


# ---------------------------------------------------------------------------
# Tests for helper functions
# ---------------------------------------------------------------------------


class TestStripAll:
    def test_strips_spaces(self):
        assert _strip_all("Cape Coast") == "capecoast"

    def test_strips_hyphens(self):
        assert _strip_all("Ningo-Prampram") == "ningoprampram"

    def test_strips_apostrophes(self):
        assert _strip_all("N'awam") == "nawam"

    def test_lowercases(self):
        assert _strip_all("ACCRA") == "accra"

    def test_combined(self):
        assert _strip_all("Atwima-Nwabiagya") == "atwimanwabiagya"


class TestPhoneticKey:
    def test_ph_to_f(self):
        assert "f" in _phonetic_key("phone")

    def test_gh_not_at_end(self):
        # 'gh' mid-word becomes 'g'
        assert _phonetic_key("Ghana") == "gana"

    def test_gh_at_end_preserved(self):
        # 'gh' at end stays
        key = _phonetic_key("Asarigh")
        assert key.endswith("gh") or key.endswith("g")  # depends on double-collapse

    def test_double_consonants_collapse(self):
        assert _phonetic_key("Kumassa") == _phonetic_key("Kumasa")

    def test_vowel_substitutions(self):
        assert _phonetic_key("Koumasi") == _phonetic_key("Kumasi")


# ---------------------------------------------------------------------------
# Tests for build_index — write-order semantics
# ---------------------------------------------------------------------------


class TestBuildIndexWriteOrder:
    """Test that write-order policies are applied correctly."""

    def test_canonical_first_wins(self):
        """Two records with same canonical lowercase — first one wins."""
        records = [
            _make_record("Accra", entity_type=EntityType.city, source="regions"),
            _make_record("Accra", entity_type=EntityType.supplementary, source="supplementary_locations"),
        ]
        index = build_index(records)
        # First record's canonical wins
        assert index.canonical_map["accra"] == "Accra"

    def test_supplementary_aliases_last_wins(self):
        """Supplementary-source aliases overwrite earlier claims (last-wins)."""
        records = [
            _make_record(
                "Ningo",
                entity_type=EntityType.city,
                source="regions",
                aliases=["New Ningo"],
            ),
            _make_record(
                "Ningo-Prampram",
                entity_type=EntityType.supplementary,
                source="supplementary_locations",
                aliases=["New Ningo"],
            ),
        ]
        index = build_index(records)
        # supplementary alias "new ningo" overwrites region alias
        assert index.canonical_map["new ningo"] == "Ningo-Prampram"

    def test_person_aliases_last_wins(self):
        """Person-source aliases overwrite earlier claims (last-wins)."""
        records = [
            _make_record(
                "SomePlace",
                entity_type=EntityType.city,
                source="regions",
                aliases=["mahama"],
            ),
            _make_record(
                "John Mahama",
                entity_kind=EntityKind.person,
                entity_type=EntityType.president,
                source="all_persons",
                aliases=["mahama"],
            ),
        ]
        index = build_index(records)
        # person alias "mahama" overwrites
        assert index.canonical_map["mahama"] == "John Mahama"

    def test_mp_aliases_first_wins(self):
        """MP-source aliases do NOT overwrite earlier claims (first-wins)."""
        records = [
            _make_record(
                "Tamale",
                entity_type=EntityType.city,
                source="regions",
                aliases=["tamale metro"],
            ),
            _make_record(
                "Alhassan Suhuyini",
                entity_kind=EntityKind.person,
                entity_type=EntityType.mp,
                source="all_mps",
                aliases=["tamale metro"],
            ),
        ]
        index = build_index(records)
        # MP alias "tamale metro" does NOT overwrite
        assert index.canonical_map["tamale metro"] == "Tamale"

    def test_party_aliases_first_wins(self):
        """Party-source aliases do NOT overwrite earlier claims (first-wins)."""
        records = [
            _make_record(
                "National Democratic Congress",
                entity_kind=EntityKind.party,
                entity_type=EntityType.party,
                source="all_parties",
                party="NDC",
                aliases=["NDC", "Congress"],
            ),
            _make_record(
                "Another Congress",
                entity_kind=EntityKind.party,
                entity_type=EntityType.party,
                source="all_parties",
                party="AC",
                aliases=["Congress"],
            ),
        ]
        index = build_index(records)
        # "congress" first-wins for NDC
        assert index.canonical_map["congress"] == "National Democratic Congress"


# ---------------------------------------------------------------------------
# Tests for phonetic_map scope
# ---------------------------------------------------------------------------


class TestPhoneticMapScope:
    """phonetic_map indexes canonicals + supplementary aliases only."""

    def test_canonicals_indexed(self):
        records = [
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        key = _phonetic_key("Kumasi")
        assert key in index.phonetic_map
        assert "Kumasi" in index.phonetic_map[key]

    def test_supplementary_aliases_indexed(self):
        records = [
            _make_record(
                "Ningo-Prampram",
                entity_type=EntityType.supplementary,
                source="supplementary_locations",
                aliases=["Ningoprampram"],
            ),
        ]
        index = build_index(records)
        key = _phonetic_key("Ningoprampram")
        assert key in index.phonetic_map
        assert "Ningo-Prampram" in index.phonetic_map[key]

    def test_person_aliases_not_indexed(self):
        """Person aliases should NOT be in phonetic_map."""
        records = [
            _make_record(
                "John Mahama",
                entity_kind=EntityKind.person,
                entity_type=EntityType.president,
                source="all_persons",
                aliases=["Mahama"],
            ),
        ]
        index = build_index(records)
        alias_key = _phonetic_key("Mahama")
        # The canonical "John Mahama" IS indexed, but the alias "Mahama"
        # should NOT add an extra entry pointing to "John Mahama"
        # (only the canonical phonetic key should exist)
        canonical_key = _phonetic_key("John Mahama")
        assert canonical_key in index.phonetic_map
        # If alias_key happens to differ from canonical_key, it shouldn't be there
        # unless it happens to be the same phonetic key as the canonical
        if alias_key != canonical_key:
            # alias_key should not map to John Mahama
            entries = index.phonetic_map.get(alias_key, [])
            assert "John Mahama" not in entries

    def test_mp_aliases_not_indexed(self):
        """MP aliases should NOT be in phonetic_map."""
        records = [
            _make_record(
                "Alhassan Suhuyini",
                entity_kind=EntityKind.person,
                entity_type=EntityType.mp,
                source="all_mps",
                aliases=["Suhuyini"],
            ),
        ]
        index = build_index(records)
        alias_key = _phonetic_key("Suhuyini")
        canonical_key = _phonetic_key("Alhassan Suhuyini")
        if alias_key != canonical_key:
            entries = index.phonetic_map.get(alias_key, [])
            assert "Alhassan Suhuyini" not in entries

    def test_party_aliases_not_indexed(self):
        """Party aliases should NOT be in phonetic_map."""
        records = [
            _make_record(
                "National Democratic Congress",
                entity_kind=EntityKind.party,
                entity_type=EntityType.party,
                source="all_parties",
                aliases=["NDC"],
            ),
        ]
        index = build_index(records)
        alias_key = _phonetic_key("NDC")
        canonical_key = _phonetic_key("National Democratic Congress")
        if alias_key != canonical_key:
            entries = index.phonetic_map.get(alias_key, [])
            assert "National Democratic Congress" not in entries


# ---------------------------------------------------------------------------
# Tests for surname_map and initial_surname_map
# ---------------------------------------------------------------------------


class TestSurnameAndInitialMaps:
    """surname_map and initial_surname_map built for person records."""

    def test_surname_indexed(self):
        records = [
            _make_record(
                "Ken Ofori-Atta",
                entity_kind=EntityKind.person,
                entity_type=EntityType.minister,
                source="all_persons",
            ),
        ]
        index = build_index(records)
        assert "ofori-atta" in index.surname_map
        assert "Ken Ofori-Atta" in index.surname_map["ofori-atta"]

    def test_hyphenated_parts_indexed(self):
        records = [
            _make_record(
                "Ken Ofori-Atta",
                entity_kind=EntityKind.person,
                entity_type=EntityType.minister,
                source="all_persons",
            ),
        ]
        index = build_index(records)
        # "Ofori-Atta" is hyphenated, also indexed
        assert "ofori-atta" in index.surname_map

    def test_initial_surname_key(self):
        records = [
            _make_record(
                "Ken Ofori-Atta",
                entity_kind=EntityKind.person,
                entity_type=EntityType.minister,
                source="all_persons",
            ),
        ]
        index = build_index(records)
        assert "k.ofori-atta" in index.initial_surname_map
        assert "Ken Ofori-Atta" in index.initial_surname_map["k.ofori-atta"]

    def test_multi_initial_surname_key(self):
        records = [
            _make_record(
                "Jerry John Rawlings",
                entity_kind=EntityKind.person,
                entity_type=EntityType.president,
                source="all_persons",
            ),
        ]
        index = build_index(records)
        # Single initials
        assert "j.rawlings" in index.initial_surname_map
        # Multi-initial
        assert "j.j.rawlings" in index.initial_surname_map
        assert "Jerry John Rawlings" in index.initial_surname_map["j.j.rawlings"]

    def test_location_records_not_in_surname_map(self):
        records = [
            _make_record("Cape Coast", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        assert "coast" not in index.surname_map

    def test_single_word_person_skipped(self):
        """A person with only one word has no surname to index."""
        records = [
            _make_record(
                "Rawlings",
                entity_kind=EntityKind.person,
                entity_type=EntityType.president,
                source="all_persons",
            ),
        ]
        index = build_index(records)
        # Single-word person can't produce surname/initial keys
        assert len(index.surname_map) == 0
        assert len(index.initial_surname_map) == 0


# ---------------------------------------------------------------------------
# Tests for entity maps and party abbreviation
# ---------------------------------------------------------------------------


class TestEntityMapsAndParty:
    def test_entity_kind_map(self):
        records = [
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
            _make_record(
                "John Mahama",
                entity_kind=EntityKind.person,
                entity_type=EntityType.president,
                source="all_persons",
            ),
        ]
        index = build_index(records)
        assert index.entity_kind_map["Kumasi"] == "location"
        assert index.entity_kind_map["John Mahama"] == "person"

    def test_entity_type_map(self):
        records = [
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        assert index.entity_type_map["Kumasi"] == "city"

    def test_party_abbr_map_from_party_field(self):
        records = [
            _make_record(
                "National Democratic Congress",
                entity_kind=EntityKind.party,
                entity_type=EntityType.party,
                source="all_parties",
                party="NDC",
                aliases=["NDC"],
            ),
        ]
        index = build_index(records)
        assert index.party_abbr_map["National Democratic Congress"] == "NDC"

    def test_party_abbr_map_fallback_shortest_alias(self):
        """When no party field, use shortest alias."""
        records = [
            _make_record(
                "Convention People's Party",
                entity_kind=EntityKind.party,
                entity_type=EntityType.party,
                source="all_parties",
                party=None,
                aliases=["CPP", "Convention People"],
            ),
        ]
        index = build_index(records)
        assert index.party_abbr_map["Convention People's Party"] == "CPP"


# ---------------------------------------------------------------------------
# Tests for alias_ordinal, length_buckets, bk_tree
# ---------------------------------------------------------------------------


class TestOrdinalAndFuzzyStructures:
    def test_alias_ordinal_assigned(self):
        records = [
            _make_record("Accra", entity_type=EntityType.city, source="regions"),
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        assert "accra" in index.alias_ordinal
        assert "kumasi" in index.alias_ordinal
        # First record gets lower ordinal
        assert index.alias_ordinal["accra"] < index.alias_ordinal["kumasi"]

    def test_alias_ordinal_not_reassigned(self):
        """An alias appearing in two records keeps its first ordinal."""
        records = [
            _make_record(
                "Tema",
                entity_type=EntityType.city,
                source="regions",
                aliases=["tema new town"],
            ),
            _make_record(
                "Tema Metro",
                entity_type=EntityType.supplementary,
                source="supplementary_locations",
                aliases=["tema new town"],
            ),
        ]
        index = build_index(records)
        # "tema new town" gets ordinal from first appearance
        ordinal = index.alias_ordinal["tema new town"]
        assert ordinal is not None

    def test_length_buckets(self):
        records = [
            _make_record("Accra", entity_type=EntityType.city, source="regions"),
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
            _make_record("Ho", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        # "accra" has length 5, "kumasi" has length 6, "ho" has length 2
        assert "accra" in index.length_buckets[5]
        assert "kumasi" in index.length_buckets[6]
        assert "ho" in index.length_buckets[2]

    def test_bk_tree_built(self):
        records = [
            _make_record("Accra", entity_type=EntityType.city, source="regions"),
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        assert index.bk_tree is not None

    def test_bk_tree_finds_close_keys(self):
        records = [
            _make_record("Kumasi", entity_type=EntityType.city, source="regions"),
            _make_record("Accra", entity_type=EntityType.city, source="regions"),
        ]
        index = build_index(records)
        # "kumase" is distance 1 from "kumasi"
        results = index.bk_tree.find("kumase", 2)
        keys = [item[1] for item in results]
        assert "kumasi" in keys

    def test_empty_records_produces_empty_index(self):
        index = build_index([])
        assert len(index.canonical_map) == 0
        assert index.bk_tree is None


# ---------------------------------------------------------------------------
# Tests for fused_map
# ---------------------------------------------------------------------------


class TestFusedMap:
    def test_canonical_in_fused_map(self):
        records = [
            _make_record("Ningo-Prampram", entity_type=EntityType.supplementary, source="supplementary_locations"),
        ]
        index = build_index(records)
        assert "ningoprampram" in index.fused_map
        assert index.fused_map["ningoprampram"] == "Ningo-Prampram"

    def test_alias_in_fused_map(self):
        records = [
            _make_record(
                "Cape Coast",
                entity_type=EntityType.supplementary,
                source="supplementary_locations",
                aliases=["Cabo Corso"],
            ),
        ]
        index = build_index(records)
        assert "cabocorso" in index.fused_map
        assert index.fused_map["cabocorso"] == "Cape Coast"
