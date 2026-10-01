"""Pytest fixtures exposing the shared property-test Dataset_Cache and lexicon.

Every property test in this package (Properties 1-15, tasks 1.x-6.2) can request
these fixtures to obtain the SAME fixed in-memory Dataset_Cache snapshot, its
Match_Index, the bundled English_Lexicon, and the fixture's alias/canonical
helper sets — satisfying Req 16.15's "derive every property test input from a
fixed in-memory Dataset_Cache fixture" requirement.

The underlying data lives in :mod:`tests.property.fixtures`; the hypothesis input
strategies live in :mod:`tests.property.strategies`. These fixtures are
``session``-scoped because the snapshot is immutable and shared.
"""

from __future__ import annotations

import pytest

from app.correction.lexicon import EnglishLexicon
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import MatchIndex
from tests.property.fixtures import (
    build_property_snapshot,
    load_property_lexicon,
    property_alias_set,
    property_canonicals,
)


@pytest.fixture(scope="session")
def property_snapshot() -> DatasetSnapshot:
    """The fixed in-memory Dataset_Cache snapshot shared by every property test."""
    return build_property_snapshot()


@pytest.fixture(scope="session")
def property_index(property_snapshot: DatasetSnapshot) -> MatchIndex:
    """The Match_Index derived from the shared fixture snapshot."""
    return property_snapshot.index


@pytest.fixture(scope="session")
def property_lexicon() -> EnglishLexicon:
    """The bundled English_Lexicon loaded once for the property tests."""
    return load_property_lexicon()


@pytest.fixture(scope="session")
def fixture_canonical_names() -> frozenset[str]:
    """The set of canonical entity names present in the fixture snapshot."""
    return property_canonicals()


@pytest.fixture(scope="session")
def fixture_alias_keys() -> frozenset[str]:
    """The lowercased alias/canonical key set of the fixture snapshot."""
    return property_alias_set()
