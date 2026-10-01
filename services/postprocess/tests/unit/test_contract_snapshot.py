"""Response contract-snapshot (golden-file) drift test (task 6.3.3).

This is the committed-SNAPSHOT sibling of ``test_contract_compat.py`` (task
6.7). Where 6.7 asserts a hand-listed set of Baseline keys inline, this file
pins the whole stable shape of the Correction_Response into a committed golden
artifact — ``tests/unit/contract_snapshot.json`` — so that any accidental
break of the Node.js/frontend contract shows up as a visible diff of that file
plus a failing test, not as a silent runtime surprise downstream.

What it protects (Req 16.14): for every response model the CorrectionResponse
emits — CorrectionResponse (top level), CorrectedWord, EntitySummary, Metadata,
and CorrectionRecord — the snapshot records each Baseline field's JSON key (its
camelCase alias where one is declared, else the field name) and its JSON value
type. The test FAILS on any rename, removal, retype, or alias change of a
Baseline key. Additive new keys (Metadata.gateRejections and Metadata.vetoes,
introduced by this spec) are ALLOWED: each model carries an
``additive_allowlist`` and a live key that is not in the Baseline must appear in
that allowlist, otherwise the test fails (so a genuinely unexpected new key is
still caught). This is exactly the asymmetry Req 16.14 asks for — additive is
fine, mutation of a Baseline key is not.

Two complementary halves:

1. Declaration-level drift (``TestContractDeclarationDrift``): derives the live
   ``{json_key: json_type}`` map straight from each model's ``model_fields`` and
   compares it to the committed Baseline. This does not depend on
   ``exclude_none`` and therefore pins every declared field, including the
   optional flags/counters that a given request omits.

2. Runtime emitted-keys (``TestContractRuntimeEmission``): builds ONE
   representative request, runs it through ``run_pipeline`` (``bedrock_client``,
   ``settings``, ``session_factory`` all ``None``; a mocked Dataset_Cache), and
   serialises with ``model_dump(by_alias=True, exclude_none=True)`` — the exact
   call the HTTP handler uses. It then asserts (Req 14.2) that every emitted key
   that is a Baseline key carries the snapshot's JSON type, and (Req 14.3) that
   every ``corrections[].strategy`` value is one of the allowed Match_Strategy
   values.

The representative request — how it was chosen (so a maintainer can regenerate
the snapshot intentionally):

* transcript ``"ningo prampram was mentioned"`` with the two-token alias
  ``"ningo prampram"`` present in the shared property fixture
  (``tests/property/fixtures.py``) resolving to the canonical
  ``"Ningo-Prampram"`` via an exact two-Word -> one-Word merge. This guarantees
  ``corrections`` is non-empty (so the CorrectionRecord keys are exercised),
  ``entities`` is non-empty (so the EntitySummary keys are exercised), and a
  corrected Word carries the ``locationCorrected``/``entityKind``/``entityType``
  flags (so those CorrectedWord aliases are exercised).
* The two merged Words carry ``confidence`` + a ``punctuated_word`` provider
  extra + a ``speaker`` provider extra, so the confidence field and the
  provider-passthrough path are both populated.
* ``correlation_id`` is set so ``metadata.correlationId`` is emitted; the fixture
  snapshot's ``version`` populates ``metadata.dataset_version``; the rule stage
  populates ``rule_latency_ms``/``llm_latency_ms``; ``location_corrections`` is
  populated by the applied location correction.
* ``options.llm_refine`` is False so no Bedrock call is attempted (the suite
  makes no network/AWS call).

Because ``exclude_none`` omits absent optional fields, the emitted key set for
this request is the set of Baseline keys that are populated for it. The
declaration-level half (1) covers the remaining optional Baseline keys
(``bedrockCorrected``, ``yearCorrected``, ``year_corrections``,
``bedrock_corrections``) that this particular request does not populate.

To regenerate the snapshot intentionally after a deliberate contract change:
update ``contract_snapshot.json`` to match the new model declarations (moving a
newly-added key into the relevant model's ``additive_allowlist`` when it is
purely additive), and confirm this test passes.

Runs with no Bedrock and no database. No network or AWS call is made.

Validates: Requirements 16.14, 14.2, 14.3
"""

from __future__ import annotations

import asyncio
import json
import types
import typing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.datasets.cache import DatasetCache, DatasetSnapshot
from app.datasets.index import build_index
from app.models.entities import (
    CorrectionRecord,
    EntityKind,
    EntityRecord,
    EntityType,
    MatchStrategy,
)
from app.models.request import CorrectionOptions, CorrectionRequest, Word
from app.models.response import (
    CorrectedWord,
    CorrectionResponse,
    EntitySummary,
    Metadata,
)
from app.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Snapshot artifact
# ---------------------------------------------------------------------------

SNAPSHOT_PATH = Path(__file__).with_name("contract_snapshot.json")

# The response models the snapshot pins, keyed by the name used in the snapshot.
_MODELS = {
    "CorrectionResponse": CorrectionResponse,
    "CorrectedWord": CorrectedWord,
    "EntitySummary": EntitySummary,
    "Metadata": Metadata,
    "CorrectionRecord": CorrectionRecord,
}


def _load_snapshot() -> dict:
    with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Python annotation -> JSON type mapping
# ---------------------------------------------------------------------------


def _unwrap_optional(annotation: object) -> object:
    """Strip ``None``/``Optional`` from a union annotation, returning the core type.

    ``str | None`` -> ``str``; ``int | None`` -> ``int``; a plain type is
    returned unchanged. A multi-member union without ``None`` is returned as-is
    (the caller maps it to ``None`` -> handled as unknown).
    """
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _json_type_of(annotation: object) -> str | None:
    """Map a Pydantic field annotation to the JSON type it serialises to.

    Returns one of ``"string"``, ``"number"``, ``"integer"``, ``"boolean"``,
    ``"array"``, ``"object"`` — or ``None`` when the annotation is not one this
    contract expects (which surfaces as a drift failure).
    """
    core = _unwrap_optional(annotation)
    origin = typing.get_origin(core)

    # Container annotations: list[...] -> array.
    if origin in (list, tuple, set, frozenset):
        return "array"

    if not isinstance(core, type):
        return None

    # bool must be checked before int (bool is a subclass of int).
    if issubclass(core, bool):
        return "boolean"
    if issubclass(core, int) and not issubclass(core, bool):
        # StrEnum subclasses str, not int; plain int -> integer.
        return "integer"
    if issubclass(core, float):
        return "number"
    # StrEnum (MatchStrategy, EntityKind, EntityType) serialises to a string.
    if issubclass(core, str):
        return "string"
    # Nested Pydantic models (Metadata) -> object.
    from pydantic import BaseModel

    if issubclass(core, BaseModel):
        return "object"
    return None


def _live_model_keys(model: type) -> dict[str, str]:
    """Return the live ``{json_key: json_type}`` map for a response model.

    ``json_key`` is the field's declared alias where one exists, else the field
    name — matching what ``model_dump(by_alias=True)`` emits.
    """
    result: dict[str, str] = {}
    for name, field in model.model_fields.items():
        json_key = field.alias or name
        result[json_key] = _json_type_of(field.annotation)
    return result


# ---------------------------------------------------------------------------
# Representative request + pipeline run (shared across the runtime tests)
# ---------------------------------------------------------------------------


def _representative_snapshot() -> DatasetSnapshot:
    """Snapshot with the ``ningo prampram`` -> ``Ningo-Prampram`` merge alias.

    A minimal hand-built snapshot (independent of the property fixture import
    surface) carrying the one entity the representative request needs, so the
    request produces at least one correction and a populated entity summary.
    """
    records = [
        EntityRecord(
            canonical="Ningo-Prampram",
            entity_kind=EntityKind.location,
            entity_type=EntityType.supplementary,
            aliases=["ningoprampram", "ningo prampram"],
            source="supplementary",
            source_rank=0,
        ),
    ]
    index = build_index(records)
    return DatasetSnapshot(
        version="contract-snapshot-fixture-v1",
        records=tuple(records),
        record_count=len(records),
        loaded_at=datetime.now(UTC),
        index=index,
        block_list=frozenset(),
        stopwords=frozenset(["was"]),
        word_stopwords=frozenset(["was"]),
        title_prefixes=frozenset(),
    )


def _representative_request() -> CorrectionRequest:
    """The representative Correction_Request (see module docstring for rationale).

    Rich enough that every populated Baseline key is exercised: a two-Word merge
    producing a correction + entity + corrected-Word flags, provider extras on
    the merged Words, and a correlation id.
    """
    return CorrectionRequest(
        transcript="ningo prampram was mentioned",
        words=[
            Word.model_validate(
                {
                    "word": "ningo",
                    "start": 0.0,
                    "end": 0.4,
                    "confidence": 0.5,
                    "punctuated_word": "Ningo",
                    "speaker": 1,
                }
            ),
            Word.model_validate(
                {
                    "word": "prampram",
                    "start": 0.4,
                    "end": 0.8,
                    "confidence": 0.5,
                    "punctuated_word": "Prampram.",
                    "speaker": 1,
                }
            ),
            Word(word="was", start=0.8, end=0.9, confidence=0.99),
            Word(word="mentioned", start=0.9, end=1.2, confidence=0.99),
        ],
        options=CorrectionOptions(llm_refine=False),
        correlation_id="contract-snap-1",
    )


def _run_representative() -> dict:
    """Run the representative request and return its by-alias, exclude-none dump."""
    cache = MagicMock(spec=DatasetCache)
    cache.get_snapshot.return_value = _representative_snapshot()
    response = asyncio.run(
        run_pipeline(
            _representative_request(),
            cache,
            bedrock_client=None,
            settings=None,
            session_factory=None,
        )
    )
    return response.model_dump(by_alias=True, exclude_none=True)


# JSON runtime-value -> contract type. ``integer`` and ``number`` are both
# acceptable for a numeric Baseline field: a value like ``0.0`` may serialise as
# a Python float even where the declared type is int-like, so a numeric field is
# satisfied by any real number and a "number" field is not satisfied by a bool.
def _runtime_type_matches(value: object, expected: str) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


# ---------------------------------------------------------------------------
# 1. Declaration-level drift: the live models vs the committed golden file
# ---------------------------------------------------------------------------


class TestContractDeclarationDrift:
    """Req 16.14: the live model declarations must match the committed snapshot.

    Fails on any rename, removal, retype, or alias change of a Baseline key.
    Additive live keys are allowed only when listed in the model's
    ``additive_allowlist``.
    """

    def test_snapshot_file_exists_and_parses(self):
        assert SNAPSHOT_PATH.is_file(), f"missing golden snapshot {SNAPSHOT_PATH}"
        snapshot = _load_snapshot()
        assert set(snapshot["models"]) == set(_MODELS), (
            "snapshot models set drifted from the response model set"
        )

    @pytest.mark.parametrize("model_name", sorted(_MODELS))
    def test_baseline_keys_present_unrenamed_and_correctly_typed(self, model_name):
        """Every Baseline key still exists as a live key with the same JSON type."""
        snapshot = _load_snapshot()
        model_snap = snapshot["models"][model_name]
        live = _live_model_keys(_MODELS[model_name])

        for py_field, spec in model_snap["baseline"].items():
            json_key = spec["json_key"]
            expected_type = spec["type"]
            # Present (not removed) and not renamed: the alias/json_key still
            # resolves to a live field.
            assert json_key in live, (
                f"{model_name}: Baseline key {json_key!r} (field {py_field!r}) "
                f"was renamed or removed"
            )
            # Not retyped and alias unchanged (json_key IS the alias).
            assert live[json_key] == expected_type, (
                f"{model_name}: Baseline key {json_key!r} retyped: "
                f"snapshot={expected_type!r} live={live[json_key]!r}"
            )

    @pytest.mark.parametrize("model_name", sorted(_MODELS))
    def test_no_unexpected_new_keys_outside_the_additive_allowlist(self, model_name):
        """A live key not in the Baseline must be in the additive allowlist."""
        snapshot = _load_snapshot()
        model_snap = snapshot["models"][model_name]
        baseline_json_keys = {
            spec["json_key"] for spec in model_snap["baseline"].values()
        }
        allowlist = set(model_snap.get("additive_allowlist", []))
        live = _live_model_keys(_MODELS[model_name])

        for json_key in live:
            if json_key in baseline_json_keys:
                continue
            assert json_key in allowlist, (
                f"{model_name}: unexpected new contract key {json_key!r} — if this "
                f"is a deliberate additive field, add it to the model's "
                f"additive_allowlist in contract_snapshot.json"
            )

    def test_additive_allowlist_keys_are_actually_present_live(self):
        """Allowlisted additive keys must exist on the live model (no stale entries)."""
        snapshot = _load_snapshot()
        for model_name, model_snap in snapshot["models"].items():
            live = _live_model_keys(_MODELS[model_name])
            for json_key in model_snap.get("additive_allowlist", []):
                assert json_key in live, (
                    f"{model_name}: allowlisted additive key {json_key!r} is not on "
                    f"the live model — remove the stale allowlist entry"
                )

    def test_match_strategy_set_matches_snapshot(self):
        """Req 14.3: the committed Match_Strategy set matches the live enum."""
        snapshot = _load_snapshot()
        assert set(snapshot["match_strategies"]) == {s.value for s in MatchStrategy}


# ---------------------------------------------------------------------------
# 2. Runtime emission: the serialised representative response vs the snapshot
# ---------------------------------------------------------------------------


class TestContractRuntimeEmission:
    """Req 14.2 / 14.3: the emitted representative response honours the snapshot."""

    def test_representative_request_produces_correction_and_entity(self):
        """Guards the fixture: the representative request must exercise every model."""
        dumped = _run_representative()
        assert dumped["corrections"], "representative request produced no corrections"
        assert dumped["entities"], "representative request produced no entities"
        # A corrected Word carries the flag/kind/type aliases.
        corrected = next(
            (w for w in dumped["words"] if w.get("word") == "Ningo-Prampram"), None
        )
        assert corrected is not None
        assert corrected.get("locationCorrected") is True
        assert corrected.get("entityKind") == "location"

    def test_top_level_emitted_keys_match_snapshot_types(self):
        dumped = _run_representative()
        model_snap = _load_snapshot()["models"]["CorrectionResponse"]["baseline"]
        by_json_key = {spec["json_key"]: spec["type"] for spec in model_snap.values()}
        for key, value in dumped.items():
            if key in by_json_key:
                assert _runtime_type_matches(value, by_json_key[key]), (
                    f"top-level {key!r} emitted {type(value).__name__}, "
                    f"contract expects {by_json_key[key]!r}"
                )
        # Every top-level Baseline key is actually emitted for this request.
        for json_key in by_json_key:
            assert json_key in dumped, f"top-level Baseline key {json_key!r} not emitted"

    def test_emitted_metadata_keys_match_snapshot_types(self):
        dumped = _run_representative()
        model_snap = _load_snapshot()["models"]["Metadata"]
        baseline = {
            spec["json_key"]: spec["type"] for spec in model_snap["baseline"].values()
        }
        allowlist = set(model_snap["additive_allowlist"])
        meta = dumped["metadata"]
        for key, value in meta.items():
            if key in baseline:
                assert _runtime_type_matches(value, baseline[key]), (
                    f"metadata[{key!r}] emitted {type(value).__name__}, "
                    f"contract expects {baseline[key]!r}"
                )
            else:
                # Any non-Baseline emitted key must be an allowed additive key.
                assert key in allowlist, (
                    f"metadata emitted unexpected key {key!r} outside the "
                    f"additive allowlist"
                )

    def test_emitted_word_and_correction_keys_match_snapshot_types(self):
        dumped = _run_representative()
        snapshot = _load_snapshot()["models"]

        word_baseline = {
            spec["json_key"]: spec["type"]
            for spec in snapshot["CorrectedWord"]["baseline"].values()
        }
        for word in dumped["words"]:
            for key, value in word.items():
                # Provider extras (punctuated_word, speaker) are not Baseline
                # contract keys; only assert types for keys we pin.
                if key in word_baseline:
                    assert _runtime_type_matches(value, word_baseline[key]), (
                        f"word[{key!r}] emitted {type(value).__name__}, "
                        f"contract expects {word_baseline[key]!r}"
                    )

        rec_baseline = {
            spec["json_key"]: spec["type"]
            for spec in snapshot["CorrectionRecord"]["baseline"].values()
        }
        for rec in dumped["corrections"]:
            # Every Baseline CorrectionRecord key is emitted (no exclude_none
            # omission — all are required, non-None fields).
            for json_key, expected in rec_baseline.items():
                assert json_key in rec, (
                    f"correction record missing Baseline key {json_key!r}"
                )
                assert _runtime_type_matches(rec[json_key], expected), (
                    f"correction[{json_key!r}] emitted {type(rec[json_key]).__name__}, "
                    f"contract expects {expected!r}"
                )

        entity_baseline = {
            spec["json_key"]: spec["type"]
            for spec in snapshot["EntitySummary"]["baseline"].values()
        }
        for ent in dumped["entities"]:
            for json_key, expected in entity_baseline.items():
                assert json_key in ent, f"entity missing Baseline key {json_key!r}"
                assert _runtime_type_matches(ent[json_key], expected), (
                    f"entity[{json_key!r}] emitted {type(ent[json_key]).__name__}, "
                    f"contract expects {expected!r}"
                )

    def test_correction_strategy_values_are_allowed_match_strategies(self):
        """Req 14.3: every corrections[].strategy is an allowed Match_Strategy."""
        dumped = _run_representative()
        allowed = {s.value for s in MatchStrategy}
        assert dumped["corrections"], "expected at least one correction"
        for rec in dumped["corrections"]:
            assert rec["strategy"] in allowed, (
                f"correction strategy {rec['strategy']!r} not in the allowed "
                f"Match_Strategy set {sorted(allowed)}"
            )
