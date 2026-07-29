"""Block_List, stopword, word-stopword, and title-prefix accessors.

Provides simple set-membership checks against the frozen sets stored on
the ``DatasetSnapshot``. All checks are case-insensitive (the token is
lowercased before lookup).

Usage context within the Correction_Engine:

- **Block_List**: tokens the engine is forbidden from treating as entity
  candidates. Checked inside ``match_fuzzy`` only (Requirement 4.1).
- **Stopwords**: filtered at the top of ``correct_single`` before any
  strategy runs.
- **Word-stopwords**: used in ``correct_words`` for join-window rejection.
- **Title prefixes**: trigger the title-person path in the engine
  (Requirement 3.6).

Requirements: 4.1, 4.10
"""

from __future__ import annotations

from app.datasets.cache import DatasetSnapshot


def is_blocked(token: str, snapshot: DatasetSnapshot) -> bool:
    """Return True if the lowercased token is in the Block_List.

    Block_List entries prevent the Correction_Engine from matching a
    common word to an entity name (e.g. "general", "page", "national").
    """
    return token.lower() in snapshot.block_list


def is_stopword(token: str, snapshot: DatasetSnapshot) -> bool:
    """Return True if the lowercased token is in the stopwords set.

    Stopwords are skipped entirely at the top of ``correct_single`` —
    they never enter the strategy chain.
    """
    return token.lower() in snapshot.stopwords


def is_word_stopword(token: str, snapshot: DatasetSnapshot) -> bool:
    """Return True if the lowercased token is in the word-stopwords set.

    Word-stopwords cause join-window rejection in ``correct_words`` —
    an n-gram window containing one of these tokens is not considered
    for multi-token joining.
    """
    return token.lower() in snapshot.word_stopwords


def is_title(token: str, snapshot: DatasetSnapshot) -> bool:
    """Return True if the lowercased token is in the title-prefixes set.

    Title prefixes (e.g. "honorable", "honourable", "minister") trigger
    the title-person matching path, which looks for person names in the
    following tokens.
    """
    return token.lower() in snapshot.title_prefixes
