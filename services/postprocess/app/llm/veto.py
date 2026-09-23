"""LLM_Refiner veto — resolution and pipeline restore logic (Req 9).

The refiner (``app/llm/refiner.py``) supplies the model, per chunk, the rule
corrections whose Span falls in that chunk and parses the model's veto
decisions back out. This module holds the provider-agnostic pieces the pipeline
uses to turn those decisions into restorations:

* :class:`RuleCorrection` — one Correction_Engine correction the pipeline can
  veto, carrying its index into the response ``corrections`` list, its original
  and corrected text, the matched entity kind (Req 9.13), and the pre-correction
  Word slice needed to restore Word count and timings (Req 9.4).
* :func:`assign_corrections_to_chunks` — maps each rule correction to the chunk
  whose text contains its corrected Span (Req 9.1).
* :func:`resolve_vetoes` — resolves each veto decision to exactly one supplied
  correction within its chunk, discarding a decision that resolves to zero or
  more than one (Req 9.11).
* :func:`apply_vetoes` — restores every resolved veto in the transcript text and
  the Words list, leaving no occurrence of the vetoed corrected text at the
  restored position (Req 9.2, 9.10), restoring the pre-correction Word count and
  timings (Req 9.4), and removing exactly the one ``corrections`` entry per
  restored Span while preserving the order of the rest (Req 9.3).

Everything here is pure and synchronous — no Bedrock, no I/O — so the pipeline
can drive it after the refiner returns and enqueue the vetoed history records
itself (Req 9.5). Failures in history persistence are the pipeline's concern and
never reach this module (Req 9.12).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.correction.engine import VetoSpan
from app.models.entities import CorrectionRecord


@dataclass
class RuleCorrection:
    """One Correction_Engine correction eligible for an LLM veto (Req 9.1).

    ``record_index`` is the position of the correction's entry in the response
    ``corrections`` list, so :func:`apply_vetoes` can remove exactly that entry
    (Req 9.3). ``veto_span`` carries the pre-correction Word slice for a
    word-level correction (Req 9.4); it is ``None`` for a text-only correction
    that has no Words-list counterpart (an empty Words request), in which case
    only the transcript text and the ``corrections`` entry are restored.
    """

    record_index: int
    original: str
    corrected: str
    entity_kind: str
    veto_span: VetoSpan | None = None
    # Additional corrections-list indices that record the SAME logical Span
    # (e.g. the transcript-text correction that mirrors this word correction).
    # The pipeline populates these so a restore removes every entry recorded for
    # the Span, keeping the transcript, Words list, and corrections list mutually
    # consistent (Req 9.3, 9.10). Empty when the Span has only one entry.
    mirror_record_indices: tuple[int, ...] = ()


@dataclass
class ResolvedVeto:
    """A veto decision resolved to exactly one :class:`RuleCorrection` (Req 9.11)."""

    correction: RuleCorrection


@dataclass
class VetoOutcome:
    """The result of applying vetoes for a request.

    ``restored`` is the list of resolved vetoes actually applied, in the order
    their corrections appeared, so the pipeline can enqueue one history record
    per restored Span (Req 9.5) and report the count (Req 9.7). ``final_text``
    and ``final_words`` are the restored transcript and Words list (Req 9.2,
    9.4, 9.10). ``corrections`` is the pruned response corrections list with
    exactly one entry removed per restored Span, remaining entries in their
    original order (Req 9.3).
    """

    final_text: str
    final_words: list[dict]
    corrections: list[CorrectionRecord]
    restored: list[ResolvedVeto] = field(default_factory=list)

    @property
    def veto_count(self) -> int:
        """Number of corrections entries removed for restored Spans (Req 9.7)."""
        return len(self.restored)


def assign_corrections_to_chunks(
    corrections: list[RuleCorrection],
    chunk_texts: list[str],
) -> list[list[RuleCorrection]]:
    """Group rule corrections by the chunk whose text contains their Span (Req 9.1).

    A correction is assigned to the first chunk whose text contains its
    corrected Span text (the text the rule stage wrote, which is what appears in
    the chunk). A correction whose corrected text matches no chunk is left
    unassigned — the model is never asked about it, so it is retained unchanged.

    Returns one list per chunk, parallel to *chunk_texts*.
    """
    per_chunk: list[list[RuleCorrection]] = [[] for _ in chunk_texts]
    for corr in corrections:
        needle = corr.corrected
        for idx, text in enumerate(chunk_texts):
            if needle and needle in text:
                per_chunk[idx].append(corr)
                break
    return per_chunk


def resolve_vetoes(
    decisions: list[tuple[str, str]],
    chunk_corrections: list[RuleCorrection],
) -> list[ResolvedVeto]:
    """Resolve each veto decision to exactly one supplied correction (Req 9.11).

    A decision is a ``(original, corrected)`` pair the model emitted for this
    chunk. It resolves when exactly one of *chunk_corrections* matches both the
    original and the corrected text (compared case-insensitively and trimmed).
    A decision that matches zero corrections, or two or more, is discarded; the
    remaining resolvable decisions still apply (Req 9.11). Each supplied
    correction is consumed at most once, so two identical decisions cannot
    restore the same Span twice.

    Returns the resolved vetoes in decision order.
    """
    resolved: list[ResolvedVeto] = []
    consumed: set[int] = set()
    for dec_original, dec_corrected in decisions:
        do = dec_original.strip().lower()
        dc = dec_corrected.strip().lower()
        matches = [
            corr
            for corr in chunk_corrections
            if corr.record_index not in consumed
            and corr.original.strip().lower() == do
            and corr.corrected.strip().lower() == dc
        ]
        if len(matches) != 1:
            # Zero or ambiguous → discard this decision (Req 9.11).
            continue
        corr = matches[0]
        consumed.add(corr.record_index)
        resolved.append(ResolvedVeto(correction=corr))
    return resolved


def _restore_words(final_words: list[dict], veto_span: VetoSpan) -> bool:
    """Restore one vetoed Span's Words in place; return True on success (Req 9.4, 9.10).

    Locates the corrected Word in *final_words* — the Word whose text equals the
    correction's corrected text and that carries the ``locationCorrected`` flag
    the rule stage set — and replaces it with the pre-correction Word slice, so
    the original Word count and each Word's ``start``/``end`` are reinstated
    (Req 9.4) and no occurrence of the vetoed corrected text remains at that
    position (Req 9.10). Uses the recorded ``output_index`` as a hint, falling
    back to a forward scan, because the Year_Corrector may have shifted indices.

    Returns ``False`` when the corrected Word cannot be found (already restored,
    or altered by a later stage), leaving *final_words* untouched so the caller
    treats the veto as unresolved for the Words list.
    """
    target = veto_span.corrected

    def _is_target(w: dict) -> bool:
        return w.get("word") == target and bool(w.get("locationCorrected"))

    idx = veto_span.output_index
    if not (0 <= idx < len(final_words) and _is_target(final_words[idx])):
        idx = next(
            (j for j, w in enumerate(final_words) if _is_target(w)),
            -1,
        )
    if idx < 0:
        return False

    restored_slice = [dict(ow) for ow in veto_span.original_words]
    final_words[idx : idx + 1] = restored_slice
    return True


def _restore_text(final_text: str, original: str, corrected: str) -> str:
    """Replace the first occurrence of *corrected* with *original* (Req 9.2, 9.10).

    Restores the vetoed Span in the transcript string so no occurrence of the
    vetoed corrected text remains at that position. Only the first occurrence is
    replaced, matching the one-Span-per-veto contract; when the corrected text
    is not present (already restored, or never in the text) the string is
    returned unchanged.
    """
    if not corrected or corrected not in final_text:
        return final_text
    return final_text.replace(corrected, original, 1)


def apply_vetoes(
    final_text: str,
    final_words: list[dict],
    corrections: list[CorrectionRecord],
    resolved: list[ResolvedVeto],
) -> VetoOutcome:
    """Apply resolved vetoes to the transcript, Words list, and corrections (Req 9.2-9.4, 9.10).

    For each resolved veto, in the order its correction appears in the response
    ``corrections`` list:

    * restore the original Span text in the transcript, leaving no occurrence of
      the vetoed corrected text at that position (Req 9.2, 9.10);
    * restore the pre-correction Word slice with its original count and timings
      (Req 9.4, 9.10) — a person-entity veto is restored the same way, not
      retained under the person guard (Req 9.13), because the restore is driven
      by the veto decision, not by ``entity_kind``;
    * mark the correction's ``corrections`` entry for removal.

    Exactly one ``corrections`` entry is removed per restored Span and the
    remaining entries keep their original order (Req 9.3). A veto whose Words
    restore fails (the corrected Word is gone) is skipped entirely — its text,
    Words, and ``corrections`` entry are all left unchanged — so the response
    stays internally consistent.

    Returns a :class:`VetoOutcome` with the restored text, restored Words, the
    pruned corrections list, and the vetoes actually applied.
    """
    # Process in corrections-list order for deterministic text/index handling.
    ordered = sorted(resolved, key=lambda rv: rv.correction.record_index)

    words = final_words
    text = final_text
    applied: list[ResolvedVeto] = []
    remove_indices: set[int] = set()

    for rv in ordered:
        corr = rv.correction

        # Restore the Words list first (Req 9.4). When there is no Words-list
        # counterpart (text-only correction, empty Words request), skip the
        # Words restore but still restore the text and drop the entry. When the
        # corrected Word cannot be found, treat this veto as inapplicable and
        # leave everything for this Span unchanged.
        if (
            corr.veto_span is not None
            and words
            and not _restore_words(words, corr.veto_span)
        ):
            continue

        text = _restore_text(text, corr.original, corr.corrected)
        remove_indices.add(corr.record_index)
        # Also drop any mirror entry recording the same Span (e.g. the
        # transcript-text correction) so the corrections list stays consistent
        # with the restored transcript and Words (Req 9.3, 9.10).
        remove_indices.update(corr.mirror_record_indices)
        applied.append(rv)

    pruned = [
        record
        for idx, record in enumerate(corrections)
        if idx not in remove_indices
    ]

    return VetoOutcome(
        final_text=text,
        final_words=words,
        corrections=pruned,
        restored=applied,
    )
