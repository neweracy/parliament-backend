"""Evaluation_Corpus: labelled Negative_Set and Positive_Set fixtures (Req 10.1-10.3).

The Evaluation_Corpus is the labelled fixture set the Evaluation_Harness runs
the REAL Correction_Engine against to measure precision and recall (Baseline vs
gated). It is split into two labelled halves:

* **Negative_Set** (Req 10.2) — Spans of non-entity text the engine is expected
  to leave unchanged. At least 200 Spans, none of which also appear in the
  Positive_Set. It folds in the former Block_List tokens (Req 11.1) — the
  ordinary English words that must survive gating without the block list — plus
  additional ordinary parliamentary English.
* **Positive_Set** (Req 10.3) — Spans labelled with the canonical entity each
  should be corrected to. At least 100 Spans, and every labelled canonical
  entity is present in the fixture Dataset_Cache snapshot
  (:func:`tests.evaluation.dataset.fixture_canonicals`).

Each :class:`CorpusSpan` carries:

* ``text`` — the Span token(s) as they appear in the transcript;
* ``label`` — ``None`` for a Negative_Set Span, or the canonical entity name for
  a Positive_Set Span;
* ``enclosing_text`` — the transcript text the Span sits in (Req 10.1);
* ``words`` — the Word dicts covering the Span, carrying ``confidence`` where the
  provider supplied one and ``punctuated_word`` where supplied (Req 10.1);
* ``span_start`` — the index of the Span's first Word within ``words``;
* ``provider`` — the ASR provider tag (``deepgram`` for the confidence-bearing
  fixtures) so task 2.12.5 can split the report by provider.

Every fixture is synthesized parliamentary-style text with NO PII beyond the
public figure names already in the committed datasets. Nothing is generated at
random per run: the corpus is a deterministic function of committed seed data,
so :func:`load_evaluation_corpus` returns identical data on every call.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from tests.evaluation.blocklist_fixtures import load_former_blocklist_fixtures

# Default High_Confidence_Threshold (Req 12.3). Negative_Set confidence-bearing
# fixtures sit at or above it so the Confidence_Gate/Lexicon_Gate keep them out
# of approximate matching; Positive_Set fixtures sit below it so the gate lets
# the approximate strategies repair the mangled name.
_NEG_CONF = 0.97
_POS_CONF = 0.55


@dataclass(frozen=True)
class CorpusSpan:
    """One labelled Evaluation_Corpus Span (Req 10.1)."""

    text: str
    label: str | None  # None => Negative_Set; canonical name => Positive_Set
    enclosing_text: str
    words: tuple[dict, ...]
    span_start: int
    provider: str = "deepgram"


@dataclass(frozen=True)
class EvaluationCorpus:
    """The loaded Evaluation_Corpus split into its two labelled halves."""

    negative_set: tuple[CorpusSpan, ...]
    positive_set: tuple[CorpusSpan, ...]


# ---------------------------------------------------------------------------
# Deterministic Word / Span construction helpers
# ---------------------------------------------------------------------------


def _make_words(
    tokens: list[str],
    *,
    confidence: float | None,
    start_time: float = 0.0,
) -> list[dict]:
    """Build a deterministic Word-dict list for *tokens*.

    Timings are a fixed function of position (no randomness). ``confidence`` is
    attached to every Word when supplied. ``punctuated_word`` is the capitalised
    first token, matching Deepgram output.
    """
    words: list[dict] = []
    cursor = start_time
    for i, tok in enumerate(tokens):
        w: dict = {
            "word": tok,
            "start": round(cursor, 3),
            "end": round(cursor + 0.30, 3),
            "punctuated_word": tok.capitalize() if i == 0 else tok,
        }
        if confidence is not None:
            w["confidence"] = confidence
        cursor += 0.35
        words.append(w)
    return words


def _sentence(prefix: list[str], span_tokens: list[str], suffix: list[str]) -> str:
    """Join a synthesized parliamentary sentence around the Span tokens."""
    return " ".join(prefix + span_tokens + suffix)


# Synthesized parliamentary-style carrier phrases (no PII). Deterministic pool.
_NEG_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("the", "honourable", "member", "raised", "a"),
    ("we", "must", "address", "the"),
    ("this", "house", "considered", "the"),
    ("the", "committee", "reviewed", "the"),
    ("mister", "speaker", "on", "the"),
)
_NEG_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("matter", "before", "the", "house"),
    ("during", "the", "sitting"),
    ("in", "the", "chamber", "today"),
    ("as", "recorded", "in", "hansard"),
    ("and", "moved", "the", "motion"),
)


def _build_negative_set() -> list[CorpusSpan]:
    """Build the Negative_Set (Req 10.2): former Block_List tokens + more.

    Every Span is ordinary non-entity English embedded in a synthesized
    parliamentary sentence, labelled ``None``. The former Block_List tokens
    (Req 11.1) form the core; additional ordinary words pad the set past the
    200-Span floor (Req 10.2). Positions and timings are fixed functions of the
    index, so the set is identical on every run.
    """
    spans: list[CorpusSpan] = []
    blocklist = load_former_blocklist_fixtures()

    for i, fx in enumerate(blocklist):
        prefix = list(_NEG_PREFIXES[i % len(_NEG_PREFIXES)])
        suffix = list(_NEG_SUFFIXES[i % len(_NEG_SUFFIXES)])
        span_tokens = [fx.token]
        text = _sentence(prefix, span_tokens, suffix)
        all_tokens = prefix + span_tokens + suffix
        words = _make_words(all_tokens, confidence=fx.span_confidence)
        spans.append(
            CorpusSpan(
                text=fx.token,
                label=None,
                enclosing_text=text,
                words=tuple(words),
                span_start=len(prefix),
                provider="deepgram",
            )
        )

    # Additional ordinary non-entity English words to comfortably clear the
    # 200-Span Negative_Set floor (Req 10.2). Distinct from the block-list set.
    extra_negatives = [
        "welcome", "morning", "afternoon", "sitting", "record", "reading",
        "measure", "purpose", "process", "progress", "concern", "concerns",
        "matter", "matters", "response", "responses", "opinion", "opinions",
        "consideration", "understanding", "responsibility", "opportunity",
        "arrangement", "arrangements", "requirement", "requirements",
        "procedure", "procedures", "framework", "objective", "objectives",
        "outcome", "outcomes", "challenge", "challenges", "engagement",
        "engagements", "assessment", "assessments", "commitment",
        "commitments", "reference", "references", "guideline", "guidelines",
        "clarification", "clarifications", "explanation", "explanations",
        "presentation", "presentations", "observation", "observations",
        "consultation", "consultations", "implementation", "implementations",
        "regulation", "regulations", "obligation", "obligations", "provided",
        "providing", "regarding", "concerning", "following", "preceding",
        "accordingly", "therefore", "however", "moreover", "furthermore",
    ]
    offset = len(spans)
    for j, token in enumerate(extra_negatives):
        i = offset + j
        prefix = list(_NEG_PREFIXES[i % len(_NEG_PREFIXES)])
        suffix = list(_NEG_SUFFIXES[i % len(_NEG_SUFFIXES)])
        text = _sentence(prefix, [token], suffix)
        all_tokens = prefix + [token] + suffix
        words = _make_words(all_tokens, confidence=_NEG_CONF)
        spans.append(
            CorpusSpan(
                text=token,
                label=None,
                enclosing_text=text,
                words=tuple(words),
                span_start=len(prefix),
                provider="deepgram",
            )
        )

    return spans


# ---------------------------------------------------------------------------
# Positive_Set: mangled-spelling → canonical entity (present in the snapshot)
# ---------------------------------------------------------------------------
#
# (mangled_span_text, canonical_label). Every canonical is present in
# tests.evaluation.dataset.fixture_canonicals(). The mangled span is a low-
# confidence approximate form (alias, phonetic neighbour, or fuzzy neighbour)
# the gated engine is expected to repair to the canonical.

_POSITIVE_PAIRS: tuple[tuple[str, str], ...] = (
    # Locations — supplementary aliases / phonetic neighbours
    ("Gana", "Ghana"),
    ("Ghanna", "Ghana"),
    ("Ghanah", "Ghana"),
    ("Ningo Prampram", "Ningo-Prampram"),
    ("Ningoprampram", "Ningo-Prampram"),
    ("Nyungoprampram", "Ningo-Prampram"),
    ("Cabo Corso", "Cape Coast"),
    # Persons — aliases and fuzzy/phonetic neighbours
    ("Rawlings", "Jerry John Rawlings"),
    ("Jerry Rawlings", "Jerry John Rawlings"),
    ("Mahama", "John Dramani Mahama"),
    ("John Mahama", "John Dramani Mahama"),
    ("Dramani Mahama", "John Dramani Mahama"),
    ("Bagbin", "Alban Sumana Kingsford Bagbin"),
    ("Alban Bagbin", "Alban Sumana Kingsford Bagbin"),
    ("Segbefia", "Alex Segbefia"),
    ("Ato Forson", "Cassiel Ato Forson"),
    ("Forson", "Cassiel Ato Forson"),
    ("Cassiel Ato", "Cassiel Ato Forson"),
    ("Kyei-Mensah-Bonsu", "Osei Kyei-Mensah-Bonsu"),
    ("Sam George", "Samuel Nartey George"),
    ("Nartey George", "Samuel Nartey George"),
    ("Ablakwa", "Samuel Okudzeto Ablakwa"),
    ("Okudzeto Ablakwa", "Samuel Okudzeto Ablakwa"),
    ("Okudzeto", "Samuel Okudzeto Ablakwa"),
    ("Iddrisu", "Haruna Iddrisu"),
    ("Haruna", "Haruna Iddrisu"),
    ("Ofori-Atta", "Ken Ofori-Atta"),
    ("Ken Ofori Atta", "Ken Ofori-Atta"),
    ("Nikpe", "Joseph Bukari Nikpe"),
    ("Bukari Nikpe", "Joseph Bukari Nikpe"),
    ("Naa Momo", "Agnes Naa Momo Lartey"),
    ("Agnes Lartey", "Agnes Naa Momo Lartey"),
    # Parties — abbreviations (deterministic)
    ("NDC", "National Democratic Congress"),
    ("N.D.C.", "National Democratic Congress"),
    ("NPP", "New Patriotic Party"),
    ("N.P.P.", "New Patriotic Party"),
    ("PNC", "People's National Convention"),
)

# Context templates for Positive_Set spans. Several distinct carrier phrases per
# kind so the same (mangled, canonical) pair yields several fixtures — enough to
# clear the 100-Span Positive_Set floor (Req 10.3) without inventing labels.
# Person prefixes each supply a Context_Signal (Title_Prefix / "Member for") so
# the Context_Gate (Req 7) admits the approximate person correction.
_POS_PERSON_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("the", "honourable", "member"),
    ("mister", "speaker", "the", "honourable"),
    ("i", "yield", "to", "the", "honourable"),
)
_POS_LOC_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("the", "town", "of"),
    ("the", "people", "of"),
    ("the", "district", "of"),
)
_POS_PARTY_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("the", "party", "known", "as"),
    ("a", "member", "of", "the"),
    ("supporters", "of", "the"),
)
_POS_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("was", "mentioned", "in", "the", "debate"),
    ("addressed", "the", "house"),
    ("during", "the", "sitting", "today"),
)


def _positive_prefixes(canonical: str, kinds: dict[str, str]) -> tuple[tuple[str, ...], ...]:
    """Return the carrier-prefix pool appropriate to the entity kind."""
    kind = kinds.get(canonical, "location")
    if kind == "person":
        return _POS_PERSON_PREFIXES
    if kind == "party":
        return _POS_PARTY_PREFIXES
    return _POS_LOC_PREFIXES


def _build_positive_set() -> list[CorpusSpan]:
    """Build the Positive_Set (Req 10.3): mangled span → canonical entity.

    Each canonical is present in the fixture snapshot. Each (mangled, canonical)
    pair is expanded across three carrier templates so the set clears the
    100-Span floor deterministically. The Span carries a low-confidence Word
    (below High_Confidence_Threshold) so the gated engine admits the approximate
    repair; a Title_Prefix precedes person names to supply the Context_Signal
    (Req 7).
    """
    # Local import avoids a module import cycle at load time.
    from tests.evaluation.dataset import build_fixture_snapshot

    snapshot = build_fixture_snapshot()
    kinds = dict(snapshot.index.entity_kind_map)

    spans: list[CorpusSpan] = []
    for pair_idx, (mangled, canonical) in enumerate(_POSITIVE_PAIRS):
        prefixes = _positive_prefixes(canonical, kinds)
        span_tokens = mangled.split()
        for tmpl_idx in range(3):
            prefix = list(prefixes[tmpl_idx % len(prefixes)])
            suffix = list(_POS_SUFFIXES[(pair_idx + tmpl_idx) % len(_POS_SUFFIXES)])
            text = _sentence(prefix, span_tokens, suffix)
            all_tokens = prefix + span_tokens + suffix
            words = _make_words(all_tokens, confidence=_POS_CONF)
            spans.append(
                CorpusSpan(
                    text=mangled,
                    label=canonical,
                    enclosing_text=text,
                    words=tuple(words),
                    span_start=len(prefix),
                    provider="deepgram",
                )
            )
    return spans


@lru_cache(maxsize=1)
def load_evaluation_corpus() -> EvaluationCorpus:
    """Load the labelled Evaluation_Corpus (Req 10.1-10.3).

    Returns the Negative_Set and Positive_Set. The result is cached and built
    from committed seed data with no randomness, so every call returns identical
    data. The Negative_Set holds >= 200 Spans (including the former Block_List
    tokens, Req 11.1), the Positive_Set holds >= 100 Spans each labelled with a
    canonical entity present in the fixture snapshot (Req 10.3), and no
    Negative_Set Span text also appears as a Positive_Set Span text (Req 10.2).
    """
    negatives = _build_negative_set()
    positives = _build_positive_set()

    # Enforce Negative/Positive disjointness by Span text (Req 10.2): drop any
    # negative whose text also appears as a positive Span text.
    positive_texts = {p.text.lower() for p in positives}
    negatives = [n for n in negatives if n.text.lower() not in positive_texts]

    return EvaluationCorpus(
        negative_set=tuple(negatives),
        positive_set=tuple(positives),
    )
