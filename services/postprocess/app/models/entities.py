"""Entity domain models and enumerations.

Defines the core enumerations for entity classification and the Pydantic
models representing stored entities and applied corrections.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EntityKind(StrEnum):
    """Top-level entity classification."""

    location = "location"
    person = "person"
    party = "party"


class EntityType(StrEnum):
    """Finer classification of an Entity_Record."""

    region = "region"
    city = "city"
    constituency = "constituency"
    district = "district"
    president = "president"
    minister = "minister"
    mp = "mp"
    party = "party"
    supplementary = "supplementary"


class MatchStrategy(StrEnum):
    """Named method that produced a Correction_Record."""

    exact = "exact"
    fused = "fused"
    joined = "joined"
    initials = "initials"
    title_person = "title_person"
    phonetic = "phonetic"
    fuzzy = "fuzzy"
    substring = "substring"


class EntityRecord(BaseModel):
    """One stored entity from the Dataset_Store.

    Represents a canonical entity with its aliases and optional attributes
    such as region, constituency, party, or role.
    """

    canonical: str
    entity_kind: EntityKind
    entity_type: EntityType
    aliases: list[str] = Field(default_factory=list)
    region: str | None = None
    constituency: str | None = None
    party: str | None = None
    role: str | None = None
    active: bool = True
    source: str | None = None
    source_rank: int = 0


class CorrectionRecord(BaseModel):
    """One applied correction produced by the Correction_Engine.

    Contains the original text, the corrected text, the match strategy used,
    the confidence score, and the entity classification.
    """

    original: str
    corrected: str
    strategy: MatchStrategy
    confidence: float = Field(ge=0.0, le=1.0)
    entity_kind: EntityKind
    entity_type: EntityType
