"""Unit tests for app/correction/lexicon.py.

Covers the EnglishLexicon membership check (case- and punctuation-insensitive)
and load_lexicon's success and failure paths: the bundled artifact loads active
and meets the size floor, and absence / short / unreadable inputs return an
inactive lexicon.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.10, 2.11
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.correction.lexicon import (
    DEFAULT_LEXICON_PATH,
    MIN_LEXICON_SIZE,
    EnglishLexicon,
    load_lexicon,
)

# ---------------------------------------------------------------------------
# EnglishLexicon membership
# ---------------------------------------------------------------------------


def test_contains_is_case_insensitive() -> None:
    lex = EnglishLexicon(frozenset({"thank", "district"}), loaded=True)
    assert lex.contains("thank")
    assert lex.contains("Thank")
    assert lex.contains("THANK")
    assert not lex.contains("ghana")


def test_contains_strips_surrounding_punctuation() -> None:
    lex = EnglishLexicon(frozenset({"thank"}), loaded=True)
    assert lex.contains("thank,")
    assert lex.contains('"thank"')
    assert lex.contains("(thank)")
    assert lex.contains("thank.")


def test_contains_empty_after_strip_is_not_a_member() -> None:
    lex = EnglishLexicon(frozenset({"thank"}), loaded=True)
    assert not lex.contains(".")
    assert not lex.contains("")
    assert not lex.contains("  ")


def test_in_operator_matches_contains() -> None:
    lex = EnglishLexicon(frozenset({"later"}), loaded=True)
    assert "Later!" in lex
    assert "sege" not in lex


# ---------------------------------------------------------------------------
# load_lexicon — bundled artifact
# ---------------------------------------------------------------------------


def test_bundled_artifact_loads_active_and_meets_floor() -> None:
    lex = load_lexicon()
    assert lex.loaded is True
    assert len(lex) >= MIN_LEXICON_SIZE
    # Ordinary English words the gate must recognise (Req 2.4 inflections).
    for word in ("thank", "district", "later", "running", "walked", "fastest"):
        assert lex.contains(word), word


def test_bundled_artifact_path_exists() -> None:
    assert DEFAULT_LEXICON_PATH.is_file()


# ---------------------------------------------------------------------------
# load_lexicon — failure paths return an inactive lexicon (Req 2.11)
# ---------------------------------------------------------------------------


def test_absent_artifact_returns_inactive(tmp_path: Path) -> None:
    lex = load_lexicon(tmp_path / "does_not_exist.txt")
    assert lex.loaded is False
    assert len(lex) == 0
    # An inactive lexicon still answers membership without raising.
    assert not lex.contains("thank")


def test_short_artifact_returns_inactive(tmp_path: Path) -> None:
    artifact = tmp_path / "short.txt"
    artifact.write_text("\n".join(f"word{i}" for i in range(100)), encoding="utf-8")
    lex = load_lexicon(artifact)
    assert lex.loaded is False
    assert len(lex) == 0


def test_directory_path_returns_inactive(tmp_path: Path) -> None:
    # A directory is not a regular file → treated as absent (Req 2.11).
    lex = load_lexicon(tmp_path)
    assert lex.loaded is False


def test_loader_skips_blank_and_multitoken_lines(tmp_path: Path) -> None:
    artifact = tmp_path / "mixed.txt"
    lines = [f"word{i}" for i in range(MIN_LEXICON_SIZE)]
    # Interleave blanks and a whitespace-containing line that must be skipped.
    lines += ["", "  ", "two tokens"]
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    lex = load_lexicon(artifact)
    assert lex.loaded is True
    assert len(lex) == MIN_LEXICON_SIZE
    assert not lex.contains("two tokens")


def test_timeout_returns_inactive(tmp_path: Path) -> None:
    artifact = tmp_path / "words.txt"
    artifact.write_text(
        "\n".join(f"word{i}" for i in range(MIN_LEXICON_SIZE + 10)),
        encoding="utf-8",
    )
    # A zero-second budget forces the wall-clock guard to trip on the first
    # line, producing an inactive lexicon rather than raising.
    lex = load_lexicon(artifact, timeout_s=0.0)
    assert lex.loaded is False
    assert len(lex) == 0


@pytest.mark.parametrize("form", ["houses", "thinking", "faster", "played"])
def test_bundled_artifact_includes_inflected_forms(form: str) -> None:
    lex = load_lexicon()
    assert lex.contains(form)


# ---------------------------------------------------------------------------
# lexicon_gate_blocks_approximate — the Lexicon_Gate decision (Req 2.5-2.8, 2.13)
# ---------------------------------------------------------------------------

from app.correction.lexicon import lexicon_gate_blocks_approximate  # noqa: E402

_LEX = EnglishLexicon(frozenset({"thank", "district", "later", "member", "for"}), loaded=True)


class TestLexiconGateBlocksApproximate:
    """Pure Lexicon_Gate decision — Req 2.5, 2.6, 2.7, 2.8, 2.13."""

    def test_confident_single_lexicon_word_blocks(self):
        # Req 2.5: lexicon member, not an alias, conf >= override → reject.
        assert lexicon_gate_blocks_approximate(
            ["thank"], 0.95, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_at_override_threshold_blocks(self):
        # Req 2.5: >= override.
        assert lexicon_gate_blocks_approximate(
            ["thank"], 0.60, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_below_override_permits(self):
        # Req 2.7: known confidence strictly below override permits approximate.
        assert not lexicon_gate_blocks_approximate(
            ["thank"], 0.59, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_unknown_confidence_blocks(self):
        # Req 2.6: Unknown always gates a lexicon member absent from aliases.
        assert lexicon_gate_blocks_approximate(
            ["thank"], None, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_alias_never_gated(self):
        # Req 2.5, 2.8: a Span present in the alias set is never gated.
        assert not lexicon_gate_blocks_approximate(
            ["thank"], None, lexicon=_LEX, is_alias=True, override_threshold=0.60
        )

    def test_non_lexicon_word_permits(self):
        # Not an English word → gate does not apply (a mangled name).
        assert not lexicon_gate_blocks_approximate(
            ["sege"], 0.99, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_multitoken_all_members_blocks(self):
        # Req 2.8: multi-token gated only when every token is a lexicon member.
        assert lexicon_gate_blocks_approximate(
            ["member", "for"], None, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_multitoken_one_nonmember_permits(self):
        # Req 2.8: a non-member token frees the whole Span.
        assert not lexicon_gate_blocks_approximate(
            ["member", "tema"], None, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_none_lexicon_permits(self):
        # Req 2.13: no lexicon handle → gate inactive.
        assert not lexicon_gate_blocks_approximate(
            ["thank"], None, lexicon=None, is_alias=False, override_threshold=0.60
        )

    def test_unloaded_lexicon_permits(self):
        # Req 2.13: a lexicon that failed to load → gate inactive.
        inactive = EnglishLexicon(frozenset(), loaded=False)
        assert not lexicon_gate_blocks_approximate(
            ["thank"], None, lexicon=inactive, is_alias=False, override_threshold=0.60
        )

    def test_empty_tokens_permits(self):
        assert not lexicon_gate_blocks_approximate(
            [], None, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )

    def test_membership_is_punctuation_insensitive(self):
        # Req 2.10: tokens compared lowercased and stripped of edge punctuation.
        assert lexicon_gate_blocks_approximate(
            ["Thank,"], None, lexicon=_LEX, is_alias=False, override_threshold=0.60
        )


# ---------------------------------------------------------------------------
# Engine integration: correct_single Lexicon_Gate wiring (Req 2.5-2.9, 2.13, 12.9)
# ---------------------------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

from app.correction.engine import correct_single  # noqa: E402
from app.correction.gates import GateConfig, GateContext  # noqa: E402
from app.datasets.cache import DatasetSnapshot  # noqa: E402
from app.datasets.index import build_index  # noqa: E402
from app.models.entities import EntityKind, EntityRecord, EntityType  # noqa: E402


def _gate_env():
    """Index + snapshot with 'Sege' reachable approximately from 'sage'.

    'sage' is an ordinary English word; 'sege' is a mangled Ghanaian place
    name. The Lexicon_Gate must keep 'sage' out of approximate matching when
    the ASR was confident (Req 2.5).
    """
    records = [
        EntityRecord(
            canonical="Sege",
            entity_kind=EntityKind.location,
            entity_type=EntityType.city,
            aliases=["seged"],
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


def _lexicon_context(*, high_threshold: float = 0.90) -> GateContext:
    """A non-inert context with only the Lexicon_Gate flag on.

    High_Confidence_Threshold is raised out of the way so the Confidence_Gate
    never fires — isolating the Lexicon_Gate behaviour under test.
    """
    lex = EnglishLexicon(frozenset({"sage"}), loaded=True)
    return GateContext(
        config=GateConfig(
            lexicon_gate_enabled=True,
            high_confidence_threshold=high_threshold,
            lexicon_override_threshold=0.60,
        ),
        lexicon=lex,
    )


class TestCorrectSingleLexiconGate:
    """The Lexicon_Gate is wired into correct_single (Req 2.5-2.9, 2.13, 12.9)."""

    def test_confident_lexicon_word_blocks_approximate(self):
        # Req 2.5: confident lexicon word absent from aliases → reject approx.
        index, snapshot = _gate_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_lexicon_context(high_threshold=1.01), span_conf=0.95,
        )
        assert result is None

    def test_unknown_confidence_blocks_approximate(self):
        # Req 2.6: Unknown Span_Confidence gates a lexicon member.
        index, snapshot = _gate_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_lexicon_context(high_threshold=1.01), span_conf=None,
        )
        assert result is None

    def test_below_override_permits_approximate(self):
        # Req 2.7: known confidence below override permits approximate.
        index, snapshot = _gate_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_lexicon_context(high_threshold=1.01), span_conf=0.30,
        )
        assert result is not None
        assert result.canonical == "Sege"

    def test_deterministic_never_gated(self):
        # Req 2.9: a deterministic exact match applies even for a lexicon word.
        # Add 'sage' as a real alias so exact resolves; the gate must not block it.
        records = [
            EntityRecord(
                canonical="Sage Town",
                entity_kind=EntityKind.location,
                entity_type=EntityType.city,
                aliases=["sage"],
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
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=_lexicon_context(high_threshold=1.01), span_conf=0.99,
        )
        # 'sage' is an alias → exact match, gate does not apply (Req 2.5, 2.9).
        assert result is not None
        assert result.strategy == "exact"

    def test_flag_off_permits_approximate(self):
        # Req 2.13: with the lexicon flag off, approximate evaluation proceeds.
        index, snapshot = _gate_env()
        ctx = GateContext(
            config=GateConfig(evidence_confidence_enabled=True),  # some other flag on
            lexicon=EnglishLexicon(frozenset({"sage"}), loaded=True),
        )
        result = correct_single("sage", 1, index, snapshot, gate_context=ctx, span_conf=None)
        assert result is not None
        assert result.canonical == "Sege"

    def test_unloaded_lexicon_permits_approximate(self):
        # Req 2.13: lexicon failed to load → gate inactive.
        index, snapshot = _gate_env()
        ctx = GateContext(
            config=GateConfig(lexicon_gate_enabled=True, high_confidence_threshold=1.01),
            lexicon=EnglishLexicon(frozenset(), loaded=False),
        )
        result = correct_single("sage", 1, index, snapshot, gate_context=ctx, span_conf=None)
        assert result is not None
        assert result.canonical == "Sege"

    def test_inert_context_is_baseline(self):
        # Req 12.9: every flag off → gate inert, approximate chain runs.
        index, snapshot = _gate_env()
        result = correct_single(
            "sage", 1, index, snapshot,
            gate_context=GateContext.inert(), span_conf=0.99,
        )
        assert result is not None
        assert result.canonical == "Sege"
