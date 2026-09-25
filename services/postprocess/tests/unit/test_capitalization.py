"""Unit tests for app/correction/capitalization.py — Capitalization_Prior + Punctuated_Word.

Covers Requirement 6:
- word_cap_prior: read punctuated_word; Uppercase/Lowercase from the first cased
  letter; Unknown when absent/non-string/no cased letter, sentence-initial
  (first Word), or preceded by terminal .?! or an absent prior punctuated_word
  (Req 6.1, 6.2, 6.3).
- span_cap_prior: Uppercase only when every covered Word is Uppercase; Unknown
  for an empty Words list (Req 6.4, 6.5).
- merged_punctuated_word / replaced_punctuated_word: corrected text plus
  inherited terminal .?!; None when no source Word carried a punctuated_word
  string (Req 6.7, 6.8, 6.9).
- Engine wiring: correct_words rewrites punctuated_word on merge/replace only
  when the GateContext is non-inert, and passes uncorrected Words through
  verbatim (Req 6.6); the inert (flag-off) path is Baseline (Req 12.9).
"""

from datetime import UTC, datetime

from app.correction.capitalization import (
    CapPrior,
    merged_punctuated_word,
    replaced_punctuated_word,
    span_cap_prior,
    word_cap_prior,
)
from app.correction.engine import correct_words
from app.correction.gates import GateConfig, GateContext
from app.datasets.cache import DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import EntityKind, EntityRecord, EntityType


def _w(word: str, punctuated: object = ..., **extra) -> dict:
    """A Word dict; pass ``punctuated=...`` (default) to omit the field."""
    d: dict = {"word": word, "start": 0.0, "end": 0.1, **extra}
    if punctuated is not ...:
        d["punctuated_word"] = punctuated
    return d


# ---------------------------------------------------------------------------
# word_cap_prior — Req 6.1, 6.2, 6.3
# ---------------------------------------------------------------------------


class TestWordCapPrior:
    def test_uppercase_first_letter(self):
        # Req 6.1: uppercase first cased letter, not sentence-initial.
        prev = _w("said", "said")
        assert word_cap_prior(_w("Tema", "Tema"), prev, False) is CapPrior.UPPERCASE

    def test_lowercase_first_letter(self):
        # Req 6.1: lowercase first cased letter.
        prev = _w("the", "the")
        assert word_cap_prior(_w("district", "district"), prev, False) is CapPrior.LOWERCASE

    def test_leading_punctuation_then_uppercase(self):
        # First *cased* letter drives the prior, past leading punctuation.
        prev = _w("said", "said")
        assert word_cap_prior(_w("Tema", '"Tema'), prev, False) is CapPrior.UPPERCASE

    def test_field_absent_is_unknown(self):
        # Req 6.2: no punctuated_word string.
        prev = _w("said", "said")
        assert word_cap_prior(_w("tema"), prev, False) is CapPrior.UNKNOWN

    def test_non_string_field_is_unknown(self):
        # Req 6.2: non-string punctuated_word.
        prev = _w("said", "said")
        assert word_cap_prior(_w("tema", 123), prev, False) is CapPrior.UNKNOWN

    def test_no_cased_letter_is_unknown(self):
        # Req 6.2: string with no cased letter (digits/punctuation only).
        prev = _w("said", "said")
        assert word_cap_prior(_w("2024", "2024."), prev, False) is CapPrior.UNKNOWN

    def test_first_word_is_unknown_even_if_uppercase(self):
        # Req 6.3: sentence-initial (first Word) discounts capitalization.
        assert word_cap_prior(_w("Tema", "Tema"), None, True) is CapPrior.UNKNOWN

    def test_after_terminal_period_is_unknown(self):
        # Req 6.3: previous punctuated_word ends a sentence.
        prev = _w("home", "home.")
        assert word_cap_prior(_w("Tema", "Tema"), prev, False) is CapPrior.UNKNOWN

    def test_after_terminal_question_mark_is_unknown(self):
        prev = _w("what", "What?")
        assert word_cap_prior(_w("Tema", "Tema"), prev, False) is CapPrior.UNKNOWN

    def test_after_terminal_exclamation_is_unknown(self):
        prev = _w("stop", "Stop!")
        assert word_cap_prior(_w("Tema", "Tema"), prev, False) is CapPrior.UNKNOWN

    def test_prev_word_absent_punctuated_is_unknown(self):
        # Req 6.3: previous Word carries no punctuated_word string → no boundary
        # info → treat as sentence-initial → Unknown.
        prev = _w("said")  # no punctuated_word
        assert word_cap_prior(_w("Tema", "Tema"), prev, False) is CapPrior.UNKNOWN

    def test_mid_sentence_after_non_terminal_prev(self):
        # A comma-terminated previous Word is NOT a sentence boundary.
        prev = _w("said", "said,")
        assert word_cap_prior(_w("Tema", "Tema"), prev, False) is CapPrior.UPPERCASE


# ---------------------------------------------------------------------------
# span_cap_prior — Req 6.4, 6.5
# ---------------------------------------------------------------------------


class TestSpanCapPrior:
    def test_empty_span_is_unknown(self):
        # Req 6.5.
        assert span_cap_prior([], is_first=False) is CapPrior.UNKNOWN

    def test_all_uppercase_span_is_uppercase(self):
        # Req 6.4: every covered Word Uppercase, mid-sentence (a non-terminal
        # preceding Word so the Span's first Word is not sentence-initial).
        prev = _w("with", "with")
        words = [_w("Ama", "Ama"), _w("Sarpong", "Sarpong")]
        assert span_cap_prior(words, is_first=False, prev_word=prev) is CapPrior.UPPERCASE

    def test_all_uppercase_span_unknown_without_prev(self):
        # Req 6.3: with no preceding Word supplied the Span's first Word is
        # treated as sentence-initial (conservative default) → Unknown.
        words = [_w("Ama", "Ama"), _w("Sarpong", "Sarpong")]
        assert span_cap_prior(words, is_first=False) is CapPrior.UNKNOWN

    def test_mixed_case_span_is_unknown(self):
        # Req 6.4: any non-Uppercase covered Word → Unknown.
        words = [_w("Ama", "Ama"), _w("sarpong", "sarpong")]
        assert span_cap_prior(words, is_first=False) is CapPrior.UNKNOWN

    def test_span_first_word_sentence_initial_is_unknown(self):
        # Req 6.3 propagated: the Span leads the whole list, so its first Word
        # is discounted to Unknown, dragging the Span to Unknown.
        words = [_w("Ama", "Ama"), _w("Sarpong", "Sarpong")]
        assert span_cap_prior(words, is_first=True) is CapPrior.UNKNOWN

    def test_single_uppercase_word_span(self):
        prev = _w("in", "in")
        assert span_cap_prior([_w("Tema", "Tema")], is_first=False, prev_word=prev) is (
            CapPrior.UPPERCASE
        )

    def test_span_word_missing_punctuated_is_unknown(self):
        prev = _w("with", "with")
        words = [_w("Ama", "Ama"), _w("sarpong")]
        assert span_cap_prior(words, is_first=False, prev_word=prev) is CapPrior.UNKNOWN


# ---------------------------------------------------------------------------
# merged_punctuated_word / replaced_punctuated_word — Req 6.7, 6.8, 6.9
# ---------------------------------------------------------------------------


class TestMergedPunctuatedWord:
    def test_inherits_last_terminal_period(self):
        # Req 6.7: corrected text + last merged Word's terminal punctuation.
        merged = [_w("joseph", "Joseph"), _w("nikpe", "Nikpe.")]
        assert merged_punctuated_word("Joseph Bukari Nikpe", merged) == "Joseph Bukari Nikpe."

    def test_no_terminal_when_last_has_none(self):
        merged = [_w("joseph", "Joseph"), _w("nikpe", "Nikpe")]
        assert merged_punctuated_word("Joseph Bukari Nikpe", merged) == "Joseph Bukari Nikpe"

    def test_last_word_absent_but_earlier_present(self):
        # Req 6.7: at least one merged Word carried a string → field is set; the
        # terminal punctuation comes from the *last* merged Word (here: none).
        merged = [_w("joseph", "Joseph."), _w("nikpe")]
        assert merged_punctuated_word("Joseph Nikpe", merged) == "Joseph Nikpe"

    def test_none_when_no_word_carried_punctuated(self):
        # Req 6.9: no merged Word carried a punctuated_word string.
        merged = [_w("joseph"), _w("nikpe")]
        assert merged_punctuated_word("Joseph Nikpe", merged) is None


class TestReplacedPunctuatedWord:
    def test_inherits_prior_terminal_question(self):
        # Req 6.8: corrected text + the Word's prior terminal punctuation.
        assert replaced_punctuated_word("Sege", _w("sage", "sage?")) == "Sege?"

    def test_no_terminal_when_prior_has_none(self):
        assert replaced_punctuated_word("Sege", _w("sage", "sage")) == "Sege"

    def test_none_when_word_absent_punctuated(self):
        # Req 6.9.
        assert replaced_punctuated_word("Sege", _w("sage")) is None

    def test_none_when_non_string_punctuated(self):
        assert replaced_punctuated_word("Sege", _w("sage", 5)) is None


# ---------------------------------------------------------------------------
# Engine wiring: correct_words punctuated_word passthrough/merge/replace
# ---------------------------------------------------------------------------


def _env():
    """Index + snapshot: a single-token alias ("sege") and a two-token merge
    ("pram pram" -> "Prampram")."""
    records = [
        EntityRecord(
            canonical="Sege",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["sege"],
            source="supplementary",
            source_rank=0,
        ),
        EntityRecord(
            canonical="Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.supplementary,
            aliases=["pram pram"],
            source="supplementary",
            source_rank=0,
        ),
    ]
    index = build_index(records)
    snapshot = DatasetSnapshot(
        version="2026-01-01T00:00:00Z",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(),
        word_stopwords=frozenset(),
        title_prefixes=frozenset(),
    )
    return index, snapshot


def _active() -> GateContext:
    """A non-inert context (one flag on) so Req 6.7/6.8/6.9 apply."""
    return GateContext(config=GateConfig(evidence_confidence_enabled=True))


class TestCorrectWordsPunctuatedPassthrough:
    """Req 6.6: uncorrected Words return punctuated_word verbatim (both paths)."""

    def test_uncorrected_word_verbatim_inert(self):
        index, snapshot = _env()
        words = [_w("hello", "Hello,", confidence=0.99, speaker=1)]
        result = correct_words(words, index, snapshot)
        assert result.words[0]["punctuated_word"] == "Hello,"
        assert result.words[0]["speaker"] == 1

    def test_uncorrected_word_verbatim_active(self):
        index, snapshot = _env()
        words = [_w("hello", "Hello,", confidence=0.99)]
        result = correct_words(words, index, snapshot, gate_context=_active())
        assert result.words[0]["punctuated_word"] == "Hello,"


class TestCorrectWordsReplacePunctuated:
    """Req 6.8, 6.9: single-Word replacement punctuated_word semantics."""

    def test_replacement_rewrites_with_terminal(self):
        # "sege" (alias) → "Sege"; punctuated becomes corrected text + terminal.
        index, snapshot = _env()
        words = [_w("sege", "sege.")]
        result = correct_words(words, index, snapshot, gate_context=_active())
        assert result.words[0]["word"] == "Sege"
        assert result.words[0]["punctuated_word"] == "Sege."

    def test_replacement_omits_when_source_absent(self):
        # Req 6.9: source Word carried no punctuated_word → field omitted.
        index, snapshot = _env()
        words = [_w("sege")]
        result = correct_words(words, index, snapshot, gate_context=_active())
        assert result.words[0]["word"] == "Sege"
        assert "punctuated_word" not in result.words[0]

    def test_inert_keeps_stale_punctuated_baseline(self):
        # Req 12.9: with every flag off the corrected Word keeps the source
        # Word's punctuated_word verbatim (Baseline behaviour), unrewritten.
        index, snapshot = _env()
        words = [_w("sege", "sege.")]
        result = correct_words(words, index, snapshot)
        assert result.words[0]["word"] == "Sege"
        assert result.words[0]["punctuated_word"] == "sege."


class TestCorrectWordsMergePunctuated:
    """Req 6.7, 6.9: multi-Word merge punctuated_word semantics."""

    def test_merge_rewrites_with_last_terminal(self):
        # "pram pram" → "Prampram" merge; punctuated = corrected + last terminal.
        index, snapshot = _env()
        words = [_w("pram", "Pram"), _w("pram", "Pram.")]
        result = correct_words(words, index, snapshot, gate_context=_active())
        assert result.words[0]["word"] == "Prampram"
        assert result.words[0]["punctuated_word"] == "Prampram."

    def test_merge_omits_when_none_carried(self):
        # Req 6.9: no merged Word carried a punctuated_word → field omitted.
        index, snapshot = _env()
        words = [_w("pram"), _w("pram")]
        result = correct_words(words, index, snapshot, gate_context=_active())
        assert result.words[0]["word"] == "Prampram"
        assert "punctuated_word" not in result.words[0]

    def test_inert_merge_keeps_first_word_punctuated_baseline(self):
        # Req 12.9: flag-off merge inherits the first source Word's
        # punctuated_word (Baseline), not the rewritten value.
        index, snapshot = _env()
        words = [_w("pram", "Pram"), _w("pram", "Pram.")]
        result = correct_words(words, index, snapshot)
        assert result.words[0]["word"] == "Prampram"
        assert result.words[0]["punctuated_word"] == "Pram"
