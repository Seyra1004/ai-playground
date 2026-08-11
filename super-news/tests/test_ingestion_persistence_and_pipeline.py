"""DB-integration and orchestration tests for the Phase 2A ingestion
foundation: TEST D, J, K, L, R, S, T, Z, plus the required failure-isolation
scenario (Section 35), the idempotency scenario (Section 36), and the
security/log-redaction scenario (Section 37). All use a real scratch
SQLite DB (tmp_path) — never production data/super_news.db — and never
make a real network call (adapters are stubbed functions)."""

import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.http import HttpTransientError
from ingestion.records import AdapterOutcome, IngestionRecord
from ingestion.registry import RetryPolicy, SourceConfig
from ingestion.pipeline import run_source_ingestion


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
        (run_id, "2026-08-12", "2026-08-12T00:00:00+00:00", "running"),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]


def _source(name="source_a", category="AI_NEWS", enabled=True, source_type="rss"):
    return SourceConfig(
        source_name=name,
        enabled=enabled,
        source_type=source_type,
        category=category,
        region="GLOBAL",
        endpoint="https://example.com/feed.xml",
        timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )


def _record(source_name, key, url=None, title="t", payload_hash=None):
    return IngestionRecord(
        source_name=source_name,
        source_item_key=key,
        source_type="rss",
        source_url=url or f"https://example.com/{key}",
        title=title,
        collected_at=datetime.now(timezone.utc).isoformat(),
        payload_hash=payload_hash,
    )


def _stub_adapter(outcome_or_exc):
    def adapter(source_config, sleep=None):
        if isinstance(outcome_or_exc, Exception):
            raise outcome_or_exc
        return outcome_or_exc
    return adapter


# ---- TEST D: disabled source -> no network / adapter call -------------------


def test_D_disabled_source_never_calls_adapter(conn):
    run_id = _insert_run(conn, "r1")
    source = _source(enabled=False)
    with patch("ingestion.pipeline.get_adapter") as mock_get_adapter:
        outcome = run_source_ingestion(conn, run_id, source)
    mock_get_adapter.assert_not_called()
    assert outcome["status"] == "SKIPPED"
    row = conn.execute(
        "SELECT status FROM run_source_status WHERE run_id=? AND source_name=?", (run_id, source.source_name)
    ).fetchone()
    assert row["status"] == "SKIPPED"


# ---- TEST J: same source/item re-collected -> no raw duplicate --------------


def test_J_retry_within_run_does_not_duplicate_raw_items(conn):
    run_id = _insert_run(conn, "r1")
    source = _source()
    outcome = AdapterOutcome(records=[_record(source.source_name, "k1"), _record(source.source_name, "k1")])
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        result = run_source_ingestion(conn, run_id, source)
    count = conn.execute(
        "SELECT COUNT(*) FROM raw_items WHERE source_name=? AND source_item_key='k1'", (source.source_name,)
    ).fetchone()[0]
    assert count == 1
    assert result["items_collected"] == 1  # inserted count, not raw fetched count


# ---- TEST K: different sources, same event -> both raw rows preserved ------


def test_K_cross_source_same_event_both_preserved(conn):
    run_id = _insert_run(conn, "r1")
    source_a = _source(name="naver_news")
    source_b = _source(name="rss_source")

    outcome_a = AdapterOutcome(records=[_record("naver_news", "event-x-naver")])
    outcome_b = AdapterOutcome(records=[_record("rss_source", "event-x-rss")])

    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome_a)):
        run_source_ingestion(conn, run_id, source_a)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome_b)):
        run_source_ingestion(conn, run_id, source_b)

    count = conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]
    assert count == 2


# ---- TEST L: same payload_hash across sources -> no cross-source dedup -----


def test_L_same_payload_hash_different_source_no_dedup(conn):
    run_id = _insert_run(conn, "r1")
    source_a = _source(name="naver_news")
    source_b = _source(name="rss_source")

    shared_hash = "deadbeef"
    outcome_a = AdapterOutcome(records=[_record("naver_news", "k-a", payload_hash=shared_hash)])
    outcome_b = AdapterOutcome(records=[_record("rss_source", "k-b", payload_hash=shared_hash)])

    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome_a)):
        run_source_ingestion(conn, run_id, source_a)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome_b)):
        run_source_ingestion(conn, run_id, source_b)

    count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE payload_hash=?", (shared_hash,)).fetchone()[0]
    assert count == 2


# ---- TEST R: 0 results is a normal SUCCESS, not FAILED ----------------------


def test_R_zero_results_is_success_not_failed(conn):
    run_id = _insert_run(conn, "r1")
    source = _source()
    outcome = AdapterOutcome(records=[], parse_errors=0)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        result = run_source_ingestion(conn, run_id, source)
    assert result["status"] == "SUCCESS"
    assert result["items_collected"] == 0


# ---- TEST S: some items parse-fail, some succeed -> PARTIAL -----------------


def test_S_partial_parse_failure_yields_partial_status(conn):
    run_id = _insert_run(conn, "r1")
    source = _source()
    outcome = AdapterOutcome(records=[_record(source.source_name, "k1")], parse_errors=2)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        result = run_source_ingestion(conn, run_id, source)
    assert result["status"] == "PARTIAL"
    assert result["items_collected"] == 1


def test_all_items_parse_fail_and_none_succeed_is_failed(conn):
    run_id = _insert_run(conn, "r1")
    source = _source()
    outcome = AdapterOutcome(records=[], parse_errors=3)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        result = run_source_ingestion(conn, run_id, source)
    assert result["status"] == "FAILED"


# ---- TEST T + Section 35: source failure isolation ---------------------------


def test_T_one_source_failure_does_not_roll_back_others(conn):
    run_id = _insert_run(conn, "r1")
    source_a = _source(name="source_a")
    source_b = _source(name="source_b")
    source_c = _source(name="source_c")

    outcome_a = AdapterOutcome(records=[_record("source_a", "a1"), _record("source_a", "a2"), _record("source_a", "a3")])
    outcome_c = AdapterOutcome(records=[_record("source_c", "c1"), _record("source_c", "c2")])

    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome_a)):
        result_a = run_source_ingestion(conn, run_id, source_a)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(HttpTransientError("timeout"))):
        result_b = run_source_ingestion(conn, run_id, source_b)
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome_c)):
        result_c = run_source_ingestion(conn, run_id, source_c)

    assert result_a["status"] == "SUCCESS"
    assert result_b["status"] == "FAILED"
    assert result_c["status"] == "SUCCESS"

    a_count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source_name='source_a'").fetchone()[0]
    c_count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source_name='source_c'").fetchone()[0]
    assert a_count == 3
    assert c_count == 2


# ---- TEST Z: run_source_status identity does not collide across category ---


def test_Z_same_run_and_source_different_category_both_preserved(conn):
    run_id = _insert_run(conn, "r1")
    source_ai = _source(name="multi_source", category="AI_NEWS")
    source_econ = _source(name="multi_source", category="ECONOMY_NEWS")

    outcome = AdapterOutcome(records=[])
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        run_source_ingestion(conn, run_id, source_ai)
        run_source_ingestion(conn, run_id, source_econ)

    rows = conn.execute(
        "SELECT category FROM run_source_status WHERE run_id=? AND source_name='multi_source' ORDER BY category",
        (run_id,),
    ).fetchall()
    assert [r["category"] for r in rows] == ["AI_NEWS", "ECONOMY_NEWS"]


def test_same_run_category_source_second_call_rejected(conn):
    import sqlite3

    run_id = _insert_run(conn, "r1")
    source = _source()
    outcome = AdapterOutcome(records=[])
    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        run_source_ingestion(conn, run_id, source)
        with pytest.raises(sqlite3.IntegrityError):
            run_source_ingestion(conn, run_id, source)


# ---- Section 36: idempotency across two separate runs -----------------------


def test_idempotent_across_two_runs_no_raw_duplicate_and_not_failed(conn):
    run_1 = _insert_run(conn, "r1")
    run_2 = _insert_run(conn, "r2")
    source = _source()
    outcome = AdapterOutcome(records=[_record(source.source_name, "same-key")])

    with patch("ingestion.pipeline.get_adapter", return_value=_stub_adapter(outcome)):
        result_1 = run_source_ingestion(conn, run_1, source)
        result_2 = run_source_ingestion(conn, run_2, source)

    assert result_1["status"] == "SUCCESS"
    assert result_1["items_collected"] == 1
    assert result_2["status"] == "SUCCESS"  # all-duplicates is not a failure
    assert result_2["items_collected"] == 0  # nothing NEW inserted the second time

    count = conn.execute(
        "SELECT COUNT(*) FROM raw_items WHERE source_name=? AND source_item_key='same-key'",
        (source.source_name,),
    ).fetchone()[0]
    assert count == 1


# ---- Section 37: security — no secret ever reaches error/log/status ---------


def test_credential_failure_never_leaks_secret_in_run_source_status(conn, caplog):
    from ingestion.http import HttpClientError

    run_id = _insert_run(conn, "r1")
    source = _source(name="naver_news", source_type="naver_news_api")
    secret_value = "super_secret_naver_client_id_value"

    def failing_adapter(source_config, sleep=None):
        raise HttpClientError(f"failed (never actually embeds {secret_value})", status_code=401)

    with caplog.at_level(logging.ERROR):
        with patch("ingestion.pipeline.get_adapter", return_value=failing_adapter):
            result = run_source_ingestion(conn, run_id, source)

    assert result["status"] == "FAILED"
    assert secret_value not in (result["failure_reason"] or "")
    row = conn.execute(
        "SELECT failure_reason FROM run_source_status WHERE run_id=? AND source_name=?",
        (run_id, source.source_name),
    ).fetchone()
    assert secret_value not in (row["failure_reason"] or "")
    for record in caplog.records:
        assert secret_value not in record.getMessage()
