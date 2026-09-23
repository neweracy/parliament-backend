"""Evidence builder — map each Correction_Engine change to a Correction_Entry.

Feature: transcript-evidence-navigation (task 2.2).

The correction pipeline already produces a ``corrections`` array (each element a
:class:`~app.models.entities.CorrectionRecord`: ``original``, ``corrected``,
``strategy``, ``confidence``, ``entity_kind``, ``entity_type``) and, when the
LLM Veto ran, a set of restored (``vetoed``) Spans. This module turns those
outputs into :class:`~app.models.evidence.CorrectionEntry` rows, one per
Postprocessing_Change (Property 2), addressing each change back to the raw ASR
Source_Words it replaced.

Source addressing (Req 2.1-2.4, 2.10)
-------------------------------------
Each change's ``original`` text is the whitespace-joined run of the raw ASR
Source_Words it replaced (the engine builds it as ``" ".join(window_words)``).
The builder aligns that run back to the raw ASR word list — using the stable
Source_Word_Ids assigned before corrections ran (task 1.1) — and derives:

* ``source_word_ids`` — every covered raw word's id, in ASR order;
* ``source_span_id`` — ``s:{first}-{last}`` from the covered ids;
* ``source_start`` — the ASR ``start`` of the first covered word;
* ``source_end`` — the ASR ``end`` of the last covered word.

The alignment reads position and text only; it never reads or rewrites the ASR
``start``/``end`` of any word (Immutable_Source_Timing, Req 2.7).

Lost mapping (Req 2.10)
-----------------------
When the alignment between the corrected change and the raw Source_Words
diverges so no contiguous run of raw words matches the ``original`` text, the
entry is emitted with ``mapping_confidence = lost`` and an empty
``source_word_ids`` / synthesized span id, rather than an inferred range — never
a guessed range (Property 7).

Stage, outcome, and provenance (Req 1.4, 1.5, 1.7, 1.8, 2.5, 2.6)
-----------------------------------------------------------------
``correction_stage`` is set by origin (Correction_Engine -> ``rule``,
Year_Corrector -> ``year``, LLM_Refiner -> ``llm``). ``correction_outcome`` is
``applied`` for a change present in the returned transcript and ``vetoed`` for
one the LLM_Refiner restored to its original Source_Span text; a ``vetoed``
entry retains the original text and timing unchanged and is not rendered as
current text. Provenance (``correlation_id``, ``dataset_version``, ``model_id``)
is recorded where the producing stage supplied it and omitted otherwise (no
placeholder) — the ``exclude_none`` serialization contract on the model drops
absent fields.

This module is pure and free of pipeline/engine state so it can be unit-tested
in isolation and called from the pipeline (task 4.1) without widening any
correction signature.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.correction.source_ids import source_span_id, source_word_id
from app.models.entities import CorrectionRecord
from app.models.evidence import (
    CorrectionEntry,
    CorrectionEvidence,
    CorrectionOutcome,
    CorrectionStage,
    MappingConfidence,
)

# Characters stripped from a token's leading/trailing edges before an alignment
# comparison. Mirrors ``app.correction.confidence`` so a change's ``original``
# run aligns to the raw Words carrying the same surface forms even when the
# engine's tokenizer discarded edge punctuation.
_EDGE_PUNCT = ".,;:!?\"'()[]{}<>\u201c\u201d\u2018\u2019\u2026\u2014\u2013-"


def _normalize_token(token: str) -> str:
    """Lowercase *token* and strip leading/trailing punctuation.

    Internal separators (hyphens, apostrophes) are preserved so a hyphenated or
    apostrophised name aligns to the raw Word carrying the same surface form.
    """
    return token.strip(_EDGE_PUNCT).lower()


def _word_text(word: Any) -> str:
    """Return the surface text of a raw ASR Word (dict or object)."""
    value = word.get("word", "") if isinstance(word, dict) else getattr(word, "word", "")
    return value if isinstance(value, str) else ""


def _word_timing(word: Any, key: str) -> float | None:
    """Return the ASR ``start`` or ``end`` of a raw Word, or ``None``.

    Reads timing only; never writes it (Immutable_Source_Timing, Req 2.7).
    """
    value = word.get(key) if isinstance(word, dict) else getattr(word, key, None)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _find_source_run(
    original: str,
    raw_words: Sequence[Any],
    search_from: int,
) -> tuple[int, int] | None:
    """Find the contiguous run of raw Words matching *original* (Req 2.1-2.4).

    The ``original`` text of a change is the whitespace-joined run of the raw
    Source_Words it replaced. This scans *raw_words* from *search_from* for the
    first contiguous run whose normalized surface forms equal, in order, the
    normalized tokens of *original*.

    Parameters
    ----------
    original:
        The change's original Source_Span text.
    raw_words:
        The raw ASR Words in ASR order.
    search_from:
        The index to begin scanning from. Callers advance this so repeated
        identical Spans map to successive raw runs rather than all to the first.

    Returns
    -------
    tuple[int, int] | None
        ``(first_index, last_index)`` (inclusive) of the matched run, or
        ``None`` when no contiguous run matches (Req 2.10 — the caller emits a
        ``lost`` mapping rather than an inferred range).
    """
    tokens = [_normalize_token(t) for t in original.split() if _normalize_token(t)]
    if not tokens:
        return None
    span_len = len(tokens)
    last_start = len(raw_words) - span_len
    for start in range(max(0, search_from), last_start + 1):
        if all(
            _normalize_token(_word_text(raw_words[start + offset])) == tokens[offset]
            for offset in range(span_len)
        ):
            return (start, start + span_len - 1)
    return None


def _build_entry(
    record: CorrectionRecord,
    raw_words: Sequence[Any],
    source_word_ids: Sequence[str],
    *,
    stage: CorrectionStage,
    outcome: CorrectionOutcome,
    threshold: float | None,
    correlation_id: str | None,
    dataset_version: str | None,
    model_id: str | None,
    search_from: int,
) -> tuple[CorrectionEntry, int]:
    """Build one :class:`CorrectionEntry` for *record* and its next search index.

    Aligns *record*'s ``original`` run back to the raw Words to derive the
    source addressing and immutable timing (Req 2.1-2.4). When the run cannot be
    resolved the entry carries ``mapping_confidence = lost`` with no inferred
    range (Req 2.10). Carries ``original``/``corrected``/``confidence`` and the
    entity classification across from the record (Property 2), records the
    ``stage``/``outcome``/``threshold``, and attaches supplied provenance —
    unsupplied provenance is left ``None`` and omitted by the model serializer
    (Req 1.5).

    Returns ``(entry, next_search_from)`` so the caller advances past the matched
    run for the next record, letting repeated identical Spans resolve to
    successive raw runs.
    """
    run = _find_source_run(record.original, raw_words, search_from)

    if run is None:
        # Alignment diverged — no resolvable Source_Word (Req 2.10). Emit a
        # ``lost`` mapping with no inferred range rather than guessing.
        entry = CorrectionEntry(
            source_span_id=f"s:lost:{_normalize_token(record.original) or '_'}",
            source_word_ids=[],
            original=record.original,
            corrected=record.corrected,
            source_start=None,
            source_end=None,
            correction_stage=stage,
            correction_outcome=outcome,
            confidence=record.confidence,
            threshold=threshold,
            entity_kind=str(record.entity_kind) if record.entity_kind else None,
            entity_type=str(record.entity_type) if record.entity_type else None,
            correlation_id=correlation_id,
            dataset_version=dataset_version,
            model_id=model_id,
            mapping_confidence=MappingConfidence.lost,
        )
        return entry, search_from

    first_index, last_index = run
    covered_ids = [
        source_word_ids[i] if i < len(source_word_ids) else source_word_id(i)
        for i in range(first_index, last_index + 1)
    ]
    span_id = source_span_id(covered_ids)

    entry = CorrectionEntry(
        source_span_id=span_id,
        source_word_ids=covered_ids,
        original=record.original,
        corrected=record.corrected,
        source_start=_word_timing(raw_words[first_index], "start"),
        source_end=_word_timing(raw_words[last_index], "end"),
        correction_stage=stage,
        correction_outcome=outcome,
        confidence=record.confidence,
        threshold=threshold,
        entity_kind=str(record.entity_kind) if record.entity_kind else None,
        entity_type=str(record.entity_type) if record.entity_type else None,
        correlation_id=correlation_id,
        dataset_version=dataset_version,
        model_id=model_id,
        # mapping_confidence left None (resolved client-side against displayed
        # text) — the builder only asserts ``lost`` when it already knows the
        # source alignment diverged (Req 2.10).
    )
    return entry, last_index + 1


def build_correction_entries(
    records: Sequence[CorrectionRecord],
    raw_words: Sequence[Any],
    source_word_ids: Sequence[str],
    *,
    stage: CorrectionStage = CorrectionStage.rule,
    outcome: CorrectionOutcome = CorrectionOutcome.applied,
    threshold: float | None = None,
    correlation_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
) -> list[CorrectionEntry]:
    """Map each :class:`CorrectionRecord` to exactly one Correction_Entry (Property 2).

    Iterates *records* in order, aligning each ``original`` run back to
    *raw_words* to derive stable source addressing and immutable ASR timing
    (Req 2.1-2.4, 2.7). Repeated identical Spans resolve to successive raw runs
    because the search advances past each matched run. A record whose run cannot
    be resolved yields a ``lost`` mapping with no inferred range (Req 2.10).

    Parameters
    ----------
    records:
        The Correction_Engine ``corrections`` array (or a subset sharing one
        *stage*/*outcome*), each a :class:`CorrectionRecord`.
    raw_words:
        The raw ASR Words in ASR order (dicts or ``Word``-like objects). Only
        text and ``start``/``end`` are read.
    source_word_ids:
        The stable Source_Word_Ids for *raw_words* (task 1.1), index-aligned to
        *raw_words*. A short/absent list falls back to the deterministic
        ``w{index}`` scheme so the builder never crashes on a mismatch.
    stage:
        The Correction_Stage every record in this batch originates from
        (Req 1.7). Defaults to ``rule`` (the Correction_Engine).
    outcome:
        The Correction_Outcome for this batch (Req 1.8). Defaults to
        ``applied``.
    threshold:
        The acceptance threshold the confidence was compared against (Req 1.1),
        or ``None`` when the stage supplied none.
    correlation_id, dataset_version, model_id:
        Provenance reused from ``Metadata`` (and the LLM stage's model id where
        supplied). Each is omitted from the serialized entry when ``None``
        (Req 1.4, 1.5).

    Returns
    -------
    list[CorrectionEntry]
        One entry per record, in the same order.
    """
    entries: list[CorrectionEntry] = []
    search_from = 0
    for record in records:
        entry, search_from = _build_entry(
            record,
            raw_words,
            source_word_ids,
            stage=stage,
            outcome=outcome,
            threshold=threshold,
            correlation_id=correlation_id,
            dataset_version=dataset_version,
            model_id=model_id,
            search_from=search_from,
        )
        entries.append(entry)
    return entries


def build_correction_evidence(
    transcript_id: int,
    version: int,
    raw_words: Sequence[Any],
    source_word_ids: Sequence[str],
    *,
    applied_records: Sequence[CorrectionRecord],
    vetoed_records: Sequence[CorrectionRecord] = (),
    year_records: Sequence[CorrectionRecord] = (),
    llm_records: Sequence[CorrectionRecord] = (),
    rule_threshold: float | None = None,
    llm_threshold: float | None = None,
    correlation_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
) -> CorrectionEvidence:
    """Assemble the per-version :class:`CorrectionEvidence` from pipeline outputs.

    Groups the pipeline's changes by origin so each batch carries the right
    Correction_Stage and Correction_Outcome (Req 1.7, 1.8), then maps every
    record to exactly one entry (Property 2). All four batches share the raw ASR
    word list and its stable ids so every entry addresses the same
    Source_Word_Ids the citation navigation resolves against.

    Batches
    -------
    * *applied_records* — Correction_Engine changes present in the returned
      transcript -> ``stage=rule``, ``outcome=applied``.
    * *vetoed_records* — changes the LLM_Refiner restored to their original
      Source_Span text -> ``stage=llm``, ``outcome=vetoed``. Their ``original``
      text and timing are retained unchanged and are not rendered as current
      text (Req 1.9, 2.6).
    * *year_records* — Year_Corrector changes -> ``stage=year``,
      ``outcome=applied``.
    * *llm_records* — LLM_Refiner changes present in the transcript ->
      ``stage=llm``, ``outcome=applied``; ``model_id`` recorded where supplied
      (Req 2.5).

    Parameters
    ----------
    transcript_id, version:
        The key the evidence is persisted under (Req 1.6).
    raw_words, source_word_ids:
        The raw ASR Words and their stable ids (task 1.1).
    rule_threshold, llm_threshold:
        The acceptance thresholds the rule/LLM confidences were compared against
        (Req 1.1). ``year`` changes carry no confidence threshold.

    Returns
    -------
    CorrectionEvidence
        The per-version container of one entry per Postprocessing_Change.
    """
    entries: list[CorrectionEntry] = []

    entries.extend(
        build_correction_entries(
            applied_records,
            raw_words,
            source_word_ids,
            stage=CorrectionStage.rule,
            outcome=CorrectionOutcome.applied,
            threshold=rule_threshold,
            correlation_id=correlation_id,
            dataset_version=dataset_version,
        )
    )
    entries.extend(
        build_correction_entries(
            year_records,
            raw_words,
            source_word_ids,
            stage=CorrectionStage.year,
            outcome=CorrectionOutcome.applied,
            threshold=None,
            correlation_id=correlation_id,
            dataset_version=dataset_version,
        )
    )
    entries.extend(
        build_correction_entries(
            llm_records,
            raw_words,
            source_word_ids,
            stage=CorrectionStage.llm,
            outcome=CorrectionOutcome.applied,
            threshold=llm_threshold,
            correlation_id=correlation_id,
            dataset_version=dataset_version,
            model_id=model_id,
        )
    )
    entries.extend(
        build_correction_entries(
            vetoed_records,
            raw_words,
            source_word_ids,
            stage=CorrectionStage.llm,
            outcome=CorrectionOutcome.vetoed,
            threshold=llm_threshold,
            correlation_id=correlation_id,
            dataset_version=dataset_version,
            model_id=model_id,
        )
    )

    return CorrectionEvidence(
        transcript_id=transcript_id,
        version=version,
        entries=entries,
    )
