"""Answer text parsing — citation markers and the recommendations block.

Shared by the grounded chain and the conversational agent so citation
validation and recommendation extraction cannot drift between the two.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.rag.recommendations import (
    TARGET_SUGGESTION_COUNT,
    Citation,
    RecommendationItem,
)
from app.rag.retriever import RetrievedChunk

logger = structlog.get_logger("rag.parsing")

# Deterministic text returned when the model invocation raises
GENERATION_FAILURE_TEXT = "Unable to generate an answer at this time. Please try again later."

# Applied when a recommendation line omits its "| reason" half. Non-empty by
# construction so a parsed item always satisfies the non-empty-reason contract.
DEFAULT_RECOMMENDATION_REASON = "Related to the current discussion"

# Citation markers, e.g. "[42]" — stripped from the answer on the zero-context
# path, where nothing is citable and a marker could only be a dead reference
_CITATION_MARKER_RE = re.compile(r"\[\d+\]")

# The RECOMMENDATIONS: heading that closes an answer, optionally preceded by
# markdown heading hashes and followed by a colon.
_RECOMMENDATIONS_HEADING_RE = re.compile(
    r"\n*(?:#{0,3}\s*)?RECOMMENDATIONS\s*:?\s*\n",
    re.IGNORECASE,
)

# List marker: a dash, bullet, numbered prefix, or a lone asterisk. The
# lookahead stops a `**bold**` opener from being eaten as a bullet, which would
# strand the second asterisk inside the captured text.
_LIST_MARKER = r"(?:[-•]|\*(?!\*)|\d+[.)])"

# "- text | reason", "* text", "1. text | reason" — bullet or numbered.
_BULLET_LINE_RE = re.compile(
    rf"^\s*{_LIST_MARKER}\s*(.+?)(?:\s*\|\s*(.+))?$",
    re.MULTILINE,
)

# As above, but the leading marker is optional. Blank lines never match because
# the text group requires at least one non-whitespace character.
_BARE_LINE_RE = re.compile(
    rf"^\s*{_LIST_MARKER}?\s*(\S.*?)(?:\s*\|\s*(.+))?$",
    re.MULTILINE,
)

# Markdown emphasis wrappers: **bold**, *italic*, __bold__, _italic_, `code`.
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1", re.DOTALL)


def _strip_emphasis(value: str) -> str:
    """Remove markdown emphasis wrappers and collapse whitespace.

    Applied to both halves of a recommendation line so `**budget allocation**`
    and `budget allocation` normalise to the same text. Runs repeatedly to
    unwrap nesting such as `**_term_**`, and drops any stray leftover markers
    so an unbalanced wrapper cannot leak into the UI.
    """
    text = value.strip()
    for _ in range(3):
        unwrapped = _EMPHASIS_RE.sub(r"\2", text)
        if unwrapped == text:
            break
        text = unwrapped
    text = text.replace("**", "").replace("__", "").replace("`", "")
    # An unbalanced wrapper leaves a single marker behind; drop it at the edges
    # rather than letting it surface in the UI.
    text = text.strip().strip("*_").strip()
    return re.sub(r"\s+", " ", text).strip()


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
                sitting_id=chunk.sitting_id,
                record_id=chunk.record_id,
            )
        )

    return citations


def parse_recommendation_lines(
    block: str,
    *,
    limit: int,
    default_reason: str = DEFAULT_RECOMMENDATION_REASON,
    allow_bare_lines: bool = False,
) -> list[RecommendationItem]:
    """Parse `text | reason` lines out of a recommendation block.

    The single parser for every recommendation the model emits, whether the
    block came from the `RECOMMENDATIONS:` section of an answer or from a
    dedicated search-recommendation call. Sharing it is what keeps the two
    paths from drifting: previously the search path scraped `**bold**` markers
    with its own regex in the gateway and silently dropped anything unbolded.

    Markdown emphasis is stripped rather than required. Models bold the term
    inconsistently, so treating `**budget allocation**` and `budget
    allocation` as the same input removes a whole class of empty-result bugs.

    Args:
        block: Raw text containing one recommendation per line.
        limit: Maximum items to return. Zero or negative yields an empty list.
        default_reason: Reason applied when a line omits the `| reason` part.
            Never empty, so a parsed item always has a non-empty reason.
        allow_bare_lines: When True, lines without a leading bullet or number
            are also accepted. Used by the search path, whose prompt asks for
            plain `query | reason` lines. Left False for answer parsing so
            trailing prose after the block cannot be mistaken for an item.

    Returns:
        At most `limit` RecommendationItem objects, each with non-empty `text`
        and non-empty `reason`, in the order they appeared.
    """
    if limit <= 0:
        return []

    pattern = _BARE_LINE_RE if allow_bare_lines else _BULLET_LINE_RE

    items: list[RecommendationItem] = []
    for line_match in pattern.finditer(block):
        text = _strip_emphasis(line_match.group(1))
        reason = _strip_emphasis(line_match.group(2) or "") or default_reason

        if not text:
            continue

        items.append(RecommendationItem(text=text, reason=reason))
        if len(items) == limit:
            break

    return items


def split_recommendations(
    raw_answer: str,
    *,
    limit: int = TARGET_SUGGESTION_COUNT,
) -> tuple[str, list[RecommendationItem]]:
    """Split the raw model response into answer text and recommendations.

    Looks for a RECOMMENDATIONS: section at the end of the response.
    Returns the answer text (without the recommendations section) and
    a list of parsed RecommendationItem objects.

    Args:
        raw_answer: The unmodified model response.
        limit: Maximum recommendations to return. Defaults to the same
            TARGET_SUGGESTION_COUNT the builder tops up to, so the parse cap
            and the assembly cap can no longer disagree.

    Returns:
        The answer text with the recommendations block removed, and at most
        `limit` parsed items.
    """
    match = _RECOMMENDATIONS_HEADING_RE.search(raw_answer)

    if not match:
        return raw_answer.strip(), []

    # Everything before the marker is the answer; everything after is the block.
    answer_text = raw_answer[: match.start()].strip()
    rec_block = raw_answer[match.end() :]

    return answer_text, parse_recommendation_lines(rec_block, limit=limit)


def message_text(message: Any) -> str:
    """Flatten a model message's content to text.

    A tool-calling model may return content as a list of blocks rather than a
    plain string, so handling only the string case would drop the answer.

    Shared by the conversational agent and the search-recommendation generator
    so both read model output the same way.
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif (
                isinstance(block, dict) and "text" in block and block.get("type") in (None, "text")
            ):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)
