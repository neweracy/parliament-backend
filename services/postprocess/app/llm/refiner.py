"""LLM_Refiner — chunking, waves, tokenMap, and application.

Splits corrected words into ~LLM_CHUNK_SIZE chunks, processes them in waves
of LLM_MAX_PARALLEL concurrent invocations, aligns the LLM response back to
the Word list via the tokenMap, and enforces year/person guards on individual
changes.

Requirements: 11.1, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.datasets.cache import DatasetSnapshot
from app.llm.align import apply_aligned, apply_aligned_with_map
from app.llm.bedrock import BedrockClient
from app.llm.prompt import build_system_prompt, build_user_content, estimate_prompt_tokens
from app.llm.retrieval import retrieve_candidates

logger = structlog.get_logger("llm.refiner")


# ---------------------------------------------------------------------------
# Chunk data structure
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """One chunk of words[] entries for LLM processing."""

    words: list[dict]
    start_index: int
    text: str
    token_map: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_words(words: list[dict], chunk_size: int) -> list[Chunk]:
    """Split words into chunks of ~chunk_size entries with a per-chunk token_map.

    Each Chunk contains:
      - words: the slice of word dicts
      - start_index: absolute index of the first word in the original list
      - text: the joined text from that slice
      - token_map: maps each whitespace-token position → absolute words[] index

    The token_map handles multi-word entries (e.g. merged person names like
    "B.B. Carboo" or "Nana Addo Dankwa Akufo-Addo") so alignment can
    correctly resolve which words[] entry each LLM output token belongs to.
    """
    chunks: list[Chunk] = []

    for i in range(0, len(words), chunk_size):
        slice_ = words[i : i + chunk_size]

        # Build token_map: for each whitespace token position → words[] index
        token_map: list[int] = []
        for k, w in enumerate(slice_):
            word_text = w.get("word", "")
            n_tokens = max(1, len(word_text.split()))
            token_map.extend([i + k] * n_tokens)

        text = " ".join(w.get("word", "") for w in slice_)

        chunks.append(
            Chunk(
                words=slice_,
                start_index=i,
                text=text,
                token_map=token_map,
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Year and person guards (post-alignment)
# ---------------------------------------------------------------------------


def _apply_guards(words: list[dict], start_index: int, end_index: int) -> int:
    """Discard bedrock changes that would alter yearCorrected or person-flagged spans.

    Scans words[start_index:end_index] and reverts any bedrockCorrected change
    on a word that has:
      - yearCorrected: True
      - locationCorrected: True AND entityKind: "person"

    Returns the number of changes that were reverted.
    """
    reverted = 0

    for idx in range(start_index, min(end_index, len(words))):
        w = words[idx]

        if not w.get("bedrockCorrected"):
            continue

        # Guard 1: yearCorrected tokens must not be altered by LLM
        if w.get("yearCorrected"):
            # Revert: remove bedrockCorrected flag
            # The word text was already overwritten by alignment — we need
            # to restore it. We stored the original in _pre_llm_word if set.
            if "_pre_llm_word" in w:
                w["word"] = w.pop("_pre_llm_word")
            del w["bedrockCorrected"]
            reverted += 1
            continue

        # Guard 2: person-flagged locationCorrected spans must not be altered
        if w.get("locationCorrected") and w.get("entityKind") == "person":
            if "_pre_llm_word" in w:
                w["word"] = w.pop("_pre_llm_word")
            del w["bedrockCorrected"]
            reverted += 1
            continue

    return reverted


def _snapshot_pre_llm(words: list[dict], start_index: int, end_index: int) -> None:
    """Save the pre-LLM word text so guards can revert changes."""
    for idx in range(start_index, min(end_index, len(words))):
        w = words[idx]
        # Only snapshot words that have year or person protection
        if w.get("yearCorrected") or (
            w.get("locationCorrected") and w.get("entityKind") == "person"
        ):
            w["_pre_llm_word"] = w["word"]


def _cleanup_snapshots(words: list[dict], start_index: int, end_index: int) -> None:
    """Remove the temporary _pre_llm_word keys after guard application."""
    for idx in range(start_index, min(end_index, len(words))):
        words[idx].pop("_pre_llm_word", None)


# ---------------------------------------------------------------------------
# Single-chunk processing
# ---------------------------------------------------------------------------


async def _process_chunk(
    chunk: Chunk,
    chunk_index: int,
    words: list[dict],
    snapshot: DatasetSnapshot,
    bedrock_client: BedrockClient,
    session: AsyncSession | None,
    settings: Settings,
    semaphore: asyncio.Semaphore,
) -> int:
    """Process a single chunk: retrieve, prompt, invoke, align, guard.

    Returns the number of bedrock corrections applied (after guards).
    Raises on failure (caught by the caller).
    """
    async with semaphore:
        # 1. Retrieve relevant entities for this chunk
        entities = await retrieve_candidates(
            chunk.text,
            snapshot,
            session,
            max_records=settings.llm_max_prompt_records,
        )

        # 2. Build system prompt with those entities
        system_prompt = build_system_prompt(entities)

        # 3. Build user content with the chunk text
        user_content = build_user_content(chunk.text, chunk_index + 1)

        # 4. Record prompt token estimate
        token_estimate = estimate_prompt_tokens(system_prompt, user_content)
        logger.debug(
            "llm.refiner.chunk_prompt",
            chunk_index=chunk_index,
            token_estimate=token_estimate,
            word_count=len(chunk.words),
        )

        # 5. Call bedrock via asyncio.to_thread under asyncio.wait_for
        timeout_seconds = settings.llm_chunk_timeout_ms / 1000.0

        raw_response = await asyncio.wait_for(
            asyncio.to_thread(
                bedrock_client.invoke, system_prompt, user_content
            ),
            timeout=timeout_seconds,
        )

        # 6. Strip the [Segment N]: prefix
        corrected_text = re.sub(r"^\[Segment \d+\]:\s*", "", raw_response).strip()

        # 7. Tokenize the response
        corrected_tokens = corrected_text.split()
        original_tokens = chunk.text.split()

        # 8. Snapshot pre-LLM words for guard reversion
        end_index = chunk.start_index + len(chunk.words)
        _snapshot_pre_llm(words, chunk.start_index, end_index)

        # 9. Apply alignment
        # Extract the chunk's word slice — alignment functions mutate
        # the list in place (replacing dict entries at indices).
        chunk_slice = words[chunk.start_index : end_index]

        if len(corrected_tokens) == len(original_tokens):
            # Direct application when token counts agree
            apply_aligned(chunk_slice, corrected_tokens)
        else:
            # LCS alignment via tokenMap when counts differ
            logger.debug(
                "llm.refiner.token_mismatch",
                chunk_index=chunk_index,
                original_count=len(original_tokens),
                corrected_count=len(corrected_tokens),
            )
            # Build a local token_map (0-based for the slice)
            local_token_map: list[int] = []
            for k, w in enumerate(chunk_slice):
                word_text = w.get("word", "")
                n_tokens = max(1, len(word_text.split()))
                local_token_map.extend([k] * n_tokens)

            apply_aligned_with_map(chunk_slice, corrected_tokens, local_token_map)

        # Write the (possibly mutated) slice back into the master list
        for k, w in enumerate(chunk_slice):
            words[chunk.start_index + k] = w

        # 10. Apply guards (revert changes on yearCorrected / person spans)
        reverted = _apply_guards(words, chunk.start_index, end_index)
        if reverted > 0:
            logger.debug(
                "llm.refiner.guards_reverted",
                chunk_index=chunk_index,
                reverted=reverted,
            )

        # 11. Cleanup temporary snapshot keys
        _cleanup_snapshots(words, chunk.start_index, end_index)

        # 12. Count bedrock corrections in this chunk
        corrections = 0
        for idx in range(chunk.start_index, end_index):
            if words[idx].get("bedrockCorrected"):
                corrections += 1

        return corrections


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def refine_chunks(
    words: list[dict],
    snapshot: DatasetSnapshot,
    bedrock_client: BedrockClient,
    session: AsyncSession | None,
    settings: Settings,
) -> tuple[list[dict], str, int]:
    """Orchestrate LLM refinement of corrected words.

    Returns:
        (refined_words, llm_status, bedrock_correction_count)

    llm_status values:
        - "ok": all chunks succeeded
        - "partial": some chunks failed but at least one succeeded
        - "failed": all chunks failed
        - "skipped": LLM was disabled in options (caller checks this)
        - "unconfigured": no Bedrock credentials available (caller checks this)
    """
    if not words:
        return words, "ok", 0

    # Chunk the words
    chunks = chunk_words(words, settings.llm_chunk_size)

    if not chunks:
        return words, "ok", 0

    # Process chunks in waves using Semaphore for concurrency control
    semaphore = asyncio.Semaphore(settings.llm_max_parallel)

    total_corrections = 0
    chunks_succeeded = 0
    chunks_failed = 0

    # Launch all chunks concurrently (semaphore limits parallelism)
    tasks = [
        _process_chunk(
            chunk=chunk,
            chunk_index=idx,
            words=words,
            snapshot=snapshot,
            bedrock_client=bedrock_client,
            session=session,
            settings=settings,
            semaphore=semaphore,
        )
        for idx, chunk in enumerate(chunks)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, result in enumerate(results):
        if isinstance(result, BaseException):
            chunks_failed += 1
            # Log the failure reason
            if isinstance(result, asyncio.TimeoutError):
                logger.warning(
                    "llm.refiner.chunk_timeout",
                    chunk_index=idx,
                    timeout_ms=settings.llm_chunk_timeout_ms,
                )
            else:
                logger.warning(
                    "llm.refiner.chunk_failed",
                    chunk_index=idx,
                    error=str(result),
                )
            # Cleanup any snapshot keys left behind on failure
            if idx < len(chunks):
                chunk = chunks[idx]
                end_index = chunk.start_index + len(chunk.words)
                _cleanup_snapshots(words, chunk.start_index, end_index)
        else:
            chunks_succeeded += 1
            total_corrections += result

    # Determine llm_status
    if chunks_failed == 0:
        llm_status = "ok"
    elif chunks_succeeded > 0:
        llm_status = "partial"
    else:
        llm_status = "failed"

    return words, llm_status, total_corrections
