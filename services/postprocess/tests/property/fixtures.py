"""Shared fixed in-memory Dataset_Cache fixture for the property tests (Req 16.15).

Every hypothesis property test in this feature (Properties 1-15, tasks 1.x-6.2)
draws its inputs from ONE fixed in-memory Dataset_Cache fixture so the generated
Words, tokens, confidences, and timings are exercised against a stable, curated
entity set rather than the several-thousand-entity production datasets. Req 16.15
mandates that fixture hold:

* at least 20 canonical entities with their aliases;
* at least 10 Block_List entries; and
* the bundled English_Lexicon.

This module owns the fixture DATA. It builds the snapshot exactly the way the
production Dataset_Cache builds one — through :func:`app.datasets.index.build_index`
— so the derived gate indexes (fused, phonetic, phonetic_fanout, component_map,
surname, BK-tree) the engine reads are the same shape as production, matching the
``_make_snapshot`` / ``build_index`` construction pattern already used in
``tests/unit`` and ``tests/evaluation/dataset.py``.

The DatasetSnapshot fields (``version``, ``records``, ``record_count``,
``loaded_at``, ``index``, ``block_list``, ``stopwords``, ``word_stopwords``,
``title_prefixes``) match :class:`app.datasets.cache.DatasetSnapshot`.

The snapshot is built once at import (it is immutable and ``lru_cache``-d), so it
is byte-for-byte identical on every run and can be shared across the whole
property-test suite. No network request and no AWS call is made to build it
(the entity records are inline literals and the lexicon loads from a bundled
local artifact).

Hypothesis strategies that generate Words/tokens/confidences/timings from this
fixture live in :mod:`tests.property.strategies`; the pytest fixtures that expose
both live in ``tests/property/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from app.correction.lexicon import DEFAULT_LEXICON_PATH, EnglishLexicon, load_lexicon
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType

# Fixed dataset version — constant so the snapshot (and anything derived from it)
# is identical on every run, supporting the determinism the property tests rely on.
FIXTURE_DATASET_VERSION = "property-fixture-2025-01-01T00:00:00+00:00"


def _records() -> list[EntityRecord]:
    """Return the curated Entity_Records for the property-test fixture.

    At least 20 canonical entities, each with aliases where natural, spanning
    person, location, constituency, region, and party kinds so the phonetic,
    fuzzy, component, and deterministic paths are all exercised. The set
    deliberately includes the canonical entities the recorded false positives
    collide with — Ghana, Sege, Bole, Lartey (via *Agnes Naa Momo Lartey*),
    and *Joseph Bukari Nikpe* — so the Block_List entries below sit on top of
    real collision targets.

    Ordered the way ``store.load_active_records`` returns records
    (source_rank, entity_kind, canonical) so ``build_index`` reproduces the
    production write-order policy.
    """
    records: list[EntityRecord] = [
        # --- Locations: regions ---
        EntityRecord(
            canonical="Greater Accra",
            entity_kind=EntityKind.location,
            entity_type=EntityType.region,
            aliases=["Accra Region"],
            source="regions",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Northern",
            entity_kind=EntityKind.location,
            entity_type=EntityType.region,
            aliases=["Northern Region"],
            source="regions",
            source_rank=0,
        ),
        # --- Locations: cities / constituencies (collision targets) ---
        EntityRecord(
            canonical="Sege",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=["Sege Constituency"],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Bole",
            entity_kind=EntityKind.location,
            entity_type=EntityType.constituency,
            aliases=["Bole Bamboi"],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="La",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Kumasi",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["Kumase"],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Tamale",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["Tamali"],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Winneba",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
            source="cities",
            source_rank=1,
        ),
        # --- Locations: supplementary (rich aliases; phonetic-indexed) ---
        EntityRecord(
            canonical="Ghana",
            entity_kind=EntityKind.location,
            entity_type=EntityType.supplementary,
            aliases=["Gana", "Ghanna", "Ghanah"],
            source="supplementary",
            source_rank=2,
        ),
        EntityRecord(
            canonical="Ningo-Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.supplementary,
            aliases=["Ningo Prampram", "Ningoprampram", "Nyungoprampram"],
            source="supplementary",
            source_rank=2,
        ),
        EntityRecord(
            canonical="Cape Coast",
            entity_kind=EntityKind.location,
            entity_type=EntityType.supplementary,
            aliases=["Cabo Corso"],
            source="supplementary",
            source_rank=2,
        ),
        # --- Persons: presidents / speakers (rich alias sets) ---
        EntityRecord(
            canonical="Jerry John Rawlings",
            entity_kind=EntityKind.person,
            entity_type=EntityType.president,
            aliases=["Rawlings", "J.J. Rawlings", "JJ Rawlings", "Jerry Rawlings"],
            source="persons_presidents",
            source_rank=3,
        ),
        EntityRecord(
            canonical="John Dramani Mahama",
            entity_kind=EntityKind.person,
            entity_type=EntityType.president,
            aliases=["Mahama", "John Mahama", "Dramani Mahama"],
            source="persons_presidents",
            source_rank=3,
        ),
        EntityRecord(
            canonical="Alban Sumana Kingsford Bagbin",
            entity_kind=EntityKind.person,
            entity_type=EntityType.president,
            aliases=["Bagbin", "Alban Bagbin", "Speaker Bagbin"],
            source="persons_speakers",
            source_rank=3,
        ),
        # --- Persons: ministers / MPs (surname / component / collision targets) ---
        EntityRecord(
            canonical="Mary Grant",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["Grant", "M. Grant"],
            source="ministers",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Alex Segbefia",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["Segbefia", "A. Segbefia"],
            source="ministers",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Agnes Naa Momo Lartey",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Naa Momo", "Agnes Lartey", "Lartey"],
            source="mps",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Joseph Bukari Nikpe",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["Bukari Nikpe", "Nikpe"],
            source="ministers",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Cassiel Ato Forson",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Ato Forson", "Forson", "Cassiel Ato"],
            source="mps",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Osei Kyei-Mensah-Bonsu",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Kyei-Mensah-Bonsu"],
            source="mps",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Samuel Nartey George",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Sam George", "Nartey George", "Sam Nartey George"],
            source="mps",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Samuel Okudzeto Ablakwa",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Ablakwa", "Okudzeto Ablakwa", "Okudzeto"],
            source="mps",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Haruna Iddrisu",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Haruna", "Iddrisu"],
            source="mps",
            source_rank=4,
        ),
        EntityRecord(
            canonical="Ken Ofori-Atta",
            entity_kind=EntityKind.person,
            entity_type=EntityType.mp,
            aliases=["Ofori-Atta", "Ken Ofori Atta"],
            source="mps",
            source_rank=4,
        ),
        # --- Parties (deterministic abbreviation matching) ---
        EntityRecord(
            canonical="National Democratic Congress",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["NDC", "N.D.C.", "National Democratic Congress Party"],
            party="NDC",
            source="parties",
            source_rank=5,
        ),
        EntityRecord(
            canonical="New Patriotic Party",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["NPP", "N.P.P.", "New Patriotic Party Ghana"],
            party="NPP",
            source="parties",
            source_rank=5,
        ),
        EntityRecord(
            canonical="People's National Convention",
            entity_kind=EntityKind.party,
            entity_type=EntityType.party,
            aliases=["PNC", "P.N.C.", "Peoples National Convention"],
            party="PNC",
            source="parties",
            source_rank=5,
        ),
    ]
    records.sort(key=lambda r: (r.source_rank, r.entity_kind.value, r.canonical))
    return records


# ---------------------------------------------------------------------------
# Block_List / stopword / title-prefix sets
# ---------------------------------------------------------------------------
#
# The Block_List entries are real ordinary-English tokens that the fixture
# entities could false-positive on — every one is the *source* side of a
# recorded or plausible collision against an entity above (e.g. "thank"->Ghana,
# "sage"->Sege, "district"->Bole, "transportation"->Joseph Bukari Nikpe,
# "later"->Lartey). Stored lowercase because the engine's Block_List guard
# lowercases the token before the membership check (``is_blocked``). This lets
# property tests exercise the Block_List guard (blocked tokens must never
# receive an approximate correction, Req 11.4/11.8) with the list populated.
FIXTURE_BLOCK_LIST: frozenset[str] = frozenset(
    {
        "thank",
        "sage",
        "district",
        "transportation",
        "later",
        "general",
        "page",
        "nation",
        "national",
        "station",
        "grant",
        "member",
    }
)

# Stopwords rejected before any strategy runs (production-shaped, small).
FIXTURE_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "in",
        "to",
        "is",
        "was",
        "that",
        "for",
        "it",
        "on",
        "as",
        "at",
        "by",
        "be",
        "this",
        "with",
        "from",
        "are",
        "has",
        "have",
        "will",
        "not",
    }
)

FIXTURE_WORD_STOPWORDS: frozenset[str] = FIXTURE_STOPWORDS | frozenset({"i", "we"})

FIXTURE_TITLE_PREFIXES: frozenset[str] = frozenset(
    {
        "hon",
        "honorable",
        "honourable",
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "professor",
        "minister",
        "president",
        "speaker",
    }
)


@lru_cache(maxsize=1)
def build_property_snapshot() -> DatasetSnapshot:
    """Build the fixed property-test DatasetSnapshot (cached; immutable).

    Built through the production :func:`build_index` so the derived gate indexes
    are identical in shape to production. Carries the populated
    :data:`FIXTURE_BLOCK_LIST` (>=10 entries) so property tests can exercise the
    Block_List guard, alongside the bundled English_Lexicon (loaded separately
    via :func:`load_property_lexicon`).

    Because it is ``lru_cache``-d over no arguments and every field is immutable,
    every call returns the same object.
    """
    records = _records()
    index = build_index(records)
    return DatasetSnapshot(
        version=FIXTURE_DATASET_VERSION,
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime(2025, 1, 1, tzinfo=UTC),
        index=index,
        block_list=FIXTURE_BLOCK_LIST,
        stopwords=FIXTURE_STOPWORDS,
        word_stopwords=FIXTURE_WORD_STOPWORDS,
        title_prefixes=FIXTURE_TITLE_PREFIXES,
    )


def property_canonicals() -> frozenset[str]:
    """Return the set of canonical entity names present in the fixture snapshot."""
    return frozenset(r.canonical for r in _records())


def property_alias_set() -> frozenset[str]:
    """Return the lowercased alias/canonical key set of the fixture snapshot.

    Mirrors ``index.canonical_map`` keys — the Dataset_Cache alias set the
    Lexicon_Gate checks membership against — so strategies can draw tokens that
    are known aliases.
    """
    return frozenset(build_property_snapshot().index.canonical_map.keys())


@lru_cache(maxsize=1)
def load_property_lexicon() -> EnglishLexicon:
    """Load the bundled English_Lexicon once for the property tests (Req 16.15).

    Reuses the production loader against the committed artifact, so no network
    access occurs. Cached so repeated loads share one instance.
    """
    return load_lexicon(DEFAULT_LEXICON_PATH)
