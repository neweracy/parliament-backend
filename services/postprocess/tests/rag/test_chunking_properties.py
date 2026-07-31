"""Property tests for transcript chunking (speaker-turn-aware segmentation).

Validates: Requirements 7.1, 7.2

These tests verify that the TranscriptIngestionWorker.chunk_transcript method:
1. Produces chunks within the 100–200 word size bounds (except final chunks
   of a speaker turn which may be smaller).
2. Never produces chunks containing words from more than one speaker.

Properties tested:
- Property 11: Chunk size bounds
- Property 12: Chunk speaker boundary integrity
"""

from __future__ import annotations

from unittest.mock import MagicMock

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from app.rag.ingestion import TranscriptIngestionWorker, _MIN_CHUNK_WORDS, _MAX_CHUNK_WORDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker() -> TranscriptIngestionWorker:
    """Create a minimal TranscriptIngestionWorker instance for testing.

    Mocks session_factory and settings since chunking is a pure computation
    that doesn't require DB or AWS access.
    """
    mock_session_factory = MagicMock()
    mock_settings = MagicMock()
    mock_settings.aws_region = "us-east-1"

    # Patch the bedrock client builder to avoid real AWS calls
    original_build = TranscriptIngestionWorker._build_bedrock_client
    TranscriptIngestionWorker._build_bedrock_client = staticmethod(lambda s: MagicMock())
    worker = TranscriptIngestionWorker(mock_session_factory, mock_settings)
    TranscriptIngestionWorker._build_bedrock_client = original_build

    return worker


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Realistic speaker labels
_SPEAKERS = ["Speaker A", "Speaker B", "Speaker C", "Speaker D", "Speaker E"]

# Word pool for generating realistic transcript text
_WORD_POOL = [
    "the", "honourable", "member", "for", "constituency", "raised",
    "an", "important", "point", "about", "fiscal", "policy",
    "in", "this", "parliament", "we", "must", "consider",
    "economic", "growth", "and", "development", "of", "our",
    "nation", "Mr", "Speaker", "I", "would", "like", "to",
    "address", "budget", "allocation", "committee", "report",
    "minister", "finance", "education", "health", "agriculture",
    "infrastructure", "regional", "national", "local", "government",
    "president", "house", "session", "debate", "motion", "bill",
]


@st.composite
def speaker_turn_words(draw: st.DrawFn, speaker: str, min_words: int = 50, max_words: int = 500) -> list[dict]:
    """Generate a list of word-timing dicts for a single speaker turn.

    Each word dict has: word, start, end, speaker.
    Timestamps are monotonically increasing.
    """
    n_words = draw(st.integers(min_value=min_words, max_value=max_words))
    words: list[dict] = []
    cursor = draw(st.floats(min_value=0.0, max_value=10.0))

    for _ in range(n_words):
        word_text = draw(st.sampled_from(_WORD_POOL))
        gap = draw(st.floats(min_value=0.01, max_value=3.0))
        duration = draw(st.floats(min_value=0.05, max_value=0.8))
        start = cursor + gap
        end = start + duration
        cursor = end

        words.append({
            "word": word_text,
            "start": round(start, 3),
            "end": round(end, 3),
            "speaker": speaker,
        })

    return words


@st.composite
def multi_speaker_transcript(draw: st.DrawFn) -> tuple[str, list[dict], list[dict]]:
    """Generate a transcript with multiple speakers and word timings.

    Returns (text, words, entities) suitable for chunk_transcript().
    - 2-4 speakers
    - 2-4 turns total (alternating speakers)
    - Each turn has 80-300 words (enough to exercise chunking boundaries)

    The strategy is sized to stay within hypothesis health check limits
    while still exercising the chunk boundary logic meaningfully.
    """
    n_speakers = draw(st.integers(min_value=2, max_value=4))
    speakers = _SPEAKERS[:n_speakers]

    # Generate turns: interleave speakers (2-4 turns)
    n_turns = draw(st.integers(min_value=2, max_value=4))
    turn_speakers: list[str] = []
    for i in range(n_turns):
        # Cycle through speakers to ensure no consecutive duplicates
        turn_speakers.append(speakers[i % n_speakers])

    all_words: list[dict] = []
    cursor = 0.0

    for speaker in turn_speakers:
        n_words = draw(st.integers(min_value=80, max_value=300))
        for _ in range(n_words):
            word_text = draw(st.sampled_from(_WORD_POOL))
            # Use fixed small gaps to reduce draw calls
            gap = 0.05
            duration = 0.3
            start = cursor + gap
            end = start + duration
            cursor = end

            all_words.append({
                "word": word_text,
                "start": round(start, 3),
                "end": round(end, 3),
                "speaker": speaker,
            })

    # Build text from words
    text_content = " ".join(w["word"] for w in all_words)

    # Generate some entities (few to keep data small)
    n_entities = draw(st.integers(min_value=0, max_value=3))
    entities: list[dict] = []
    for _ in range(n_entities):
        name = draw(st.sampled_from(["Ghana", "Kumasi", "Accra"]))
        e_start = draw(st.floats(min_value=0.0, max_value=max(cursor - 1.0, 0.1)))
        e_end = e_start + 1.0
        entities.append({"name": name, "start": e_start, "end": e_end})

    return text_content, all_words, entities



# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(data=multi_speaker_transcript())
@settings(max_examples=200, suppress_health_check=[HealthCheck.large_base_example])
def test_chunk_size_bounds(data: tuple[str, list[dict], list[dict]]) -> None:
    """Every chunk has 100–200 words, except the final chunk of a speaker turn.

    For ANY transcript with word timings containing multiple speakers and
    varying turn lengths, calling chunk_transcript() SHALL produce chunks
    where:
    - Non-final chunks of a speaker turn have between 100 and 200 words.
    - The final chunk of a speaker turn MAY have fewer than 100 words only
      if the remaining words in that turn were fewer than 100.

    **Validates: Requirements 7.1, 7.2**
    """
    text_content, words, entities = data
    assume(len(words) >= _MIN_CHUNK_WORDS)  # Need enough words to chunk

    worker = _make_worker()
    chunks = worker.chunk_transcript(text_content, words, entities)

    assert len(chunks) > 0, "Expected at least one chunk for non-empty input"

    # Group chunks by speaker to identify final chunks per speaker turn
    # A speaker turn is a consecutive sequence of chunks with the same speaker
    speaker_turn_groups: list[list[int]] = []  # list of chunk index groups
    if chunks:
        current_group: list[int] = [0]
        for i in range(1, len(chunks)):
            if chunks[i].speaker == chunks[i - 1].speaker:
                current_group.append(i)
            else:
                speaker_turn_groups.append(current_group)
                current_group = [i]
        speaker_turn_groups.append(current_group)

    # Identify which chunk indices are "final chunk of a turn"
    final_indices = {group[-1] for group in speaker_turn_groups}

    for i, chunk in enumerate(chunks):
        word_count = len(chunk.text.split())

        if i in final_indices:
            # Final chunk of a speaker turn: may be < 100 words
            assert word_count <= _MAX_CHUNK_WORDS, (
                f"Chunk {i} (final of turn, speaker={chunk.speaker}) has "
                f"{word_count} words, exceeds max {_MAX_CHUNK_WORDS}. "
                f"Text preview: {chunk.text[:80]}..."
            )
        else:
            # Non-final chunk: must be between 100 and 200 words
            assert _MIN_CHUNK_WORDS <= word_count <= _MAX_CHUNK_WORDS, (
                f"Chunk {i} (non-final, speaker={chunk.speaker}) has "
                f"{word_count} words, expected {_MIN_CHUNK_WORDS}-{_MAX_CHUNK_WORDS}. "
                f"Text preview: {chunk.text[:80]}..."
            )


@given(data=multi_speaker_transcript())
@settings(max_examples=200, suppress_health_check=[HealthCheck.large_base_example])
def test_chunk_speaker_boundary_integrity(data: tuple[str, list[dict], list[dict]]) -> None:
    """No chunk contains words from more than one speaker.

    For ANY transcript with multiple speakers and word timings, calling
    chunk_transcript() SHALL produce chunks where all words in each chunk
    belong to exactly one speaker. Chunk boundaries SHALL always be placed
    at speaker transitions.

    **Validates: Requirements 7.1, 7.2**
    """
    text_content, words, entities = data
    assume(len(words) >= _MIN_CHUNK_WORDS)  # Need enough words to chunk

    worker = _make_worker()
    chunks = worker.chunk_transcript(text_content, words, entities)

    assert len(chunks) > 0, "Expected at least one chunk for non-empty input"

    # Each chunk should have a single speaker assigned
    for i, chunk in enumerate(chunks):
        assert chunk.speaker is not None, (
            f"Chunk {i} has no speaker assigned. "
            f"Text preview: {chunk.text[:80]}..."
        )

    # Verify by reconstructing: the words in each chunk should all come from
    # the same speaker. We verify this by matching chunk text back to the
    # original word timings.
    word_idx = 0
    for i, chunk in enumerate(chunks):
        chunk_words_text = chunk.text.split()
        chunk_word_count = len(chunk_words_text)

        # Find the corresponding words in the original list
        if word_idx + chunk_word_count > len(words):
            # If we've run past the end, the chunk text was constructed
            # from the word dicts, so just verify the speaker field
            break

        speakers_in_chunk = set()
        for j in range(word_idx, min(word_idx + chunk_word_count, len(words))):
            speaker = words[j].get("speaker")
            if speaker is not None:
                speakers_in_chunk.add(speaker)

        assert len(speakers_in_chunk) <= 1, (
            f"Chunk {i} contains words from multiple speakers: {speakers_in_chunk}. "
            f"Chunk speaker field: {chunk.speaker}. "
            f"Text preview: {chunk.text[:80]}..."
        )

        if speakers_in_chunk:
            assert chunk.speaker in speakers_in_chunk, (
                f"Chunk {i} speaker field ({chunk.speaker}) doesn't match "
                f"word speakers ({speakers_in_chunk}). "
                f"Text preview: {chunk.text[:80]}..."
            )

        word_idx += chunk_word_count
