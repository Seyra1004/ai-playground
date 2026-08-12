"""Normalization Wiring Fix: RAW -> NORMALIZED is now a required stage of
run_daily_ingestion(), not a silently-skipped afterthought. Tests cover
only the NEW wiring/invariants -- normalize_batch's own behavior (idempotency,
category resolution, event_key, etc.) is already exhaustively covered by
Phase 2C's test suite and is not re-tested here."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.http import HttpTransientError
from ingestion.orchestrator import run_daily_ingestion
from ingestion.records import AdapterOutcome, IngestionRecord
from ingestion.registry import RetryPolicy, SourceConfig


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _cfg(name, category="AI_NEWS"):
    return SourceConfig(
        source_name=name, enabled=True, source_type="rss", category=category,
        region="GLOBAL", endpoint=f"https://example.com/{name}.xml", timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )


def _record(source_name, key, title="A Title"):
    return IngestionRecord(
        source_name=source_name, source_item_key=key, source_type="rss",
        source_url=f"https://example.com/{key}", title=title,
        collected_at="2026-08-12T00:00:00+00:00",
    )


def _normalized_count(conn):
    return conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]


def _raw_count(conn):
    return conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]


# ---- A: successful RAW ingestion invokes normalization and persists rows ---


def test_A_successful_ingestion_invokes_normalization(conn):
    registry = {"a": _cfg("a")}
    outcome = AdapterOutcome(records=[_record("a", "k1"), _record("a", "k2")])
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: outcome):
        result = run_daily_ingestion(conn, registry, "run-a")
    assert result["status"] == "completed"
    assert _normalized_count(conn) == 2


# ---- B: partial source failure does not prevent normalizing the rest -------


def test_B_partial_source_failure_still_normalizes_successful_raw_rows(conn):
    registry = {"good": _cfg("good"), "bad": _cfg("bad", category="ECONOMY_NEWS")}

    def adapter(source_config, sleep=None):
        if source_config.source_name == "bad":
            raise HttpTransientError("boom")
        return AdapterOutcome(records=[_record("good", "k1")])

    with patch("ingestion.pipeline.get_adapter", return_value=adapter):
        result = run_daily_ingestion(conn, registry, "run-b")

    assert result["status"] == "completed"  # unchanged source-isolation semantics
    assert _normalized_count(conn) == 1


# ---- C: re-running does not create invalid duplicate normalized rows -------


def test_C_rerun_does_not_duplicate_normalized_rows(conn):
    registry = {"a": _cfg("a")}
    outcome = AdapterOutcome(records=[_record("a", "same-key")])
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: outcome):
        run_daily_ingestion(conn, registry, "run-c1")
        run_daily_ingestion(conn, registry, "run-c2")

    assert _raw_count(conn) == 1  # raw dedup (Phase 2A, unchanged)
    assert _normalized_count(conn) == 1  # normalization dedup (Phase 2C, unchanged)


# ---- D: normalization stage exception -> run finalizes as failed -----------


def test_D_normalization_exception_finalizes_run_as_failed_without_losing_raw(conn):
    registry = {"a": _cfg("a")}
    outcome = AdapterOutcome(records=[_record("a", "k1")])

    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: outcome), \
         patch("ingestion.orchestrator.normalize_batch", side_effect=RuntimeError("boom")):
        result = run_daily_ingestion(conn, registry, "run-d")

    assert result["status"] == "failed"
    assert _raw_count(conn) == 1  # already-ingested RAW rows are not lost/rolled back
    assert _normalized_count(conn) == 0

    row = conn.execute(
        "SELECT status, finished_at, failure_stage FROM runs WHERE run_id='run-d'"
    ).fetchone()
    assert row["status"] == "failed"
    assert row["finished_at"] is not None  # run finalization was not skipped
    assert row["failure_stage"] == "normalization_stage_failed"
