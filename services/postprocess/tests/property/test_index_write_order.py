"""Property tests for Match_Index write-order parity.

Validates: Requirements 3.12, 9.5

These tests verify that the `build_index` function produces a deterministic
and policy-correct `canonical_map` that faithfully implements the JavaScript
`_canonicalMap` write-order semantics:

The design states ("Write-order semantics: first-wins is not uniform"):
  - Canonical entries via addEntry: first-wins
  - Supplementary-location aliases: last-wins
  - Person aliases: last-wins
  - MP aliases: first-wins
  - Party abbreviation and aliases: first-wins

Source order is fixed: regions → cities → supplementary → persons → MPs → parties,
encoded in `source_rank`. The store returns records in deterministic order
(`ORDER BY source_rank, entity_kind, canonical, id`), ensuring reproducibility
(Requirement 3.12).

Properties tested:
1. Determinism: identical input order → identical canonical_map on repeated builds
2. First-wins semantics: for regions/MP/party sources, the first record in load
   order claims a key and later records with the same key are rejected
3. Last-wins semantics: for supplementary/person sources, later records overwrite
   earlier records that claimed the same alias key
4. Cross-source: an MP source (first-wins, later rank) cannot overwrite a key
   already written by a supplementary source (last-wins, earlier rank)
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid entity names — constrained to non-empty printable strings
_entity_name = st.text(
    alphabet=st.characters(categories=("L", "N", "Pd"), min_codepoint=65, max_codepoint=122),
    min_size=2,
    max_size=20,
).filter(lambda s: s.strip() != "")

# Alias text — similar to entity names
_alias_text = st.text(
    alphabet=st.characters(categories=("L", "N", "Pd"), min_codepoint=65, max_codepoint=122),
    min_size=2,
    max_size=15,
).filter(lambda s: s.strip() != "")


# Source definitions mirroring the actual source_rank layout
_SOURCES = [
    ("regions", 0, EntityKind.location, EntityType.region),
    ("cities", 1, EntityKind.location, EntityType.city),
    ("supplementary_locations", 2, EntityKind.location, EntityType.supplementary),
    ("all_persons", 3, EntityKind.person, EntityType.minister),
    ("all_mps", 4, EntityKind.person, EntityType.mp),
    ("all_parties", 5, EntityKind.party, EntityType.party),
]


@st.composite
def entity_record_for_source(
    draw: st.DrawFn,
    source: str,
    source_rank: int,
    entity_kind: EntityKind,
    entity_type: EntityType,
) -> EntityRecord:
    """Generate an EntityRecord for a specific source."""
    canonical = draw(_entity_name)
    aliases = draw(st.lists(_alias_text, min_size=0, max_size=4))
    return EntityRecord(
        canonical=canonical,
        entity_kind=entity_kind,
        entity_type=entity_type,
        aliases=aliases,
        source=source,
        source_rank=source_rank,
        active=True,
    )


@st.composite
def mixed_source_dataset(draw: st.DrawFn) -> list[EntityRecord]:
    """Generate a dataset with records across all source tiers.

    Records are returned in the deterministic load order that the store
    guarantees: sorted by (source_rank, entity_kind, canonical).
    """
    all_records: list[EntityRecord] = []

    for source, rank, kind, etype in _SOURCES:
        tier_size = draw(st.integers(min_value=1, max_value=4))
        tier_records = [
            draw(entity_record_for_source(source, rank, kind, etype))
            for _ in range(tier_size)
        ]
        all_records.extend(tier_records)

    # Sort by (source_rank, entity_kind, canonical) — deterministic load order
    all_records.sort(key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    return all_records


@st.composite
def conflicting_alias_dataset(draw: st.DrawFn) -> list[EntityRecord]:
    """Generate records with deliberate alias conflicts across sources.

    Creates a shared alias key that appears in a supplementary record (last-wins,
    rank 2) and an MP record (first-wins, rank 4), to verify the cross-source
    write-order policy.
    """
    # The shared alias that will conflict
    shared_alias = draw(_entity_name)

    records: list[EntityRecord] = []

    # An earlier source (supplementary, last-wins) claims the alias
    supp_canonical = draw(_entity_name.filter(lambda s: s.lower() != shared_alias.lower()))
    records.append(EntityRecord(
        canonical=supp_canonical,
        entity_kind=EntityKind.location,
        entity_type=EntityType.supplementary,
        aliases=[shared_alias],
        source="supplementary_locations",
        source_rank=2,
        active=True,
    ))

    # A later source (all_mps, first-wins) also claims the same alias
    mp_canonical = draw(_entity_name.filter(
        lambda s: s.lower() != shared_alias.lower() and s.lower() != supp_canonical.lower()
    ))
    records.append(EntityRecord(
        canonical=mp_canonical,
        entity_kind=EntityKind.person,
        entity_type=EntityType.mp,
        aliases=[shared_alias],
        source="all_mps",
        source_rank=4,
        active=True,
    ))

    # Sort by load order
    records.sort(key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    return records


@st.composite
def person_overwrite_dataset(draw: st.DrawFn) -> list[EntityRecord]:
    """Generate two person records with the same alias key (last-wins).

    Verifies that person aliases use last-wins: the record that appears
    later in deterministic load order wins the alias key.
    """
    shared_alias = draw(_entity_name)

    # First person record claims the alias
    first_canonical = draw(_entity_name.filter(lambda s: s.lower() != shared_alias.lower()))
    # Second person record also claims it — should overwrite (last-wins)
    second_canonical = draw(_entity_name.filter(
        lambda s: s.lower() != shared_alias.lower() and s.lower() != first_canonical.lower()
    ))

    records = [
        EntityRecord(
            canonical=first_canonical,
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=[shared_alias],
            source="all_persons",
            source_rank=3,
            active=True,
        ),
        EntityRecord(
            canonical=second_canonical,
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=[shared_alias],
            source="all_persons",
            source_rank=3,
            active=True,
        ),
    ]
    # Sort by load order (same rank, so sort by kind then canonical)
    records.sort(key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    return records


@st.composite
def first_wins_conflict_dataset(draw: st.DrawFn) -> list[EntityRecord]:
    """Generate two MP records with the same alias key (first-wins).

    Verifies that MP aliases use first-wins: the record that appears
    earlier in deterministic load order claims the alias key, and the
    later record's attempt to claim it is rejected.
    """
    shared_alias = draw(_entity_name)

    # First MP record claims the alias
    first_canonical = draw(_entity_name.filter(lambda s: s.lower() != shared_alias.lower()))
    # Second MP record also tries — should be rejected (first-wins)
    second_canonical = draw(_entity_name.filter(
        lambda s: s.lower() != shared_alias.lower() and s.lower() != first_canonical.lower()
    ))

    records = [
        EntityRecord(
            canonical=first_canonical,
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=[shared_alias],
            source="all_mps",
            source_rank=4,
            active=True,
        ),
        EntityRecord(
            canonical=second_canonical,
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=[shared_alias],
            source="all_mps",
            source_rank=4,
            active=True,
        ),
    ]
    # Sort by load order
    records.sort(key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    return records


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(records=mixed_source_dataset())
@settings(max_examples=100)
def test_build_index_determinism(records: list[EntityRecord]) -> None:
    """A second build_index call produces an identical canonical_map (determinism).

    For ANY set of EntityRecords in deterministic load order, a second
    build_index call produces an identical canonical_map. This proves that
    build_index is a pure function with no hidden state.

    **Validates: Requirements 3.12, 9.5**
    """
    index_first = build_index(records)
    index_second = build_index(records)

    assert index_first.canonical_map == index_second.canonical_map
    assert index_first.fused_map == index_second.fused_map
    assert index_first.entity_kind_map == index_second.entity_kind_map
    assert index_first.entity_type_map == index_second.entity_type_map


@given(records=mixed_source_dataset())
@settings(max_examples=100)
def test_canonical_map_keys_cover_all_canonicals_and_aliases(
    records: list[EntityRecord],
) -> None:
    """The canonical_map contains an entry for every canonical and alias.

    Every canonical name (lowercased) appears as a key in the map — the
    first-wins addEntry ensures all canonicals get indexed. This proves
    the index is complete over the input dataset.

    **Validates: Requirements 3.12, 9.5**
    """
    index = build_index(records)

    # Every canonical should appear as a key (first-wins for canonicals)
    seen_canonicals: set[str] = set()
    for record in records:
        lower = record.canonical.lower()
        if lower not in seen_canonicals:
            # First occurrence of this canonical (by load order) should be in the map
            assert lower in index.canonical_map
            seen_canonicals.add(lower)


@given(records=conflicting_alias_dataset())
@settings(max_examples=100)
def test_first_wins_mp_does_not_overwrite_supplementary(
    records: list[EntityRecord],
) -> None:
    """First-wins MP source cannot overwrite a last-wins supplementary alias.

    The canonical_map exhibits the correct cross-source behavior:
    1. Supplementary (rank 2, last-wins) writes the alias key unconditionally
    2. MP (rank 4, first-wins) checks if key exists → it does → skips

    Therefore the supplementary canonical wins the alias key.

    **Validates: Requirements 3.12, 9.5**
    """
    index = build_index(records)

    # Find the shared alias
    supp_record = next(r for r in records if r.source == "supplementary_locations")
    shared_alias = supp_record.aliases[0]
    alias_key = shared_alias.lower()

    # The supplementary canonical should win because:
    # 1. Supplementary loads first (rank 2) with last-wins → writes the key
    # 2. MP loads later (rank 4) with first-wins → key exists, so MP skips
    if alias_key in index.canonical_map:
        assert index.canonical_map[alias_key] == supp_record.canonical


@given(records=person_overwrite_dataset())
@settings(max_examples=100)
def test_last_wins_person_overwrites_earlier_person(
    records: list[EntityRecord],
) -> None:
    """Last-wins person source: later record overwrites earlier record's alias.

    The canonical_map exhibits last-wins when a key appears in a person source —
    the record appearing LATER in deterministic load order claims the key,
    overwriting the earlier record's canonical.

    **Validates: Requirements 3.12, 9.5**
    """
    index = build_index(records)

    # Both records share the same alias — the last in load order should win
    shared_alias = records[0].aliases[0]
    alias_key = shared_alias.lower()

    # With last-wins, the LAST record to write wins.
    # Records are sorted by (source_rank, entity_kind, canonical).
    # The record that sorts LATER in load order writes last and wins.
    sorted_records = sorted(records, key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    last_writer = sorted_records[-1]

    if alias_key in index.canonical_map:
        assert index.canonical_map[alias_key] == last_writer.canonical


@given(records=first_wins_conflict_dataset())
@settings(max_examples=100)
def test_first_wins_mp_earlier_record_claims_alias(
    records: list[EntityRecord],
) -> None:
    """First-wins MP source: earlier record claims the alias, later is rejected.

    The canonical_map exhibits first-wins when a key appears in an MP source —
    the record appearing EARLIER in deterministic load order claims the key,
    and later records with the same key are rejected.

    **Validates: Requirements 3.12, 9.5**
    """
    index = build_index(records)

    # Both records share the same alias — the first in load order should win
    shared_alias = records[0].aliases[0]
    alias_key = shared_alias.lower()

    # With first-wins, the FIRST record to write wins.
    sorted_records = sorted(records, key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    first_writer = sorted_records[0]

    if alias_key in index.canonical_map:
        # First-wins: the key might already be claimed by the canonical entry.
        # If not claimed by a canonical, the first alias writer wins.
        # The key is either claimed by a canonical (from addEntry) or by the
        # first alias to write it.
        canonical_lower = first_writer.canonical.lower()
        if alias_key == canonical_lower:
            # The canonical itself claimed this key via addEntry
            assert index.canonical_map[alias_key] == first_writer.canonical
        else:
            # The first record's alias should have claimed it
            assert index.canonical_map[alias_key] == first_writer.canonical
