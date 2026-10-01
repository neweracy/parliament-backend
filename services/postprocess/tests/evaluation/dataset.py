"""Fixed in-memory Dataset_Cache snapshot for the Evaluation_Harness (Req 10.3, 10.8).

The Evaluation_Harness runs the REAL Correction_Engine against a labelled corpus,
so it needs a Dataset_Cache snapshot that:

* is built exactly the way the production cache builds one — through
  :func:`app.datasets.index.build_index`, so the derived indexes (fused,
  phonetic, phonetic_fanout, component_map, surname, BK-tree) the engine reads
  are the same shape as production (reusing the ``_make_snapshot`` /
  ``build_index`` construction pattern from ``tests/unit``);
* contains every canonical entity a Positive_Set fixture labels (Req 10.3),
  including the ones the recorded false positives collide with — Ghana, Sege,
  Bole, Lartey (via *Agnes Naa Momo Lartey*), and *Joseph Bukari Nikpe*;
* is a *curated subset* of the committed datasets (``datasets/*.json``) rather
  than the full several-thousand-entity set, so the harness stays fast while
  still exercising the phonetic/fuzzy/component/deterministic paths across
  person, location, constituency, and party kinds.

The snapshot is built once at import and shared (it is immutable), and the same
subset is also exposed as the Sitting_Scope member set for the sitting-scope
path. No network request and no AWS call is made to build it (Req 10.8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from app.correction.lexicon import DEFAULT_LEXICON_PATH, EnglishLexicon, load_lexicon
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType

# The fixed dataset version string — constant so the snapshot (and anything
# derived from it) is byte-for-byte identical on every run (Req 15.2 spirit).
FIXTURE_DATASET_VERSION = "evaluation-fixture-2025-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Curated entity records
# ---------------------------------------------------------------------------
#
# Drawn verbatim from the committed datasets (datasets/mps.json,
# datasets/locations.json, datasets/parties.json, datasets/persons.json). The
# subset is chosen to cover:
#   * the recorded false-positive collision targets — Ghana, Sege, Bole, La,
#     Agnes Naa Momo Lartey, Joseph Bukari Nikpe, Mary Grant, Alex Segbefia,
#     Ato Forson, Kyei-Mensah-Bonsu;
#   * persons with rich alias sets (for phonetic/fuzzy recall);
#   * constituencies and regions (non-person entities, never context-gated);
#   * parties (deterministic abbreviation matching).
#
# source_rank / source mirror the production load order so build_index applies
# the same first-wins/last-wins write-order policy.


def _records() -> list[EntityRecord]:
    """Return the curated Entity_Records for the fixture snapshot.

    Ordered the way ``store.load_active_records`` returns them
    (source_rank, entity_kind, canonical) so ``build_index`` reproduces the
    production write-order policy (Req 3.12 parity).
    """
    records: list[EntityRecord] = [
        # --- Locations: regions ---
        EntityRecord(
            canonical="Greater Accra",
            entity_kind=EntityKind.location,
            entity_type=EntityType.region,
            aliases=[],
            source="regions",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Northern",
            entity_kind=EntityKind.location,
            entity_type=EntityType.region,
            aliases=[],
            source="regions",
            source_rank=0,
        ),
        # --- Locations: cities (collision targets for the block list) ---
        EntityRecord(
            canonical="Sege",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Bole",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
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
            canonical="Kpone",
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
            aliases=[],
            source="cities",
            source_rank=1,
        ),
        EntityRecord(
            canonical="Tamale",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
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
        EntityRecord(
            canonical="Agona",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=[],
            source="cities",
            source_rank=1,
        ),
        # --- Locations: supplementary (aliases; phonetic-indexed) ---
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
            aliases=[
                "Ningo Prampram",
                "Ningoprampram",
                "Nyungoprampram",
                "Ningo Pram Pram",
            ],
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
            aliases=[
                "Rawlings",
                "J.J. Rawlings",
                "JJ Rawlings",
                "Jerry Rawlings",
            ],
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
        # --- Persons: ministers (block-list collision targets) ---
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
            canonical="Ato Austin",
            entity_kind=EntityKind.person,
            entity_type=EntityType.minister,
            aliases=["Austin", "Ato Austin"],
            source="ministers",
            source_rank=4,
        ),
        # --- Persons: MPs (surname / component matching) ---
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
        # --- Parties ---
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
# NB: the Evaluation_Corpus (Req 11.1, 11.2) evaluates the former Block_List
# tokens with the Block_List *empty* to prove gating handles them WITHOUT the
# list. So the snapshot the harness runs against carries an EMPTY block_list.
# The stopword/title/word-stopword sets are the small production-shaped sets
# the engine needs to behave normally; they are held constant so Baseline and
# gated runs see the same non-approximate rejection decisions (Req 11.5).

# Stopwords rejected before any strategy runs (mirrors the production kinds,
# kept intentionally small). These are NOT the former block-list words — those
# are exercised as Negative_Set fixtures with the block list empty.
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
def build_fixture_snapshot(block_list: frozenset[str] = frozenset()) -> DatasetSnapshot:
    """Build the fixed evaluation DatasetSnapshot (cached; immutable).

    The snapshot is built through the production :func:`build_index` so the
    derived gate indexes are identical in shape to production. By default the
    ``block_list`` is empty (Req 11.2 — the former Block_List tokens must be
    handled by gating without the list). Callers that want to exercise the
    retained Block_List escape hatch (Req 11.4) may pass a non-empty set.

    Because it is ``lru_cache``-d and every input is immutable, two calls with
    the same ``block_list`` return the same object — supporting the determinism
    the harness requires.
    """
    records = _records()
    index = build_index(records)
    return DatasetSnapshot(
        version=FIXTURE_DATASET_VERSION,
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime(2025, 1, 1, tzinfo=UTC),
        index=index,
        block_list=block_list,
        stopwords=FIXTURE_STOPWORDS,
        word_stopwords=FIXTURE_WORD_STOPWORDS,
        title_prefixes=FIXTURE_TITLE_PREFIXES,
    )


def fixture_canonicals() -> frozenset[str]:
    """Return the set of canonical entity names present in the fixture snapshot.

    Used to assert that every Positive_Set label resolves to a canonical entity
    present in the snapshot (Req 10.3) and as a Sitting_Scope member set.
    """
    return frozenset(r.canonical for r in _records())


def fixture_alias_set() -> frozenset[str]:
    """Return the lowercased alias/canonical key set of the fixture snapshot.

    Mirrors ``index.canonical_map`` keys — the Dataset_Cache alias set the
    Lexicon_Gate checks membership against (Req 2.5, 2.8).
    """
    return frozenset(build_fixture_snapshot().index.canonical_map.keys())


@lru_cache(maxsize=1)
def load_fixture_lexicon() -> EnglishLexicon:
    """Load the bundled English_Lexicon once for the harness (Req 2.1-2.3, 10.8).

    Reuses the production loader against the committed artifact, so no network
    access occurs. Cached so repeated harness loads share one instance.
    """
    return load_lexicon(DEFAULT_LEXICON_PATH)
