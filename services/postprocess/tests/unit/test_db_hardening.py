"""Tests for the production-hardening changes to the database layer.

Covers engine configuration, URL normalization, readiness semantics, and the
correction-history retention/overflow behaviour.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from app.datasets.store import make_engine, normalize_database_url
from app.history.writer import CorrectionHistoryWriter, HistoryRecord

DUMMY_URL = "postgresql://user:pass@localhost:5432/testdb"


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        # Already explicit — must be left alone.
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_normalize_database_url(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_normalize_preserves_query_parameters() -> None:
    """An sslmode already in the URL must survive normalization."""
    out = normalize_database_url("postgresql://u:p@h:5432/d?sslmode=require")
    assert out == "postgresql+psycopg://u:p@h:5432/d?sslmode=require"


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------


def test_engine_sets_recycle_and_timeouts() -> None:
    """Pool recycle and timeouts must be configured, not left at defaults."""
    engine = make_engine(DUMMY_URL)
    try:
        # SQLAlchemy exposes recycle on the pool.
        assert engine.pool._recycle == 1800
        assert engine.pool._timeout == 10
    finally:
        engine.sync_engine.dispose()


def _effective_connect_params(engine) -> dict:
    """Return the DBAPI connect params SQLAlchemy will actually use.

    ``connect_args`` are merged with the URL-derived params inside the pool's
    creator closure, so that closure is the only place the final, effective set
    is observable. Reading it beats asserting on what we passed in, because it
    proves the values survive SQLAlchemy's merge.
    """
    creator = engine.sync_engine.pool._creator
    return inspect.getclosurevars(creator).nonlocals["cparams"]


def test_engine_passes_connect_and_statement_timeouts() -> None:
    """connect_timeout and a server-side statement_timeout must be present."""
    engine = make_engine(DUMMY_URL, connect_timeout_seconds=7, statement_timeout_ms=1234)
    try:
        params = _effective_connect_params(engine)
        assert params["connect_timeout"] == 7
        assert "statement_timeout=1234" in params["options"]
    finally:
        engine.sync_engine.dispose()


def test_engine_injects_sslmode_when_requested() -> None:
    engine = make_engine(DUMMY_URL, sslmode="require")
    try:
        assert _effective_connect_params(engine).get("sslmode") == "require"
    finally:
        engine.sync_engine.dispose()


def test_engine_does_not_override_sslmode_already_in_url() -> None:
    """An explicit sslmode in DATABASE_URL must win over the setting."""
    engine = make_engine(
        "postgresql://u:p@h:5432/d?sslmode=verify-full", sslmode="require"
    )
    try:
        # psycopg surfaces the URL's sslmode; we must not have injected ours.
        params = _effective_connect_params(engine)
        assert params.get("sslmode") in (None, "verify-full")
    finally:
        engine.sync_engine.dispose()


def test_engine_pool_size_is_configurable() -> None:
    engine = make_engine(DUMMY_URL, pool_size=3, max_overflow=2)
    try:
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 2
    finally:
        engine.sync_engine.dispose()


# ---------------------------------------------------------------------------
# History writer — overflow visibility and retention
# ---------------------------------------------------------------------------


def _record() -> HistoryRecord:
    return HistoryRecord(
        correlation_id="c1",
        text_hash="h1",
        original="a",
        corrected="b",
        strategy="exact",
        confidence=0.9,
        entity_kind="location",
        entity_type="city",
        model_version="v1",
        created_at=datetime.now(timezone.utc),
    )


async def test_overflow_is_counted_and_never_raises() -> None:
    """A full queue drops records and counts them without touching the caller."""
    writer = CorrectionHistoryWriter(
        session_factory=None,  # type: ignore[arg-type] - not used by enqueue
        max_queue_size=2,
    )

    # Fill the queue, then overflow it. None of these may raise.
    for _ in range(5):
        writer.enqueue(_record())

    assert writer.dropped_count == 3


async def test_overflow_logs_at_warning_not_debug() -> None:
    """Dropped audit data must be visible to monitoring, so WARNING or above.

    Uses structlog's capture fixture rather than pytest's ``caplog``: the
    service logs through structlog, which does not necessarily route through the
    stdlib handlers ``caplog`` inspects.
    """
    import structlog

    writer = CorrectionHistoryWriter(
        session_factory=None,  # type: ignore[arg-type]
        max_queue_size=1,
    )

    with structlog.testing.capture_logs() as logs:
        writer.enqueue(_record())  # fits
        writer.enqueue(_record())  # overflows

    assert writer.dropped_count == 1

    overflow_events = [e for e in logs if e.get("event") == "history.queue_overflow"]
    assert overflow_events, f"no overflow event logged; got {logs}"
    # The whole point: this must not be debug, or nobody sees data loss.
    assert overflow_events[0]["log_level"] == "warning"


async def test_retention_disabled_skips_pruning() -> None:
    """retention_days=0 disables the sweeper entirely."""
    writer = CorrectionHistoryWriter(
        session_factory=None,  # type: ignore[arg-type]
        retention_days=0,
    )
    # Must short-circuit before touching the session factory (which is None).
    assert await writer._prune_once() == 0


async def test_retention_task_not_started_when_disabled() -> None:
    writer = CorrectionHistoryWriter(
        session_factory=None,  # type: ignore[arg-type]
        retention_days=0,
    )
    writer.start()
    try:
        assert writer._retention_task is None
    finally:
        await writer.stop()


async def test_retention_task_started_when_enabled() -> None:
    writer = CorrectionHistoryWriter(
        session_factory=None,  # type: ignore[arg-type]
        retention_days=30,
        retention_interval_seconds=3600,
    )
    writer.start()
    try:
        assert writer._retention_task is not None
    finally:
        await writer.stop()


async def test_prune_failure_is_swallowed() -> None:
    """A failing prune must not raise or kill the writer."""

    class BoomFactory:
        def __call__(self):
            raise RuntimeError("db gone")

    writer = CorrectionHistoryWriter(
        session_factory=BoomFactory(),  # type: ignore[arg-type]
        retention_days=30,
    )
    assert await writer._prune_once() == 0


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_settings_expose_db_and_retention_knobs() -> None:
    from app.config import Settings

    s = Settings(service_token="t", database_url=DUMMY_URL)
    assert s.db_pool_recycle_seconds > 0
    assert s.db_connect_timeout_seconds > 0
    assert s.db_statement_timeout_ms > 0
    assert s.db_pool_timeout_seconds > 0
    assert s.history_retention_days > 0
    # Unset by default so local/plaintext dev works; production sets it.
    assert s.db_sslmode is None


def test_invalid_numeric_db_setting_falls_back(monkeypatch) -> None:
    """A malformed numeric env var must fall back, not crash startup."""
    from app.config import Settings

    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "not-a-number")
    s = Settings(service_token="t", database_url=DUMMY_URL)
    assert s.db_pool_recycle_seconds == 1800
