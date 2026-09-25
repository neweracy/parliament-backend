"""Correction evidence domain models.

Defines the persisted, per-transcript-version audit record of what the
correction pipeline changed: :class:`CorrectionEntry` (one Postprocessing_Change
addressable back to the Source_Words it changed) and :class:`CorrectionEvidence`
(the per-version container).

These models are additive and follow the same serialization contract as the
existing response models: ``ConfigDict(populate_by_name=True)`` with
``by_alias=True, exclude_none=True`` so optional/unsupplied fields are OMITTED
from the JSON output. A correction response for an input that produces no
evidence is therefore byte-identical to the Baseline response where the new
fields are unset (Req 10.3).

The closed-set enumerations (:class:`CorrectionStage`, :class:`CorrectionOutcome`,
:class:`MappingConfidence`) mirror the ``EntityKind``/``MatchStrategy`` style in
``entities.py``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CorrectionStage(StrEnum):
    """Pipeline stage that produced a Correction_Entry (closed set).

    ``rule`` for a Correction_Engine correction, ``year`` for a Year_Corrector
    correction, and ``llm`` for an LLM_Refiner correction (Req 1.7).
    """

    rule = "rule"
    year = "year"
    llm = "llm"


class CorrectionOutcome(StrEnum):
    """Disposition of a Correction_Entry (closed set).

    ``applied`` for a correction present in the returned transcript, ``vetoed``
    for a correction the LLM_Refiner restored to its original Source_Span text
    (Req 1.8).
    """

    applied = "applied"
    vetoed = "vetoed"


class MappingConfidence(StrEnum):
    """Confidence that an entry resolves to an exact display range (closed set).

    ``exact`` when the addressed Source_Words resolve to a contiguous range;
    ``lost`` when alignment diverged so no Source_Word resolves (Req 2.10).
    """

    exact = "exact"
    lost = "lost"


class CorrectionEntry(BaseModel):
    """One Postprocessing_Change addressable back to the Source_Words it changed.

    Carries stable source addressing (``source_span_id``, ``source_word_ids``),
    immutable ASR timing (``source_start``, ``source_end``), the original and
    corrected text, the stage of origin, the outcome, the gated confidence and
    the acceptance threshold it was compared against, optional entity
    classification, and optional provenance.

    Serialize with ``by_alias=True, exclude_none=True`` so unsupplied optional
    fields (provenance, entity classification, ``mapping_confidence``) are
    omitted rather than emitted as placeholders (Req 1.5, 10.3).
    """

    model_config = ConfigDict(populate_by_name=True)

    source_span_id: str = Field(alias="sourceSpanId")
    source_word_ids: list[str] = Field(
        default_factory=list, alias="sourceWordIds"
    )
    original: str
    corrected: str
    source_start: float | None = Field(default=None, alias="sourceStart")
    source_end: float | None = Field(default=None, alias="sourceEnd")
    correction_stage: CorrectionStage = Field(alias="correctionStage")
    correction_outcome: CorrectionOutcome = Field(alias="correctionOutcome")
    confidence: float = Field(ge=0.0, le=1.0)
    threshold: float | None = None

    # Entity classification — omitted when the correction is not an entity.
    entity_kind: str | None = Field(default=None, alias="entityKind")
    entity_type: str | None = Field(default=None, alias="entityType")

    # Provenance — each omitted when the producing stage supplied no value.
    correlation_id: str | None = Field(default=None, alias="correlationId")
    dataset_version: str | None = Field(default=None, alias="datasetVersion")
    model_id: str | None = Field(default=None, alias="modelId")

    # Present only when the builder already knows the source alignment was lost;
    # otherwise resolved client-side against the displayed text.
    mapping_confidence: MappingConfidence | None = Field(
        default=None, alias="mappingConfidence"
    )


class CorrectionEvidence(BaseModel):
    """The per-version container of Correction_Entries for one transcript.

    Keyed to a transcript identifier and Transcript_Version; ``entries`` holds
    one :class:`CorrectionEntry` per Postprocessing_Change (Req 1.1, 1.6).
    """

    model_config = ConfigDict(populate_by_name=True)

    transcript_id: int = Field(alias="transcriptId")
    version: int
    entries: list[CorrectionEntry] = Field(default_factory=list)
