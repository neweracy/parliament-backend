"""Pipeline stage orchestrator — Correction_Engine → Year_Corrector → LLM_Refiner gate.

Provides the async entry point ``run_pipeline`` that executes the three stages
in their fixed contractual order, builds the deduplicated Entity_Summary, and
assembles the metadata counters.

Concurrency model:
  The Correction_Engine and Year_Corrector are CPU-bound synchronous Python.
  They run via ``await asyncio.to_thread(run_rule_stages, ...)`` so the event
  loop stays free for /health probes and concurrent requests.

Requirements: 6.6, 2.1, 2.3, 2.4, 10.7, 10.8
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from app.config import Settings
from app.correction.engine import (
    TextCorrectionResult,
    WordCorrectionResult,
    correct_text,
    correct_words,
)
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.llm.bedrock import BedrockClient
from app.llm.refiner import refine_chunks
from app.models.entities import CorrectionRecord, EntityKind, EntityType, MatchStrategy
from app.models.request import CorrectionRequest
from app.models.response import (
    CorrectedWord,
    CorrectionResponse,
    EntitySummary,
    Metadata,
)
from app.years.corrector import correct_years, correct_years_in_text


# ---------------------------------------------------------------------------
# Rule stages — synchronous, run in a thread
# ---------------------------------------------------------------------------


def _run_rule_stages(
    request: CorrectionRequest,
    snapshot: DatasetSnapshot,
) -> tuple[TextCorrectionResult, WordCorrectionResult, str, list[dict], int]:
    """Execute the Correction_Engine and Year_Corrector synchronously.

    Returns:
        (text_result, word_result, final_text, final_words, year_count)
    """
    index = snapshot.index

    # --- Stage 1: Correction_Engine ---
    # correct_text operates on the transcript string
    text_result = correct_text(
        request.transcript,
        index,
        snapshot,
        min_confidence=request.options.min_confidence,
    )

    # correct_words operates on the word list
    # Serialize words to dicts preserving extra fields (by_alias for camelCase)
    word_dicts = [
        w.model_dump(by_alias=True, exclude_none=True) for w in request.words
    ]

    word_result = correct_words(
        word_dicts,
        index,
        snapshot,
        word_accept_threshold=request.options.word_accept_threshold,
        min_confidence=request.options.min_confidence,
    )

    # --- Stage 2: Year_Corrector ---
    # Apply year correction to the text (after entity correction)
    corrected_text, text_year_count = correct_years_in_text(text_result.text)

    # Apply year correction to the words
    corrected_words, word_year_count = correct_years(word_result.words)

    year_count = max(text_year_count, word_year_count)

    return (text_result, word_result, corrected_text, corrected_words, year_count)


# ---------------------------------------------------------------------------
# Entity_Summary builder
# ---------------------------------------------------------------------------


def _build_entity_summary(
    text_entities: list[tuple[str, str, str]],
    word_entities: list[tuple[str, str, str]],
) -> list[EntitySummary]:
    """Build a deduplicated Entity_Summary with mention counts.

    Combines entities found from both correct_text and correct_words,
    deduplicates by (name, kind, type), and counts total mentions.
    """
    # Combine all entity references
    all_entities = text_entities + word_entities

    # Count mentions by (canonical_name, kind, type)
    mention_counter: Counter[tuple[str, str, str]] = Counter()
    for canonical, kind, etype in all_entities:
        mention_counter[(canonical, kind, etype)] += 1

    # Build the summary list (deduplicated, ordered by first appearance)
    seen: set[tuple[str, str, str]] = set()
    summaries: list[EntitySummary] = []

    for canonical, kind, etype in all_entities:
        key = (canonical, kind, etype)
        if key in seen:
            continue
        seen.add(key)
        summaries.append(
            EntitySummary(
                name=canonical,
                kind=kind,
                type=etype,
                mentions=mention_counter[key],
            )
        )

    return summaries


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    request: CorrectionRequest,
    cache: DatasetCache,
    *,
    bedrock_client: BedrockClient | None = None,
    settings: Settings | None = None,
) -> CorrectionResponse:
    """Execute the full correction pipeline and return a CorrectionResponse.

    Stages (fixed order, contractual):
      1. Correction_Engine (correct_text + correct_words)
      2. Year_Corrector (correct_years + correct_years_in_text)
      3. LLM_Refiner gate (invokes refine_chunks when configured)

    The rule stages run in a worker thread via asyncio.to_thread so the
    event loop stays free for /health probes and concurrent requests.

    Parameters
    ----------
    request : CorrectionRequest
        The inbound correction request.
    cache : DatasetCache
        The dataset cache holding the current snapshot.
    bedrock_client : BedrockClient | None
        The Bedrock client (constructed once at startup). None when LLM
        is not available.
    settings : Settings | None
        Application settings. None when called without app context (tests).

    Returns
    -------
    CorrectionResponse
        The fully assembled response with corrected text, words, entities,
        metadata, and corrections.
    """
    total_start = time.perf_counter()

    snapshot = cache.get_snapshot()
    if snapshot is None:
        # Should not reach here — health check gates requests.
        # Return a passthrough response with error status.
        return CorrectionResponse(
            transcript=request.transcript,
            words=[
                CorrectedWord(**w.model_dump(by_alias=True, exclude_none=True))
                for w in request.words
            ],
            entities=[],
            metadata=Metadata(
                llm_status="unconfigured",
                postprocessing_status="skipped",
                correlation_id=request.correlation_id,
            ),
            corrections=[],
        )

    # --- Run rule stages in a thread ---
    rule_start = time.perf_counter()

    text_result, word_result, final_text, final_words, year_count = (
        await asyncio.to_thread(_run_rule_stages, request, snapshot)
    )

    rule_end = time.perf_counter()
    rule_latency_ms = int((rule_end - rule_start) * 1000)

    # --- Stage 3: LLM_Refiner gate ---
    llm_start = time.perf_counter()

    llm_status: str
    bedrock_corrections = 0

    # Decision order (contractual):
    # 1. llm_refine false → skipped
    # 2. LLM_ENABLED false → skipped
    # 3. client None / not configured / empty model_id → unconfigured
    # 4. otherwise → invoke refine_chunks
    if not request.options.llm_refine:
        llm_status = "skipped"
    elif settings is not None and not settings.llm_enabled:
        llm_status = "skipped"
    elif (
        bedrock_client is None
        or not bedrock_client.is_configured
        or (settings is not None and not (settings.bedrock_model_id or "").strip())
    ):
        llm_status = "unconfigured"
    else:
        try:
            _, internal_status, count = await refine_chunks(
                final_words,
                snapshot,
                bedrock_client,
                None,  # session — retrieval falls back to snapshot
                settings,
            )
            # Map internal statuses to external
            if internal_status == "ok":
                llm_status = "applied"
            else:
                # partial, failed → degraded
                llm_status = "degraded"
            bedrock_corrections = count
        except Exception:
            llm_status = "degraded"

    llm_end = time.perf_counter()
    llm_latency_ms = int((llm_end - llm_start) * 1000)

    # --- Build Entity_Summary ---
    entity_summary = _build_entity_summary(
        text_result.entities_found,
        word_result.entities_found,
    )

    # --- Build corrected words for response ---
    response_words = [
        CorrectedWord(**wd) for wd in final_words
    ]

    # --- Assemble metadata (omit zero-valued counters) ---
    location_corrections_count = len(text_result.corrections) + len(word_result.corrections)

    metadata = Metadata(
        location_corrections=location_corrections_count if location_corrections_count > 0 else None,
        year_corrections=year_count if year_count > 0 else None,
        bedrock_corrections=bedrock_corrections if bedrock_corrections > 0 else None,
        llm_status=llm_status,
        postprocessing_status="applied",
        rule_latency_ms=rule_latency_ms,
        llm_latency_ms=llm_latency_ms,
        dataset_version=snapshot.version,
        correlation_id=request.correlation_id,
    )

    # --- Build corrections list ---
    corrections: list[CorrectionRecord] = []

    # From text corrections
    for tc in text_result.corrections:
        corrections.append(
            CorrectionRecord(
                original=tc.original,
                corrected=tc.replacement,
                strategy=MatchStrategy(tc.strategy) if tc.strategy in MatchStrategy.__members__ else MatchStrategy.exact,
                confidence=tc.confidence,
                entity_kind=EntityKind(tc.entity_kind) if tc.entity_kind in EntityKind.__members__ else EntityKind.location,
                entity_type=EntityType(tc.entity_type) if tc.entity_type in EntityType.__members__ else EntityType.supplementary,
            )
        )

    # From word corrections
    for original, corrected, strategy, confidence, kind, etype in word_result.corrections:
        corrections.append(
            CorrectionRecord(
                original=original,
                corrected=corrected,
                strategy=MatchStrategy(strategy) if strategy in MatchStrategy.__members__ else MatchStrategy.exact,
                confidence=confidence,
                entity_kind=EntityKind(kind) if kind in EntityKind.__members__ else EntityKind.location,
                entity_type=EntityType(etype) if etype in EntityType.__members__ else EntityType.supplementary,
            )
        )

    # --- Total latency (recorded but not exposed in metadata yet) ---
    _total_latency_ms = int((time.perf_counter() - total_start) * 1000)

    return CorrectionResponse(
        transcript=final_text,
        words=response_words,
        entities=entity_summary,
        metadata=metadata,
        corrections=corrections,
    )
