"""Match_Index build — derived lookup structures for the Correction_Engine.

Builds all index structures once per Dataset_Cache load (Requirement 10.5),
using a candidate-narrowing BK-tree so edit-distance comparisons do not grow
linearly with total alias count (Requirement 10.4).

The write-order semantics reproduce the JavaScript `buildDataset()` logic
exactly (Requirement 3.12):
  - Canonical entries via addEntry: first-wins
  - Supplementary-location aliases: last-wins
  - Person aliases: last-wins
  - MP aliases: first-wins
  - Party abbreviation and aliases: first-wins

Requirements: 10.4, 10.5, 3.12
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pybktree
from rapidfuzz.distance import Levenshtein

from app.models.entities import EntityKind, EntityRecord, EntityType


def _strip_all(s: str) -> str:
    """Strip spaces, hyphens, and apostrophes for fused-word matching.

    "Ningo-Prampram" → "ningoprampram"
    "Cape Coast" → "capecoast"
    """
    return re.sub(r"[\s\-']", "", s).lower()


def _phonetic_key(s: str) -> str:
    """Generate a phonetic key optimized for West African/Ghanaian names.

    Collapses common ASR substitution patterns — identical to the JS
    `phoneticKey` function.
    """
    result = re.sub(r"[\s\-']", "", s.lower())
    # Common substitutions in ASR
    result = result.replace("ph", "f")
    result = re.sub(r"gh(?!$)", "g", result)  # 'gh' not at end
    result = result.replace("ck", "k")
    result = re.sub(r"ei|ey", "e", result)
    result = re.sub(r"ou|oo", "u", result)
    result = re.sub(r"aa", "a", result)
    result = re.sub(r"ee", "e", result)
    result = re.sub(r"ii", "i", result)
    # Collapse double consonants
    result = re.sub(r"(.)\1+", r"\1", result)
    return result


def _levenshtein_distance(a: str, b: str) -> int:
    """Levenshtein distance for BK-tree — wraps rapidfuzz."""
    return Levenshtein.distance(a, b)


def _is_last_wins_source(source: str | None) -> bool:
    """Determine if a source uses last-wins write-order policy.

    Sources with "supplementary" or "persons" in the name → last-wins.
    Everything else (canonicals, "mps", "parties") → first-wins.
    """
    if source is None:
        return False
    source_lower = source.lower()
    return "supplementary" in source_lower or "persons" in source_lower


@dataclass
class MatchIndex:
    """Derived lookup structures built once per Dataset_Cache load.

    All maps are keyed on lowercased alias text unless noted otherwise.
    """

    # alias-lower → canonical name
    canonical_map: dict[str, str] = field(default_factory=dict)

    # stripped form (no spaces/hyphens/apostrophes) → canonical name
    fused_map: dict[str, str] = field(default_factory=dict)

    # phonetic key → list of canonical names
    # Scoped to canonicals + supplementary-location aliases ONLY
    phonetic_map: dict[str, list[str]] = field(default_factory=dict)

    # surname (lowercase) → list of canonical names (person records only)
    surname_map: dict[str, list[str]] = field(default_factory=dict)

    # "initial.surname" → list of canonical names (person records only)
    initial_surname_map: dict[str, list[str]] = field(default_factory=dict)

    # canonical name → entity kind string
    entity_kind_map: dict[str, str] = field(default_factory=dict)

    # canonical name → entity type string
    entity_type_map: dict[str, str] = field(default_factory=dict)

    # canonical party name → abbreviation
    party_abbr_map: dict[str, str] = field(default_factory=dict)

    # alias key (lowercase) → load ordinal (for deterministic tie-breaks)
    alias_ordinal: dict[str, int] = field(default_factory=dict)

    # alias length → list of alias keys (for fuzzy candidate narrowing)
    length_buckets: dict[int, list[str]] = field(default_factory=dict)

    # BK-tree over alias keys for fuzzy candidate narrowing
    bk_tree: pybktree.BKTree | None = None


def build_index(records: list[EntityRecord]) -> MatchIndex:
    """Build a MatchIndex from a list of EntityRecords.

    Records MUST be ordered by (source_rank, entity_kind, canonical, id) —
    the same deterministic order returned by store.load_active_records().

    This function reproduces the per-source write-order policy from the
    JavaScript buildDataset() exactly.
    """
    index = MatchIndex()

    # Track all canonicals added (for phonetic indexing)
    all_canonicals: list[str] = []

    # Ordinal counter for alias_ordinal (deterministic tie-breaks)
    ordinal_counter = 0

    def _assign_ordinal(key: str) -> None:
        """Assign a load ordinal to an alias key if not already assigned."""
        nonlocal ordinal_counter
        if key not in index.alias_ordinal:
            index.alias_ordinal[key] = ordinal_counter
            ordinal_counter += 1

    def _add_entry(canonical: str, entity_type: EntityType, entity_kind: EntityKind) -> None:
        """Add a canonical entry with first-wins policy (mirrors JS addEntry)."""
        lower = canonical.lower()
        if lower not in index.canonical_map:
            index.canonical_map[lower] = canonical
            all_canonicals.append(canonical)
            index.fused_map[_strip_all(lower)] = canonical
            _assign_ordinal(lower)

        if canonical not in index.entity_type_map:
            index.entity_type_map[canonical] = entity_type.value
            index.entity_kind_map[canonical] = entity_kind.value

    def _add_alias_first_wins(alias: str, canonical: str) -> None:
        """Add an alias with first-wins policy (MP aliases, party aliases)."""
        key = alias.lower()
        if key not in index.canonical_map:
            index.canonical_map[key] = canonical
            index.fused_map[_strip_all(key)] = canonical
            _assign_ordinal(key)

    def _add_alias_last_wins(alias: str, canonical: str) -> None:
        """Add an alias with last-wins policy (supplementary, person aliases)."""
        key = alias.lower()
        index.canonical_map[key] = canonical
        index.fused_map[_strip_all(key)] = canonical
        _assign_ordinal(key)

    # -----------------------------------------------------------------------
    # Phase 1: Process all records in load order, applying write-order policy
    # -----------------------------------------------------------------------
    for record in records:
        canonical = record.canonical
        entity_kind = record.entity_kind
        entity_type = record.entity_type

        # Always add the canonical with first-wins
        _add_entry(canonical, entity_type, entity_kind)

        # Determine alias write policy based on source
        last_wins = _is_last_wins_source(record.source)

        # Add aliases
        for alias in record.aliases:
            if last_wins:
                _add_alias_last_wins(alias, canonical)
            else:
                _add_alias_first_wins(alias, canonical)

        # Party abbreviation map: canonical → abbreviation
        if entity_kind == EntityKind.party:
            if record.party and canonical not in index.party_abbr_map:
                # The 'party' field on a party record holds the abbreviation
                index.party_abbr_map[canonical] = record.party
            elif record.aliases and canonical not in index.party_abbr_map:
                # Fallback: use the shortest alias as abbreviation
                shortest = min(record.aliases, key=len) if record.aliases else None
                if shortest:
                    index.party_abbr_map[canonical] = shortest

    # -----------------------------------------------------------------------
    # Phase 2: Build phonetic_map — scoped to canonicals + supplementary aliases
    # -----------------------------------------------------------------------
    # Index all canonicals
    for canonical in all_canonicals:
        key = _phonetic_key(canonical)
        if key not in index.phonetic_map:
            index.phonetic_map[key] = []
        if canonical not in index.phonetic_map[key]:
            index.phonetic_map[key].append(canonical)

    # Index supplementary-location aliases only (not person, MP, or party)
    for record in records:
        if record.entity_type == EntityType.supplementary or (
            record.source and "supplementary" in record.source.lower()
        ):
            for alias in record.aliases:
                key = _phonetic_key(alias)
                if key not in index.phonetic_map:
                    index.phonetic_map[key] = []
                if record.canonical not in index.phonetic_map[key]:
                    index.phonetic_map[key].append(record.canonical)

    # -----------------------------------------------------------------------
    # Phase 3: Build surname_map and initial_surname_map (person records only)
    # -----------------------------------------------------------------------
    for record in records:
        if record.entity_kind != EntityKind.person:
            continue

        canonical = record.canonical
        parts = canonical.split()
        if len(parts) < 2:
            continue

        # Surname = last part (handling hyphenated like "Ofori-Atta")
        surname = parts[-1].lower()
        if surname not in index.surname_map:
            index.surname_map[surname] = []
        index.surname_map[surname].append(canonical)

        # Also index hyphenated full surnames e.g. "Afenyo-Markin"
        for part in parts:
            if "-" in part:
                hyph = part.lower()
                if hyph not in index.surname_map:
                    index.surname_map[hyph] = []
                if canonical not in index.surname_map[hyph]:
                    index.surname_map[hyph].append(canonical)

        # Build initial+surname keys: "j.rawlings", "j.j.rawlings", "k.ofori-atta"
        initials = [p[0].lower() for p in parts[:-1]]
        surname_key = surname

        # Single initial + surname
        for ini in initials:
            key = f"{ini}.{surname_key}"
            if key not in index.initial_surname_map:
                index.initial_surname_map[key] = []
            index.initial_surname_map[key].append(canonical)

        # Multi-initial + surname (e.g. "j.j.rawlings", "j.e.a.mills")
        if len(initials) >= 2:
            multi_key = ".".join(initials) + "." + surname_key
            if multi_key not in index.initial_surname_map:
                index.initial_surname_map[multi_key] = []
            index.initial_surname_map[multi_key].append(canonical)

    # -----------------------------------------------------------------------
    # Phase 4: Build length_buckets and BK-tree for fuzzy candidate narrowing
    # -----------------------------------------------------------------------
    all_keys = list(index.canonical_map.keys())

    # Length buckets: group keys by character length
    for key in all_keys:
        length = len(key)
        if length not in index.length_buckets:
            index.length_buckets[length] = []
        index.length_buckets[length].append(key)

    # BK-tree over canonical_map keys using Levenshtein distance
    if all_keys:
        index.bk_tree = pybktree.BKTree(_levenshtein_distance, all_keys)

    return index
