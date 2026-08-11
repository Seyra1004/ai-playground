"""TEST A, C, D, E, T, U, V from the Phase 2B test matrix: run creation
prerequisites, atomic start, orphan-row prevention, duplicate run_id
blocking, and the explicit non-existence of same-run resume."""

import sqlite3
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.orchestrator import (
    DuplicateRunIdError,
    NoEnabledSourcesError,
    run_daily_ingestion,
    start_run,
)
from ingestion.records import AdapterOutcome, IngestionRecord
from ingestion.registry import RetryPolicy, SourceConfig


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _registry(enabled=True, name="source_a"):
    cfg = SourceConfig(
        source_name=name,
        enabled=enabled,
        source_type="rss",
        category="AI_NEWS",
        region="GLOBAL",
        endpoint="https://example.com/feed.xml",
        timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )
    return {name: cfg}


def _empty_outcome_adapter(source_config, sleep=None):
    return AdapterOutcome(records=[])


# ---- TEST A: valid registry -> run created -----------------------------------


def test_A_valid_registry_creates_run(conn):
    registry = _registry()
    with patch("ingestion.pipeline.get_adapter", return_value=_empty_outcome_adapter):
        result = run_daily_ingestion(conn, registry, "run-a")
    row = conn.execute("SELECT run_id, status FROM runs WHERE run_id='run-a'").fetchone()
    assert row is not None
    assert row["status"] == "completed"
    assert result["runs_row_id"] is not None


# ---- TEST C: zero enabled sources -> no run created --------------------------


def test_C_zero_enabled_sources_blocks_run_creation(conn):
    registry = _registry(enabled=False)
    with pytest.raises(NoEnabledSourcesError):
        run_daily_ingestion(conn, registry, "run-c")
    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert count == 0


# ---- TEST D: runs + run_metadata are created atomically ---------------------


def test_D_start_run_creates_both_runs_and_metadata_atomically(conn):
    runs_row_id = start_run(conn, "run-d", "2026-08-12", "somehash")
    run_row = conn.execute("SELECT id FROM runs WHERE id=?", (runs_row_id,)).fetchone()
    meta_row = conn.execute("SELECT run_id, source_registry_hash FROM run_metadata WHERE run_id=?", (runs_row_id,)).fetchone()
    assert run_row is not None
    assert meta_row is not None
    assert meta_row["source_registry_hash"] == "somehash"


# ---- TEST E: run_metadata insert failure -> no orphan runs row --------------


def test_E_run_metadata_failure_leaves_no_orphan_runs_row(conn):
    conn.execute("DROP TABLE run_metadata")
    with pytest.raises(sqlite3.OperationalError):
        start_run(conn, "run-e", "2026-08-12", "somehash")
    count = conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-e'").fetchone()[0]
    assert count == 0


# ---- TEST T: duplicate business run_id -> second run blocked ----------------


def test_T_duplicate_run_id_blocked(conn):
    registry = _registry()
    with patch("ingestion.pipeline.get_adapter", return_value=_empty_outcome_adapter):
        run_daily_ingestion(conn, registry, "run-dup")
        with pytest.raises(DuplicateRunIdError):
            run_daily_ingestion(conn, registry, "run-dup")
    count = conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-dup'").fetchone()[0]
    assert count == 1  # second attempt never created a second row


# ---- TEST U: a new run_id after a previously FAILED run is allowed ----------


def test_U_new_run_after_failed_previous_run_is_allowed(conn):
    def failing_adapter(source_config, sleep=None):
        from ingestion.http import HttpTransientError
        raise HttpTransientError("boom")

    registry = _registry()
    with patch("ingestion.pipeline.get_adapter", return_value=failing_adapter):
        first = run_daily_ingestion(conn, registry, "run-first")
    assert first["status"] == "failed"

    with patch("ingestion.pipeline.get_adapter", return_value=_empty_outcome_adapter):
        second = run_daily_ingestion(conn, registry, "run-second")
    assert second["status"] == "completed"

    count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert count == 2


# ---- TEST V: same-run resume is not implemented; a run is never silently ----
# ---- re-executed under its own run_id ----------------------------------------


def test_V_no_same_run_resume_second_call_with_same_run_id_is_rejected_not_resumed(conn):
    call_count = {"n": 0}

    def counting_adapter(source_config, sleep=None):
        call_count["n"] += 1
        return AdapterOutcome(records=[IngestionRecord(
            source_name=source_config.source_name, source_item_key=f"k{call_count['n']}",
            source_type="rss", source_url="https://example.com/x",
            collected_at="2026-08-12T00:00:00+00:00",
        )])

    registry = _registry()
    with patch("ingestion.pipeline.get_adapter", return_value=counting_adapter):
        run_daily_ingestion(conn, registry, "run-resume-test")
        assert call_count["n"] == 1
        with pytest.raises(DuplicateRunIdError):
            run_daily_ingestion(conn, registry, "run-resume-test")
    # The adapter was never invoked a second time for the same run_id — a
    # blocked duplicate is not a silent resume/re-execution.
    assert call_count["n"] == 1
