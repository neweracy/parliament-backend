"""Answer text parsing — citation markers and the recommendations block.

Shared by the grounded chain and the conversational agent so citation
validation and recommendation extraction cannot drift between the two.
"""

from __future__ import annotations

import re

import structlog

from app.rag.recommendations import Citation, RecommendationItem
from app.rag.retriever import RetrievedChunk

logger = structlog.get_logger("rag.parsing")

# Deterministic text returned when the model invocation raises
GENERATION_FAILURE_TEXT = "Unable to generate an answer at this time. Please try again later."

# Citation markers, e.g. "[42]" — stripped from the answer on the zero-context
# path, where nothing is citable and a marker could only be a dead reference
_CITATION_MARKER_RE = re.compile(r"\[\d+\]")


def parse_citations(answer_text: str) -> list[int]:
    """Extract chunk_id integers from [chunk_id] citation markers.

    Returns a deduplicated list of chunk IDs in order of first appearance.
    """
    pattern = re.compile(r"\[(\d+)\]")
    matches = pattern.findall(answer_text)

    seen: set[int] = set()
    chunk_ids: list[int] = []

    for match in matches:
        chunk_id = int(match)
        if chunk_id not in seen:
            seen.add(chunk_id)
            chunk_ids.append(chunk_id)

    return chunk_ids


def strip_citation_markers(answer_text: str) -> str:
    """Remove every [digits] marker and tidy the whitespace it leaves behind.

    Used on the zero-context path only, where no chunk is citable.
    """
    stripped = _CITATION_MARKER_RE.sub("", answer_text)
    # A marker sitting before punctuation or between words leaves a gap
    stripped = re.sub(r"[ \t]+([.,;:!?])", r"\1", stripped)
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    return stripped.strip()


def validate_citations(
    cited_chunk_ids: list[int],
    chunk_map: dict[int, RetrievedChunk],
) -> list[Citation]:
    """Validate cited chunk IDs against the source chunk map.

    Only citations referencing an actual chunk in the context are
    included. Invalid references are logged and dropped.
    """
    citations: list[Citation] = []

    for chunk_id in cited_chunk_ids:
        chunk = chunk_map.get(chunk_id)
        if chunk is None:
            logger.warning(
                "rag.parsing.invalid_citation",
                chunk_id=chunk_id,
                available_ids=list(chunk_map.keys()),
            )
            continue

        # Build excerpt (first 150 characters of chunk text)
        excerpt = chunk.text[:150]
        if len(chunk.text) > 150:
            excerpt += "..."

        citations.append(
            Citation(
                transcript_id=chunk.transcript_id,
                chunk_id=chunk.chunk_id,
                speaker=chunk.speaker,
                start_s=chunk.start_s,
                end_s=chunk.end_s,
                excerpt=excerpt,
            )
        )

    return citations


def split_recommendations(
    raw_answer: str,
) -> tuple[str, list[RecommendationItem]]:
    """Split the raw Claude response into answer text and recommendations.

    Looks for a RECOMMENDATIONS: section at the end of the response.
    Returns the answer text (without the recommendations section) and
    a list of parsed RecommendationItem objects.
    """
    recommendations: list[RecommendationItem] = []

    # Find the RECOMMENDATIONS section (case-insensitive)
    rec_pattern = re.compile(
        r"\n*(?:#{0,3}\s*)?RECOMMENDATIONS\s*:?\s*\n",
        re.IGNORECASE,
    )
    match = rec_pattern.search(raw_answer)

    if not match:
        return raw_answer.strip(), recommendations

    # Everything before the marker is the answer
    answer_text = raw_answer[: match.start()].strip()

    # Everything after is the recommendations block
    rec_block = raw_answer[match.end() :]

    # Parse individual recommendation lines
    # Format: "- [question] | [reason]" or "- [question]"
    line_pattern = re.compile(r"^[-•*]\s*(.+?)(?:\s*\|\s*(.+))?$", re.MULTILINE)

    for line_match in line_pattern.finditer(rec_block):
        text = line_match.group(1).strip()
        reason = (line_match.group(2) or "Related to the current discussion").strip()

        if text:
            recommendations.append(RecommendationItem(text=text, reason=reason))

    # Limit to 3 recommendations
    return answer_text, recommendations[:3]
