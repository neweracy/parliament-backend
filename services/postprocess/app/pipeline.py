"""Pipeline stage orchestrator — Correction_Engine → Year_Corrector → LLM_Refiner gate.

Provides the async entry point ``run_pipeline`` that executes the three stages
in their fixed contractual order, builds the deduplicated Entity_Summary, and
assembles the metadata counters.

Concurrency model:
  The Correction_Engine and Year_Corrector are CPU-bound synchronous Python.
  They run via ``await asyncio.to_thread(run_rule_stages, ...)`` so the event
  loop stays free for /health probes and concurrent requests.

Requirements: 6.6, 2.1, 2.3, 2.4, 10.7, 10.8, 13.9, 17.3, 17.5
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from datetime import datetime, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, provider_profiles
from app.correction.engine import (
    TextCorrectionResult,
    WordCorrectionResult,
    correct_text,
    correct_words,
)
from app.correction.gates import GateContext, GateTally
from app.correction.source_ids import assign_source_word_ids
from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.sittings import (
    DEFAULT_SITTING_SCOPE_TIMEOUT_S,
    resolve_sitting_scope,
)
from app.history.writer import CorrectionHistoryWriter, HistoryRecord
from app.llm.bedrock import BedrockClient
from app.llm.refiner import chunk_words, refine_chunks
from app.llm.veto import (
    ResolvedVeto,
    RuleCorrection,
    VetoOutcome,
    apply_vetoes,
    assign_corrections_to_chunks,
    resolve_vetoes,
)
from app.models.entities import CorrectionRecord, EntityKind, EntityType, MatchStrategy
from app.models.request import CorrectionRequest
from app.models.response import (
    CorrectedWord,
    CorrectionResponse,
    EntitySummary,
    Metadata,
)
from app.obs.metrics import (
    emit_approx_spans_evaluated,
    emit_corrections_applied,
    emit_gate_rejection_log,
    emit_gate_rejections,
    emit_handler_latency,
    emit_llm_latency,
    emit_rule_latency,
)
from app.years.corrector import correct_years, correct_years_in_text

logger = structlog.get_logger("pipeline")


# ---------------------------------------------------------------------------
# Sitting_Scope resolution (task 4.5, Req 8)
# ---------------------------------------------------------------------------


async def _resolve_sitting_scope(
    request: CorrectionRequest,
    settings: Settings | None,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> frozenset[str] | None:
    """Resolve the Sitting_Scope member set once for the request (Req 8.5-8.7, 8.10).

    Returns the resolved ``frozenset`` of member canonical names when the
    Sitting_Scope flag is enabled, the request carries a usable sitting id, a
    database session is available, and the lookup returns a non-empty set within
    the 2-second bound. Returns ``None`` in every other case — the flag is off,
    the request omits the sitting id, the id is whitespace-only, no session
    factory is available, or the lookup is unavailable (no match, empty set,
    error, or timeout) — so the Rule_Stage applies no Evidence_Score adjustment
    (Req 8.2, 8.5). At most one ``sitting_scope.unavailable`` debug event is
    logged for the request when a lookup was attempted but yielded no usable
    scope (Req 8.5).

    Resolution happens here, in the async pipeline, exactly once and before the
    Rule_Stage worker thread is dispatched (Req 8.7); the resolved set is passed
    into the Rule_Stage in memory, so the Rule_Stage issues no DB read of its
    own (Req 8.8). Any lookup error or timeout is contained here so the pipeline
    completes and returns corrections without Sitting_Scope adjustment (Req 8.10).
    """
    # Flag off, or called without settings (unit tests) → no scope, no lookup
    # (Req 8.2). Baseline path (Req 12.9).
    if settings is None or not settings.sitting_scope_enabled:
        return None

    # Request omits the sitting id, or the id is whitespace-only → no adjustment,
    # no lookup, no log event (Req 8.2).
    sitting_id = request.sitting_id
    if sitting_id is None or not sitting_id.strip():
        return None

    # No database session available (tests, or DB down) → treat as unavailable
    # (Req 8.5): log one debug event and proceed with no adjustment.
    if session_factory is None:
        logger.debug("sitting_scope.unavailable", reason="no_session")
        return None

    scope: frozenset[str] = frozenset()
    try:
        session: AsyncSession = session_factory()
        try:
            scope = await resolve_sitting_scope(
                session,
                sitting_id.strip(),
                timeout_s=DEFAULT_SITTING_SCOPE_TIMEOUT_S,
            )
        finally:
            await session.close()
    except Exception:  # noqa: BLE001 - lookup errors are unavailable (Req 8.10)
        scope = frozenset()

    # Unavailable (no match / empty / error / timeout) → no adjustment, at most
    # one debug event (Req 8.5). A non-empty scope is returned for the engine.
    if not scope:
        logger.debug("sitting_scope.unavailable", reason="empty")
        return None
    return scope


# ---------------------------------------------------------------------------
# Rule stages — synchronous, run in a thread
# ---------------------------------------------------------------------------


def _run_rule_stages(
    request: CorrectionRequest,
    snapshot: DatasetSnapshot,
    settings: Settings | None = None,
    lexicon: object | None = None,
    sitting_scope: frozenset[str] | None = None,
) -> tuple[TextCorrectionResult, WordCorrectionResult, str, list[dict], int, GateTally]:
    """Execute the Correction_Engine and Year_Corrector synchronously.

    Returns:
        (text_result, word_result, final_text, final_words, year_count, tally)

    The :class:`~app.correction.gates.GateTally` collects the per-request gate
    decisions (task 6.1, Req 13): which gate rejected each Span from approximate
    matching (Req 13.1, 13.9) and how many Spans had an Approximate_Strategy
    evaluated (Req 13.4). It is created here and threaded into both engine
    functions so a Span rejected in either the transcript path or the word path
    is counted once. The tally is purely observational — it never changes the
    correction output — and on the Baseline path (inert context) no gate fires,
    so it stays empty.
    """
    index = snapshot.index

    # Resolve correction thresholds from settings (deployment-level) with
    # request.options providing per-request overrides for the two values
    # that appear on CorrectionOptions.
    min_confidence = request.options.min_confidence
    word_accept_threshold = request.options.word_accept_threshold
    fuzzy_score_cutoff: float = settings.fuzzy_score_cutoff if settings else 0.70
    min_candidate_length: int = (
        settings.min_candidate_length if settings else 4
    )

    # Construct the GateContext once at this pipeline boundary (task 1.4).
    # It resolves the gating config from settings and carries the per-request
    # gate inputs later tasks populate (Span_Confidence hook, English_Lexicon
    # handle, sitting-scope member set). With every new flag off the context is
    # inert and the engine path is unchanged (Req 12.9); when settings is
    # absent (unit tests calling the rule stages directly) an inert context is
    # used, preserving the Baseline path.
    # The English_Lexicon handle (task 2.3, loaded once per process on
    # app.state) is threaded onto the context here so the Lexicon_Gate (task
    # 2.4) can consult it (Req 2.5-2.8). A ``None`` lexicon, or one that failed
    # to load, leaves the gate inactive (Req 2.13). When settings is absent
    # (unit tests calling the rule stages directly) an inert context is used,
    # preserving the Baseline path.
    #
    # The request's ``provider`` (``CorrectionOptions.provider``, default
    # ``"deepgram"``) and the gate profile resolved for it are threaded onto the
    # context here (task 2.12.3) so the Confidence_Gate and Lexicon_Gate branch
    # on provider without any strategy signature widening. ``provider_profiles``
    # returns the ``deepgram`` profile for every provider when
    # ``provider_profiles_enabled`` is off (the default), so the flag-off path
    # resolves the task 1.1 thresholds for every request and stays
    # baseline-equivalent (Req 12.9). Resolving the dict here is cheap (a small
    # fixed number of frozen dataclasses); ``clamp_ranges`` has already run at
    # startup so the values are parsed and range-clamped.
    if settings is not None:
        profile = provider_profiles(settings).get(request.options.provider)
        # The Sitting_Scope member set (task 4.5, Req 8) is resolved once in
        # ``run_pipeline`` before this thread is dispatched (Req 8.7) and passed
        # in memory here, so the Rule_Stage issues no DB read of its own
        # (Req 8.8). ``None`` when the flag is off, the request omits/whitespaces
        # the id, or the lookup was unavailable (Req 8.2, 8.5), which leaves the
        # Sitting_Scope preference inert (Baseline, Req 12.9).
        gate_context = GateContext.from_settings(
            settings,
            lexicon=lexicon,
            provider=request.options.provider,
            profile=profile,
            sitting_scope=sitting_scope,
        )
    else:
        gate_context = GateContext.inert()

    # Per-request gate-decision collector (task 6.1, Req 13). Threaded into both
    # engine functions so a Span rejected in either path is attributed once, to
    # the first gate in evaluation order (Req 13.9), and Spans that reached
    # approximate evaluation are counted (Req 13.4). On the Baseline path the
    # context is inert, no gate fires, and the tally stays empty.
    tally = GateTally()

    # correct_words operates on the word list
    # Serialize words to dicts preserving extra fields (by_alias for camelCase)
    word_dicts = [
        w.model_dump(by_alias=True, exclude_none=True) for w in request.words
    ]

    # --- Stage 1: Correction_Engine ---
    # correct_text operates on the transcript string. The Words list is passed
    # so the Confidence_Gate can derive Span_Confidence by aligning Span tokens
    # to Words at matching sequence positions (Req 1.7); an empty list leaves
    # every transcript Span_Confidence Unknown (Req 1.10).
    text_result = correct_text(
        request.transcript,
        index,
        snapshot,
        min_confidence=min_confidence,
        fuzzy_score_cutoff=fuzzy_score_cutoff,
        min_candidate_length=min_candidate_length,
        gate_context=gate_context,
        words=word_dicts,
        tally=tally,
    )

    word_result = correct_words(
        word_dicts,
        index,
        snapshot,
        word_accept_threshold=word_accept_threshold,
        min_confidence=min_confidence,
        fuzzy_score_cutoff=fuzzy_score_cutoff,
        min_candidate_length=min_candidate_length,
        gate_context=gate_context,
        tally=tally,
    )

    # --- Stage 2: Year_Corrector ---
    # Apply year correction to the text (after entity correction)
    corrected_text, text_year_count = correct_years_in_text(text_result.text)

    # Apply year correction to the words
    corrected_words, word_year_count = correct_years(word_result.words)

    year_count = max(text_year_count, word_year_count)

    return (text_result, word_result, corrected_text, corrected_words, year_count, tally)


# ---------------------------------------------------------------------------
# Gate-decision observability (task 6.1, Req 13)
# ---------------------------------------------------------------------------


def _emit_gate_observability(tally: GateTally, provider: str | None = None) -> int:
    """Emit all gate-decision metrics and logs for the request; return the total.

    Emits, in order:

    * ``postprocess.gate_rejections`` — one datum per gate value with a count
      greater than zero (Req 13.1), bounded to the thirteen enumerated gate
      values with only the ``gate`` dimension (Req 13.2, 13.3);
    * ``postprocess.approx_spans_evaluated`` — the count of Spans that had an
      Approximate_Strategy evaluated, emitted on every request including zero
      (Req 13.4);
    * one ``gate.rejection`` debug log per attributed Span, carrying Span text,
      gate, Span_Confidence (Req 13.5), and the resolved ``provider`` (task
      2.12.4) so operators get provider visibility in the debug logs, suppressed
      at non-debug levels (Req 13.10).

    The ``provider`` (``CorrectionOptions.provider`` — deepgram/khaya/hybrid, a
    bounded non-PII value) rides ONLY on the debug log events, never on any
    metric: the gate-rejection metric keeps its closed dimension set of just
    ``gate`` (plus the ``service`` dimension every metric carries), so the
    metric dimensions are unchanged (Req 13.3).

    Each emit swallows its own failures (``_emit`` and ``emit_gate_rejection_log``
    never raise), so gate observability never surfaces to the caller (Req
    13.11). Returns the single total gate-rejection count across every gate
    value for the metadata counter (Req 13.6).
    """
    emit_gate_rejections(tally.counts())
    emit_approx_spans_evaluated(tally.approx_spans_evaluated())
    for decision in tally.decisions:
        emit_gate_rejection_log(
            decision.gate, decision.span_text, decision.span_confidence, provider
        )
    return tally.total_rejections()


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
    history_writer: CorrectionHistoryWriter | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    lexicon: object | None = None,
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
    history_writer : CorrectionHistoryWriter | None
        The correction history writer. None when history is disabled.
    session_factory : async_sessionmaker[AsyncSession] | None
        An async session factory for database access (pg_trgm retrieval).
        None when no database is available (tests); retrieval falls back
        to the Dataset_Cache canonical_map.
    lexicon : object | None
        The process-global English_Lexicon handle (``app.state.english_lexicon``,
        loaded once at startup per task 2.3). Threaded onto the GateContext so
        the Lexicon_Gate can consult it (Req 2.5-2.8). ``None`` (or a lexicon
        that failed to load) leaves the gate inactive (Req 2.13).

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

    # --- Assign stable Source_Word_Ids over the raw ASR word list ---
    # (transcript-evidence-navigation task 1.1, Req 1.2, 1.3, 2.7). This runs
    # immediately after ASR output is available and BEFORE any correction stage,
    # deriving one ``source_word_id`` (``w0``, ``w1``, ...) per raw Source_Word
    # from its zero-based ASR-order index. The ids ride alongside the pipeline
    # for the evidence builder (task 2.2) to address each correction back to the
    # Source_Words it changed; they are positional only, so they never read or
    # rewrite the ASR ``start``/``end`` of any word (Immutable_Source_Timing)
    # and never touch gating — the GateContext threading below is unchanged.
    source_word_ids = assign_source_word_ids(request.words)
    logger.debug("source_ids.assigned", count=len(source_word_ids))

    # --- Resolve the Sitting_Scope once, before dispatching the Rule_Stage ---
    # (Req 8.7). The member set is resolved here in the async pipeline, with a
    # 2-second timeout and defensive error handling (Req 8.5, 8.10), and passed
    # into the worker thread in memory so the Rule_Stage issues no DB read of
    # its own (Req 8.8). ``None`` when the flag is off, the request omits or
    # whitespaces the sitting id, or the lookup is unavailable — all of which
    # leave the Sitting_Scope preference inert (Req 8.2, 8.5, 12.9).
    sitting_scope = await _resolve_sitting_scope(
        request, settings, session_factory
    )

    # --- Run rule stages in a thread ---
    rule_start = time.perf_counter()

    text_result, word_result, final_text, final_words, year_count, tally = (
        await asyncio.to_thread(
            _run_rule_stages, request, snapshot, settings, lexicon, sitting_scope
        )
    )

    rule_end = time.perf_counter()
    rule_latency_ms = int((rule_end - rule_start) * 1000)

    # Emit rule latency metric (Req 10.8, 13.4)
    emit_rule_latency(rule_latency_ms)

    # --- Gate-decision observability (task 6.1, Req 13) ---
    # Emit the per-gate rejection counts (Req 13.1, 13.2, 13.3), the
    # approx-spans-evaluated count on every request including zero (Req 13.4),
    # and one debug-level per-Span rejection log carrying Span text, gate,
    # Span_Confidence, and the request's provider (task 2.12.4) so operators get
    # provider visibility in the debug logs without widening the closed gate
    # metric dimension set (Req 13.3, 13.5, 13.10). Every emit swallows its own
    # failures, so gate observability never surfaces to the caller (Req 13.11).
    gate_total = _emit_gate_observability(tally, request.options.provider)

    # --- Build corrections list (before Stage 3 so the LLM Veto can prune it) ---
    # Text corrections first, then word corrections — the order the Baseline
    # emits (Req 14.2). The word-correction entries are index-aligned to
    # ``word_result.veto_spans`` (both come from ``correct_words`` in emission
    # order), so a vetoed word correction maps to exactly one entry (Req 9.3).
    corrections, text_correction_count = _build_corrections(text_result, word_result)

    # --- Stage 3: LLM_Refiner gate (+ LLM Veto, Req 9) ---
    llm_start = time.perf_counter()

    llm_status: str
    bedrock_corrections = 0
    veto_outcome: VetoOutcome | None = None

    # Whether the LLM Veto is active for this request (Req 9.1): the flag is on
    # and refinement will actually run. When off, ``veto_corrections_by_chunk``
    # stays ``None`` so the refiner prompt and behaviour are Baseline (Req 12.9).
    veto_enabled = bool(settings is not None and settings.llm_veto_enabled)

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
            # Build the per-chunk rule corrections supplied to the refiner
            # (Req 9.1) using the same chunking the refiner applies, so the
            # veto results come back aligned to these chunks. Only built when
            # the veto is active; ``None`` otherwise keeps the Baseline path.
            rule_corrections: list[RuleCorrection] = []
            veto_corrections_by_chunk: list[list[tuple[str, str]]] | None = None
            if veto_enabled:
                rule_corrections = _build_rule_corrections(
                    corrections, word_result, text_correction_count
                )
                chunk_texts = [
                    c.text for c in chunk_words(final_words, settings.llm_chunk_size)
                ]
                per_chunk = assign_corrections_to_chunks(rule_corrections, chunk_texts)
                veto_corrections_by_chunk = [
                    [(rc.original, rc.corrected) for rc in chunk_list]
                    for chunk_list in per_chunk
                ]

            # Acquire a database session for pg_trgm retrieval if available
            session: AsyncSession | None = None
            try:
                if session_factory is not None:
                    session = session_factory()

                _, internal_status, count, veto_results = await refine_chunks(
                    final_words,
                    snapshot,
                    bedrock_client,
                    session,
                    settings,
                    veto_corrections_by_chunk=veto_corrections_by_chunk,
                )
            finally:
                if session is not None:
                    await session.close()

            # Map internal statuses to external
            if internal_status == "ok":
                llm_status = "applied"
            else:
                # partial, failed → degraded
                llm_status = "degraded"
            bedrock_corrections = count

            # --- Apply the LLM Veto restorations (Req 9.2-9.4, 9.10, 9.11) ---
            if veto_enabled and veto_results:
                per_chunk = assign_corrections_to_chunks(
                    rule_corrections,
                    [c.text for c in chunk_words(final_words, settings.llm_chunk_size)],
                )
                resolved: list[ResolvedVeto] = []
                for vr in veto_results:
                    if vr.chunk_index < len(per_chunk):
                        resolved.extend(
                            resolve_vetoes(vr.decisions, per_chunk[vr.chunk_index])
                        )
                if resolved:
                    veto_outcome = apply_vetoes(
                        final_text, final_words, corrections, resolved
                    )
                    final_text = veto_outcome.final_text
                    final_words = veto_outcome.final_words
                    corrections = veto_outcome.corrections
        except Exception:
            llm_status = "degraded"

    llm_end = time.perf_counter()
    llm_latency_ms = int((llm_end - llm_start) * 1000)

    # Emit LLM latency metric (Req 10.8, 13.4)
    emit_llm_latency(llm_latency_ms, llm_status)

    veto_count = veto_outcome.veto_count if veto_outcome is not None else 0

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
    # After the LLM Veto prunes restored Spans, the location-correction count is
    # the number of surviving correction entries so it stays consistent with the
    # returned corrections list.
    location_corrections_count = len(corrections)

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
        # Single total gate-rejection count across every gate value, omitted
        # when 0 so the Baseline contract snapshot is unchanged (Req 13.6,
        # 14.4/14.11 additive/zero-omitted).
        gate_rejections=gate_total if gate_total > 0 else None,
        # Count of LLM_Refiner vetoes — corrections entries removed for restored
        # Spans (Req 9.7). Omitted when 0 so a non-veto request is byte-identical
        # to the Baseline response (Req 14.4).
        vetoes=veto_count if veto_count > 0 else None,
    )

    # --- Total latency (recorded but not exposed in metadata yet) ---
    _total_latency_ms = int((time.perf_counter() - total_start) * 1000)

    # Emit handler latency metric (Req 10.8, 13.4)
    emit_handler_latency(_total_latency_ms)

    # Emit corrections applied per Match_Strategy (Req 13.3)
    strategy_counts: Counter[str] = Counter()
    for cr in corrections:
        strategy_counts[cr.strategy.value] += 1
    for strategy_name, count in strategy_counts.items():
        emit_corrections_applied(strategy_name, count)

    # --- Enqueue corrections into history writer (Req 13.9, 17.3) ---
    # Surviving (applied) corrections carry outcome='applied' (the default);
    # each restored Span enqueues one record with outcome='vetoed' (Req 9.5).
    # A dropped/failed history enqueue never affects the restored Span or the
    # returned veto count — enqueue is fire-and-forget and the response is
    # already assembled from ``corrections``/``final_words`` (Req 9.12).
    if history_writer is not None:
        text_hash = hashlib.sha256(
            request.transcript.encode("utf-8")
        ).hexdigest()[:16]
        correlation_id = request.correlation_id or ""
        now = datetime.now(timezone.utc)
        dataset_version = snapshot.version if snapshot else ""

        # Enqueue is defensive: a raising enqueue (or a full/failing bounded
        # queue) must never affect the response the pipeline already assembled,
        # in particular the restored Span and veto count (Req 9.12). The
        # production writer's ``enqueue`` swallows overflow, but guarding here
        # makes the isolation explicit and robust to any writer.
        def _enqueue(record: HistoryRecord) -> None:
            try:
                history_writer.enqueue(record)
            except Exception:  # noqa: BLE001 - history is fire-and-forget (Req 9.12)
                logger.debug("history.enqueue_failed")

        for cr in corrections:
            _enqueue(
                HistoryRecord(
                    correlation_id=correlation_id,
                    text_hash=text_hash,
                    original=cr.original,
                    corrected=cr.corrected,
                    strategy=cr.strategy.value,
                    confidence=cr.confidence,
                    entity_kind=cr.entity_kind.value,
                    entity_type=cr.entity_type.value,
                    model_version=dataset_version,
                    created_at=now,
                    outcome="applied",
                )
            )

        if veto_outcome is not None:
            for rv in veto_outcome.restored:
                rc = rv.correction
                _enqueue(
                    HistoryRecord(
                        correlation_id=correlation_id,
                        text_hash=text_hash,
                        original=rc.original,
                        corrected=rc.corrected,
                        strategy="",
                        confidence=0.0,
                        entity_kind=rc.entity_kind,
                        entity_type="",
                        model_version=dataset_version,
                        created_at=now,
                        outcome="vetoed",
                    )
                )

    return CorrectionResponse(
        transcript=final_text,
        words=response_words,
        entities=entity_summary,
        metadata=metadata,
        corrections=corrections,
    )


# ---------------------------------------------------------------------------
# Corrections-list assembly and LLM Veto helpers (task 4.8, Req 9)
# ---------------------------------------------------------------------------


def _build_corrections(
    text_result: TextCorrectionResult,
    word_result: WordCorrectionResult,
) -> tuple[list[CorrectionRecord], int]:
    """Assemble the response corrections list (text first, then word).

    Returns ``(corrections, text_correction_count)``. The count is the number
    of leading text corrections, so the LLM Veto helper can map a
    ``word_result.veto_spans`` entry (index *k*) to the corrections entry at
    ``text_correction_count + k`` (Req 9.3).
    """
    corrections: list[CorrectionRecord] = []

    for tc in text_result.corrections:
        corrections.append(
            CorrectionRecord(
                original=tc.original,
                corrected=tc.replacement,
                strategy=MatchStrategy(tc.strategy)
                if tc.strategy in MatchStrategy.__members__
                else MatchStrategy.exact,
                confidence=tc.confidence,
                entity_kind=EntityKind(tc.entity_kind)
                if tc.entity_kind in EntityKind.__members__
                else EntityKind.location,
                entity_type=EntityType(tc.entity_type)
                if tc.entity_type in EntityType.__members__
                else EntityType.supplementary,
            )
        )

    text_correction_count = len(corrections)

    for original, corrected, strategy, confidence, kind, etype in word_result.corrections:
        corrections.append(
            CorrectionRecord(
                original=original,
                corrected=corrected,
                strategy=MatchStrategy(strategy)
                if strategy in MatchStrategy.__members__
                else MatchStrategy.exact,
                confidence=confidence,
                entity_kind=EntityKind(kind)
                if kind in EntityKind.__members__
                else EntityKind.location,
                entity_type=EntityType(etype)
                if etype in EntityType.__members__
                else EntityType.supplementary,
            )
        )

    return corrections, text_correction_count


def _build_rule_corrections(
    corrections: list[CorrectionRecord],
    word_result: WordCorrectionResult,
    text_correction_count: int,
) -> list[RuleCorrection]:
    """Build the vetoable rule corrections from the word-correction span state (Req 9.1).

    Only word-level corrections carry a pre-correction Word slice (captured in
    ``word_result.veto_spans`` when the LLM Veto flag is on, Req 9.4), so only
    they can be restored in the Words list. Each veto span is index-aligned to
    the word-correction entry at ``text_correction_count + k`` in *corrections*,
    which is the entry :func:`app.llm.veto.apply_vetoes` removes on a veto
    (Req 9.3). Text-only corrections are not offered to the veto here because
    the Words-list restoration (Req 9.4) has no counterpart for them; the empty
    Words path returns text corrections only (Req 14.8) and is out of veto scope.
    """
    rule_corrections: list[RuleCorrection] = []
    for k, span in enumerate(word_result.veto_spans):
        record_index = text_correction_count + k
        if record_index >= len(corrections):
            continue
        # A Span can be recorded twice — once as a transcript-text correction
        # (indices 0..text_correction_count-1) and once as this word correction.
        # Collect any leading text-correction entry with the same original and
        # corrected text so a veto removes both, keeping the corrections list
        # consistent with the restored transcript and Words (Req 9.3, 9.10).
        mirror = tuple(
            idx
            for idx in range(text_correction_count)
            if corrections[idx].original == span.original
            and corrections[idx].corrected == span.corrected
        )
        rule_corrections.append(
            RuleCorrection(
                record_index=record_index,
                original=span.original,
                corrected=span.corrected,
                entity_kind=span.entity_kind,
                veto_span=span,
                mirror_record_indices=mirror,
            )
        )
    return rule_corrections
