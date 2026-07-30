"""LCS-based alignment for LLM refinement token application.

Provides two alignment strategies:
1. Direct alignment (apply_aligned) — when original and refined token counts match.
2. LCS-based alignment (apply_aligned_with_map) — when token counts differ, uses
   longest common subsequence anchoring to safely apply changes only where alignment
   is unambiguous.

Both strategies preserve the Word count (never add or remove Words) and set only
``bedrockCorrected: True`` on changed entries.

Requirements: 11.3, 11.9
"""

from __future__ import annotations


def lcs_pairs(original: list[str], refined: list[str]) -> list[tuple[int, int]]:
    """Compute the LCS between two token lists on lowercased text.

    Returns a list of (original_idx, refined_idx) pairs representing positions
    where tokens match (case-insensitive). These pairs serve as "anchor points"
    for alignment.

    Uses the standard O(n*m) DP approach — fine for chunks of ~300 tokens.
    """
    n = len(original)
    m = len(refined)

    a = [w.lower() for w in original]
    b = [w.lower() for w in refined]

    # Build DP table
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Walk back to recover aligned pairs
    i, j = n, m
    pairs: list[tuple[int, int]] = []
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1

    pairs.reverse()
    return pairs


def apply_aligned(words: list[dict], refined_tokens: list[str]) -> list[dict]:
    """Direct alignment when original and refined token counts are equal.

    Compares word-by-word: if refined[i] differs from original[i], updates the
    word text and sets ``bedrockCorrected: True``. Uses the token_map built by
    the chunker to group consecutive tokens belonging to the same Word entry.

    Only the first token position of a multi-token entry may write — if a word
    was already merged (contains spaces), all its tokens must be collected and
    compared as a group.

    Args:
        words: The master words list (mutated in place). Each entry is a dict
               with at least a ``word`` key.
        refined_tokens: Whitespace-split tokens from the Bedrock response,
                       same count as the original tokens.

    Returns:
        The mutated words list (same reference).
    """
    # Build a token map: for each word entry, determine how many whitespace
    # tokens it contributes and map token positions back to word indices.
    token_map: list[int] = []
    for idx, w in enumerate(words):
        token_count = len(w.get("word", "").split()) or 1
        for _ in range(token_count):
            token_map.append(idx)

    # Build original tokens from the words
    original_tokens: list[str] = []
    for w in words:
        original_tokens.extend(w.get("word", "").split())

    # If the refined tokens don't match the total token count, bail safely
    if len(refined_tokens) != len(original_tokens):
        return words

    # Apply corrections grouped by word entry
    token_idx = 0
    while token_idx < len(refined_tokens):
        word_idx = token_map[token_idx]
        # Collect all tokens belonging to this word entry
        end_token = token_idx
        while (end_token + 1 < len(refined_tokens)
               and token_map[end_token + 1] == word_idx):
            end_token += 1

        original_slice = " ".join(original_tokens[token_idx:end_token + 1])
        corrected_slice = " ".join(refined_tokens[token_idx:end_token + 1])

        if word_idx < len(words) and corrected_slice != original_slice:
            words[word_idx] = {
                **words[word_idx],
                "word": corrected_slice,
                "bedrockCorrected": True,
            }

        token_idx = end_token + 1

    return words


def apply_aligned_with_map(
    words: list[dict],
    refined_tokens: list[str],
    token_map: list[int],
) -> list[dict]:
    """LCS-based alignment when token counts differ.

    Uses ``lcs_pairs`` to find anchor positions between the original word tokens
    and the refined tokens. Changes are applied:
    - At each anchor pair where the refined token differs from the original.
    - Inside equal-sized gaps between consecutive anchor pairs (safe 1:1 mapping).

    Unequal-sized gaps are skipped — can't safely align them without risking
    misalignment of subsequent words.

    Only the first token position of a multi-token entry may write, preventing
    partial replacements of merged words.

    Args:
        words: The master words list (mutated in place).
        refined_tokens: Whitespace-split tokens from the Bedrock response.
        token_map: Maps each original token position to a words[] index.

    Returns:
        The mutated words list (same reference).
    """
    # Build original tokens from the words covered by the token_map
    original_tokens: list[str] = []
    for w in words:
        original_tokens.extend(w.get("word", "").split())

    # Trim original_tokens to the length of token_map (handles chunk boundaries)
    original_tokens = original_tokens[:len(token_map)]

    n = len(original_tokens)
    m = len(refined_tokens)

    # Get anchor pairs via LCS
    pairs = lcs_pairs(original_tokens, refined_tokens)

    # Apply corrections at anchor points
    for oi, ci in pairs:
        if oi < len(token_map):
            word_idx = token_map[oi]
            if word_idx < len(words) and original_tokens[oi] != refined_tokens[ci]:
                # For multi-token entries, only write at the first token position
                entry_start = token_map.index(word_idx)
                entry_end = len(token_map) - 1 - token_map[::-1].index(word_idx)
                if entry_start == oi and entry_end == oi:
                    # Single-token entry — simple replacement
                    words[word_idx] = {
                        **words[word_idx],
                        "word": refined_tokens[ci],
                        "bedrockCorrected": True,
                    }
                # Multi-token entries at anchors: only first token may write
                elif oi == entry_start:
                    words[word_idx] = {
                        **words[word_idx],
                        "word": refined_tokens[ci],
                        "bedrockCorrected": True,
                    }

    # Handle same-sized gaps between anchors (word-for-word corrections)
    boundaries = list(pairs) + [(n, m)]
    prev_orig = -1
    prev_corr = -1

    for oi, ci in boundaries:
        orig_gap = oi - prev_orig - 1
        corr_gap = ci - prev_corr - 1

        if orig_gap > 0 and orig_gap == corr_gap:
            # Same-sized gap — safe to treat as word-for-word correction
            for k in range(orig_gap):
                orig_idx = prev_orig + 1 + k
                corr_idx = prev_corr + 1 + k
                if orig_idx < len(token_map):
                    word_idx = token_map[orig_idx]
                    if (word_idx < len(words)
                            and original_tokens[orig_idx] != refined_tokens[corr_idx]):
                        # Only the first token of a multi-token entry may write
                        entry_start = token_map.index(word_idx)
                        if orig_idx == entry_start:
                            words[word_idx] = {
                                **words[word_idx],
                                "word": refined_tokens[corr_idx],
                                "bedrockCorrected": True,
                            }

        # Gaps of different sizes mean an insertion/deletion happened —
        # skip that span rather than risk misaligning subsequent words.
        prev_orig = oi
        prev_corr = ci

    return words
