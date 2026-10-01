"""Request models for the Correction_Request contract.

Defines the inbound schema: Word (with extra="allow" for provider field
passthrough and confidence clamping), CorrectionOptions (camelCase aliases),
and CorrectionRequest (accepts an empty words list per R1.7).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Word(BaseModel):
    """One transcript unit from the ASR provider.

    Uses ``extra="allow"`` so unrecognized provider fields (Deepgram's
    ``punctuated_word``, ``speaker``, etc.) ride through untouched (R2.6).

    Confidence is clamped to [0.0, 1.0] rather than rejected — one
    malformed value in a 5000-word transcript must not produce a 422 that
    silently loses every correction for an otherwise valid transcription.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    word: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: object) -> float | None:
        """Out-of-range confidence is clamped, never rejected."""
        if v is None or not isinstance(v, (int, float)):
            return None
        return min(1.0, max(0.0, float(v)))


class CorrectionOptions(BaseModel):
    """Pipeline options with camelCase aliases for JSON compatibility."""

    model_config = ConfigDict(populate_by_name=True)

    llm_refine: bool = Field(default=True, alias="llmRefine")
    min_confidence: float = Field(
        default=0.75, ge=0.0, le=1.0, alias="minConfidence"
    )
    word_accept_threshold: float = Field(
        default=0.90, ge=0.0, le=1.0, alias="wordAcceptThreshold"
    )
    provider: Literal["deepgram", "khaya", "hybrid"] = "deepgram"


class CorrectionRequest(BaseModel):
    """Top-level inbound request body for POST /v1/postprocess.

    An empty ``words`` list is valid (R1.7) — the service still corrects
    the transcript text and returns it with an empty word list.
    """

    model_config = ConfigDict(populate_by_name=True)

    transcript: str
    words: list[Word] = Field(default_factory=list)
    options: CorrectionOptions = Field(default_factory=CorrectionOptions)
    correlation_id: str | None = Field(default=None, alias="correlationId")
    # Optional Sitting_Scope identifier (Req 8.1). Exposed as ``sittingId`` in
    # JSON and ``sitting_id`` in Python, a string of 1 to 128 characters,
    # defaulting to absent when the field is omitted or null. Additive and
    # backward-compatible: a request body that omits it behaves exactly as
    # before (Req 14.10). When present it selects the parliamentary sitting
    # whose expected member set scopes person-candidate preference (Req 8.2-8.10);
    # a whitespace-only value is treated as absent by the pipeline (Req 8.2).
    sitting_id: str | None = Field(
        default=None, alias="sittingId", min_length=1, max_length=128
    )
