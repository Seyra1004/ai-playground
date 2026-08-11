"""TEST Y, AC, AD, AG from the Phase 2B test matrix: run-finalization
failure is never swallowed, logs carry run/source context without ever
leaking a secret, and run_category_status is never touched by this phase."""

import logging
import sqlite3
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.orchestrator import run_daily_ingestion
from ingestion.records import AdapterOutcome
from ingestion.registry import RetryPolicy, SourceConfig


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _cfg(name, category="AI_NEWS", enabled=True):
    return SourceConfig(
        source_name=name, enabled=enabled, source_type="rss", category=category,
        region="GLOBAL", endpoint=f"https://example.com/{name}.xml", timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )


# ---- TEST Y: run finalization DB error is not hidden -------------------------


def test_Y_finalization_failure_propagates_not_swallowed(conn):
    registry = {"a": _cfg("a")}
    # A real DB-level failure (not a mock) for the UPDATE in finalize_run:
    # a BEFORE UPDATE trigger that always aborts. sqlite3.Connection.execute
    # is a read-only C-level attribute and can't be monkeypatched directly.
    conn.execute(
        "CREATE TRIGGER block_run_finalize BEFORE UPDATE ON runs "
        "BEGIN SELECT RAISE(ABORT, 'simulated finalize failure'); END;"
    )
    conn.commit()

    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        with pytest.raises(sqlite3.IntegrityError):
            run_daily_ingestion(conn, registry, "run-y")


# ---- TEST AC: logs carry run_id / source_name / category / outcome context --


def test_AC_logs_contain_run_and_source_context(conn, caplog):
    registry = {"a": _cfg("a")}
    with caplog.at_level(logging.INFO):
        with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
            run_daily_ingestion(conn, registry, "run-ac")
    messages = [r.getMessage() for r in caplog.records]
    assert any("run-ac" in m for m in messages)
    assert any("source=a" in m for m in messages)
    assert any("category=AI_NEWS" in m for m in messages)


# ---- TEST AD: fake secrets never appear in logs/status/error ----------------


def test_AD_fake_secret_never_appears_in_logs_or_status(conn, caplog):
    registry = {"naver_news": _cfg("naver_news")}
    secret = "fake_super_secret_value_12345"

    def leaking_adapter(source_config, sleep=None):
        raise RuntimeError(f"auth failed for header Authorization: Bearer {secret}")

    with caplog.at_level(logging.ERROR):
        with patch("ingestion.pipeline.get_adapter", return_value=leaking_adapter):
            run_daily_ingestion(conn, registry, "run-ad")

    for record in caplog.records:
        assert secret not in record.getMessage()

    row = conn.execute(
        "SELECT failure_reason FROM run_source_status WHERE source_name='naver_news'"
    ).fetchone()
    assert secret not in (row["failure_reason"] or "")


# ---- TEST AG: run_category_status is never written by Phase 2B --------------


def test_AG_run_category_status_untouched(conn):
    registry = {"a": _cfg("a")}
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        run_daily_ingestion(conn, registry, "run-ag")
    count = conn.execute("SELECT COUNT(*) FROM run_category_status").fetchone()[0]
    assert count == 0
