"""Response models for the Correction_Response contract.

Defines the outbound schema: CorrectedWord (with extra="allow" for provider
field passthrough and camelCase flag aliases), EntitySummary, Metadata, and
CorrectionResponse.

Key serialization rule: use ``by_alias=True, exclude_none=True`` so false
flags (locationCorrected, bedrockCorrected, yearCorrected) and zero-valued
counters (location_corrections, year_corrections, bedrock_corrections) are
OMITTED from the JSON output — matching the existing server.js behaviour
where these fields are only set when truthy.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import CorrectionRecord


class CorrectedWord(BaseModel):
    """One word in the corrected transcript output.

    Uses ``extra="allow"`` so unrecognized provider fields (Deepgram's
    ``punctuated_word``, ``speaker``, etc.) ride through untouched (R2.6).

    Flag fields use camelCase aliases and are only present when True —
    ``exclude_none`` in serialization ensures False/None flags are omitted,
    matching the existing server.js behaviour.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    word: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None

    # Flag fields — only present when True
    location_corrected: bool | None = Field(
        default=None, alias="locationCorrected"
    )
    bedrock_corrected: bool | None = Field(
        default=None, alias="bedrockCorrected"
    )
    year_corrected: bool | None = Field(
        default=None, alias="yearCorrected"
    )
    entity_kind: str | None = Field(default=None, alias="entityKind")
    entity_type: str | None = Field(default=None, alias="entityType")


class EntitySummary(BaseModel):
    """One entity in the response entities array."""

    name: str
    kind: str
    type: str
    mentions: int


class Metadata(BaseModel):
    """Pipeline metadata in the correction response.

    Zero-valued counters are omitted (serialized with ``exclude_none=True``)
    matching the existing behaviour where ``server.js`` only sets counters
    when they are greater than zero.
    """

    model_config = ConfigDict(populate_by_name=True)

    location_corrections: int | None = None
    year_corrections: int | None = None
    bedrock_corrections: int | None = None
    llm_status: str | None = None
    postprocessing_status: str | None = None
    rule_latency_ms: int | None = None
    llm_latency_ms: int | None = None
    dataset_version: str | None = None
    correlation_id: str | None = Field(default=None, alias="correlationId")


class CorrectionResponse(BaseModel):
    """Top-level outbound response body for POST /v1/postprocess.

    Serialize with ``by_alias=True, exclude_none=True`` to match the
    existing client contract where false flags and zero counters are
    absent from the payload.
    """

    transcript: str
    words: list[CorrectedWord] = Field(default_factory=list)
    entities: list[EntitySummary] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=Metadata)
    corrections: list[CorrectionRecord] = Field(default_factory=list)
