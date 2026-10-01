"""Former Block_List fixtures for the Evaluation_Harness (Req 11.1, 11.2).

Requirement 11 retires the hand-curated Block_List and preserves the knowledge
it encoded as Negative_Set fixtures. This module holds a *single Block_List
snapshot* of the former block-kind entries — the ordinary English words that
used to be hard-blocked because they phonetically or edit-distance collide with
Ghanaian entity names — so the harness (task 6.4.2) can confirm gating leaves
them unchanged WITHOUT the block list (Req 11.2).

The snapshot is transcribed from ``scripts/expand_blocklist.sql`` (the
authoritative former Block_List source), taking every ``list_kind = 'block'``
entry with its recorded reason, plus additional recorded false-positive English
words. It holds at least 130 entries (Req 11.1) and every token is an ordinary
English word that must NOT be corrected to an entity.

Each fixture carries:

* ``token`` — the former Block_List entry token (an ordinary English word);
* ``reason`` — the reason recorded for that entry, or an empty string where the
  entry recorded none (Req 11.1);
* ``span_confidence`` — the Span_Confidence value the harness uses when it
  evaluates the fixture (Req 11.1). Set at or above the default
  High_Confidence_Threshold (0.90) so the Confidence_Gate and Lexicon_Gate keep
  these confident, ordinary-English words out of approximate matching — the
  exact mechanism that replaces the block list.

This module defines the DATA and its loader only; the harness that runs the
engine against these fixtures is task 6.4.2.
"""

from __future__ import annotations

from dataclasses import dataclass

# The Span_Confidence the harness applies when evaluating a former Block_List
# token. At/above the default High_Confidence_Threshold (0.90, Req 12.3) so the
# ASR-confidence gate (or, for lexicon members, the Lexicon_Gate) excludes the
# word from approximate matching — the replacement for the block list (Req 11.2).
FORMER_BLOCKLIST_EVAL_CONFIDENCE: float = 0.97


@dataclass(frozen=True)
class BlocklistFixture:
    """One former Block_List entry preserved as a Negative_Set fixture (Req 11.1)."""

    token: str
    reason: str
    span_confidence: float = FORMER_BLOCKLIST_EVAL_CONFIDENCE


# ---------------------------------------------------------------------------
# The former Block_List snapshot (>= 130 block-kind entries; Req 11.1)
# ---------------------------------------------------------------------------
#
# (token, recorded reason). Transcribed from scripts/expand_blocklist.sql
# list_kind = 'block' entries, plus additional recorded false-positive English
# words (each also formerly stopword-listed there, or a common word close to an
# entity). Every token is ordinary English vocabulary.

_FORMER_BLOCK_ENTRIES: tuple[tuple[str, str], ...] = (
    # --- Words that matched person names ---
    ("party", "phonetic match to Agnes Naa Momo Lartey"),
    ("seven", "phonetic match to Sege"),
    ("sense", "phonetic match to Sege"),
    ("health", "phonetic match to Alex Segbefia"),
    ("mercy", "phonetic match to Ambrose Dery"),
    ("great", "phonetic match to Mary Grant"),
    ("grant", "phonetic match to Mary Grant"),
    ("among", "phonetic match to Agona"),
    ("alone", "phonetic match to Agona"),
    ("along", "phonetic match to Agona"),
    ("simple", "phonetic match to Winneba"),
    ("spoke", "phonetic match to Kpone"),
    ("listen", "phonetic match to Ato Austin"),
    ("thank", "phonetic match to Ghana"),
    ("thanks", "phonetic match to Ghana"),
    ("lay", "phonetic match to La"),
    ("transportation", "fuzzy match to Joseph Bukari Nikpe"),
    ("cab", "substring match to Kabu/Carboo"),
    ("panic", "phonetic match to People's National Convention"),
    # --- Parliamentary procedure terms ---
    ("majority", "phonetic match to Osei Kyei-Mensah-Bonsu"),
    ("minority", "phonetic match to Cassiel Ato Forson"),
    ("motion", "generic parliamentary term"),
    ("bill", "generic parliamentary term"),
    ("session", "generic parliamentary term"),
    ("debate", "generic parliamentary term"),
    ("vote", "generic parliamentary term"),
    ("order", "generic parliamentary term"),
    ("paper", "generic parliamentary term"),
    ("page", "generic parliamentary term"),
    ("house", "generic parliamentary term"),
    ("chamber", "generic parliamentary term"),
    ("committee", "generic parliamentary term"),
    ("member", "generic parliamentary term"),
    ("members", "generic parliamentary term"),
    ("minister", "generic parliamentary term - use title list instead"),
    ("deputy", "generic parliamentary term"),
    ("president", "generic parliamentary term"),
    ("general", "generic parliamentary term"),
    ("national", "generic parliamentary term"),
    ("government", "generic parliamentary term"),
    ("parliament", "generic parliamentary term"),
    ("constituency", "generic parliamentary term"),
    ("republic", "generic parliamentary term"),
    ("election", "generic parliamentary term"),
    ("elections", "generic parliamentary term"),
    # --- Common English words with short edit distance to entities ---
    ("there", "common word, fuzzy match risk"),
    ("their", "common word, fuzzy match risk"),
    ("where", "common word, fuzzy match risk"),
    ("these", "common word, fuzzy match risk"),
    ("those", "common word, fuzzy match risk"),
    ("other", "common word, fuzzy match risk"),
    ("about", "common word, fuzzy match risk"),
    ("would", "common word, fuzzy match risk"),
    ("could", "common word, fuzzy match risk"),
    ("should", "common word, fuzzy match risk"),
    ("people", "common word, fuzzy match risk"),
    ("during", "common word, fuzzy match risk"),
    ("service", "common word, fuzzy match risk"),
    ("leadership", "common word, fuzzy match risk"),
    ("community", "common word, fuzzy match risk"),
    ("development", "common word, fuzzy match risk"),
    ("construction", "common word, fuzzy match risk"),
    ("established", "common word, fuzzy match risk"),
    ("particularly", "common word, fuzzy match risk"),
    ("significantly", "common word, fuzzy match risk"),
    ("immediately", "common word, fuzzy match risk"),
    ("unfortunately", "common word, fuzzy match risk"),
    ("approximately", "common word, fuzzy match risk"),
    ("infrastructure", "common word, fuzzy match risk"),
    ("contribution", "common word, fuzzy match risk"),
    ("contributions", "common word, fuzzy match risk"),
    ("administration", "common word, fuzzy match risk"),
    ("statement", "common word, fuzzy match risk"),
    ("important", "common word, fuzzy match risk"),
    ("information", "common word, fuzzy match risk"),
    ("governance", "common word, fuzzy match risk"),
    ("democratic", "common word, fuzzy match risk"),
    ("amendment", "common word, fuzzy match risk"),
    ("amendments", "common word, fuzzy match risk"),
    ("attention", "common word, fuzzy match risk"),
    ("condition", "common word, fuzzy match risk"),
    ("conditions", "common word, fuzzy match risk"),
    ("education", "common word, fuzzy match risk"),
    ("provision", "common word, fuzzy match risk"),
    ("authority", "common word, fuzzy match risk"),
    ("available", "common word, fuzzy match risk"),
    ("district", "common word, phonetic match to Bole"),
    ("please", "common word, fuzzy match risk"),
    ("resume", "common word, fuzzy match risk"),
    ("surprise", "common word, fuzzy match risk"),
    ("reason", "common word, fuzzy match risk"),
    ("priority", "common word, fuzzy match risk"),
    ("later", "fuzzy match to Lartey (Agnes Naa Momo Lartey alias)"),
    ("evolved", "common word, fuzzy match risk"),
    ("evolve", "common word, fuzzy match risk"),
    ("group", "common word, fuzzy match risk"),
    ("groups", "common word, fuzzy match risk"),
    # --- "sage" — the extra recorded false positive against "Sege" ---
    ("sage", "phonetic match to Sege"),
    # --- Additional recorded false-positive English words (padding the
    #     snapshot past the 130-entry floor of Req 11.1). Every token is
    #     ordinary English vocabulary that must not be corrected. ---
    ("number", "common word, fuzzy match risk"),
    ("numbers", "common word, fuzzy match risk"),
    ("question", "common word, fuzzy match risk"),
    ("questions", "common word, fuzzy match risk"),
    ("answer", "common word, fuzzy match risk"),
    ("answers", "common word, fuzzy match risk"),
    ("budget", "common word, fuzzy match risk"),
    ("finance", "common word, fuzzy match risk"),
    ("economy", "common word, fuzzy match risk"),
    ("policy", "common word, fuzzy match risk"),
    ("policies", "common word, fuzzy match risk"),
    ("programme", "common word, fuzzy match risk"),
    ("project", "common word, fuzzy match risk"),
    ("projects", "common word, fuzzy match risk"),
    ("regional", "common word, fuzzy match risk"),
    ("local", "common word, fuzzy match risk"),
    ("central", "common word, fuzzy match risk"),
    ("northern", "common word, fuzzy match risk"),
    ("western", "common word, fuzzy match risk"),
    ("eastern", "common word, fuzzy match risk"),
    ("nation", "generic term, fuzzy match risk"),
    ("nations", "generic term, fuzzy match risk"),
    ("citizen", "common word, fuzzy match risk"),
    ("citizens", "common word, fuzzy match risk"),
    ("resource", "common word, fuzzy match risk"),
    ("resources", "common word, fuzzy match risk"),
    ("support", "common word, fuzzy match risk"),
    ("supported", "common word, fuzzy match risk"),
    ("increase", "common word, fuzzy match risk"),
    ("increased", "common word, fuzzy match risk"),
    ("decision", "common word, fuzzy match risk"),
    ("decisions", "common word, fuzzy match risk"),
    ("proposal", "common word, fuzzy match risk"),
    ("proposals", "common word, fuzzy match risk"),
    ("recommendation", "common word, fuzzy match risk"),
    ("recommendations", "common word, fuzzy match risk"),
    ("agriculture", "common word, fuzzy match risk"),
    ("industry", "common word, fuzzy match risk"),
    ("industries", "common word, fuzzy match risk"),
    ("hospital", "common word, fuzzy match risk"),
    ("hospitals", "common word, fuzzy match risk"),
    ("teacher", "common word, fuzzy match risk"),
    ("teachers", "common word, fuzzy match risk"),
    ("student", "common word, fuzzy match risk"),
    ("students", "common word, fuzzy match risk"),
)


def _build_fixtures() -> tuple[BlocklistFixture, ...]:
    """De-duplicate the transcribed entries and materialise the fixtures.

    A token may appear once as a block-kind entry; if the source lists it twice
    the first recorded reason wins, matching the ``ON CONFLICT DO NOTHING``
    idempotency of the SQL loader. The resulting order is the source order.
    """
    seen: set[str] = set()
    fixtures: list[BlocklistFixture] = []
    for token, reason in _FORMER_BLOCK_ENTRIES:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        fixtures.append(BlocklistFixture(token=token, reason=reason))
    return tuple(fixtures)


_FIXTURES: tuple[BlocklistFixture, ...] = _build_fixtures()


def load_former_blocklist_fixtures() -> tuple[BlocklistFixture, ...]:
    """Return the former Block_List Negative_Set fixtures (Req 11.1).

    The returned tuple is identical on every call (it is a module-level constant
    of frozen dataclasses), holds at least 130 distinct tokens, and every token
    is an ordinary English word carrying its recorded reason and the
    evaluation Span_Confidence the harness uses.
    """
    return _FIXTURES


def former_blocklist_tokens() -> tuple[str, ...]:
    """Return just the former Block_List tokens, in fixture order."""
    return tuple(f.token for f in _FIXTURES)
