"""TEST J, K, L, M, N, O, P, Q, R, S, W, X from the Phase 2B test matrix:
deterministic execution order, disabled-source policy, run-status
aggregation across SUCCESS/PARTIAL/FAILED/SKIPPED combinations, unexpected-
exception isolation, and the global-start-failure boundary."""

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


def _cfg(name, category="AI_NEWS", enabled=True):
    return SourceConfig(
        source_name=name,
        enabled=enabled,
        source_type="rss",
        category=category,
        region="GLOBAL",
        endpoint=f"https://example.com/{name}.xml",
        timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )


def _record(source_name, key):
    return IngestionRecord(
        source_name=source_name, source_item_key=key, source_type="rss",
        source_url=f"https://example.com/{key}", collected_at="2026-08-12T00:00:00+00:00",
    )


# ---- TEST J: source execution order is deterministic -------------------------


def test_J_execution_order_is_deterministic(conn):
    registry = {
        "z_source": _cfg("z_source", category="SOCIETY_NEWS"),
        "a_source": _cfg("a_source", category="AI_NEWS"),
        "m_source": _cfg("m_source", category="ECONOMY_NEWS"),
    }
    call_order = []

    def tracking_adapter(source_config, sleep=None):
        call_order.append(source_config.source_name)
        return AdapterOutcome(records=[])

    with patch("ingestion.pipeline.get_adapter", return_value=tracking_adapter):
        run_daily_ingestion(conn, registry, "run-order-1")

    call_order_2 = []

    def tracking_adapter_2(source_config, sleep=None):
        call_order_2.append(source_config.source_name)
        return AdapterOutcome(records=[])

    with patch("ingestion.pipeline.get_adapter", return_value=tracking_adapter_2):
        run_daily_ingestion(conn, registry, "run-order-2")

    assert call_order == ["a_source", "m_source", "z_source"]  # (category, source_name) order
    assert call_order == call_order_2


# ---- TEST K: disabled source -> adapter never invoked (no network) ---------


def test_K_disabled_source_adapter_never_called(conn):
    registry = {"disabled_source": _cfg("disabled_source", enabled=False), "enabled_source": _cfg("enabled_source")}
    with patch("ingestion.pipeline.get_adapter") as mock_get_adapter:
        mock_get_adapter.return_value = lambda source_config, sleep=None: AdapterOutcome(records=[])
        run_daily_ingestion(conn, registry, "run-k")
    called_for = [call.args[0] for call in mock_get_adapter.call_args_list]
    assert "rss" in called_for  # only the enabled source's adapter type was resolved
    assert mock_get_adapter.call_count == 1  # exactly once, for enabled_source only


# ---- TEST L: disabled source is recorded SKIPPED, accurately ----------------


def test_L_disabled_source_recorded_as_skipped(conn):
    registry = {"disabled_source": _cfg("disabled_source", enabled=False), "enabled_source": _cfg("enabled_source")}
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        run_daily_ingestion(conn, registry, "run-l")
    row = conn.execute(
        "SELECT status FROM run_source_status WHERE source_name='disabled_source'"
    ).fetchone()
    assert row["status"] == "SKIPPED"


# ---- TEST M: all enabled sources SUCCESS -> run final success ---------------


def test_M_all_success_run_is_completed(conn):
    registry = {"a": _cfg("a"), "b": _cfg("b", category="ECONOMY_NEWS")}
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[_record(sc.source_name, "k")])):
        result = run_daily_ingestion(conn, registry, "run-m")
    assert result["status"] == "completed"
    assert all(r["status"] == "SUCCESS" for r in result["source_results"])


# ---- TEST N: one FAILED + others SUCCESS -> successful data/status preserved


def test_N_partial_failure_preserves_successful_source_data(conn):
    registry = {"good": _cfg("good"), "bad": _cfg("bad", category="ECONOMY_NEWS")}

    def adapter(source_config, sleep=None):
        if source_config.source_name == "bad":
            raise HttpTransientError("boom")
        return AdapterOutcome(records=[_record("good", "k1")])

    with patch("ingestion.pipeline.get_adapter", return_value=adapter):
        result = run_daily_ingestion(conn, registry, "run-n")

    assert result["status"] == "completed"  # not everything failed
    statuses = {r["source_name"]: r["status"] for r in result["source_results"]}
    assert statuses["good"] == "SUCCESS"
    assert statuses["bad"] == "FAILED"
    count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source_name='good'").fetchone()[0]
    assert count == 1


# ---- TEST O: all enabled sources FAILED -> run failure -----------------------


def test_O_all_sources_failed_run_is_failed(conn):
    registry = {"a": _cfg("a"), "b": _cfg("b", category="ECONOMY_NEWS")}

    def failing_adapter(source_config, sleep=None):
        raise HttpTransientError("boom")

    with patch("ingestion.pipeline.get_adapter", return_value=failing_adapter):
        result = run_daily_ingestion(conn, registry, "run-o")
    assert result["status"] == "failed"


# ---- TEST P: PARTIAL source with usable data does not force run FAILED -----


def test_P_partial_source_does_not_force_run_failed(conn):
    registry = {"a": _cfg("a")}

    def adapter(source_config, sleep=None):
        return AdapterOutcome(records=[_record("a", "k1")], parse_errors=1)

    with patch("ingestion.pipeline.get_adapter", return_value=adapter):
        result = run_daily_ingestion(conn, registry, "run-p")
    assert result["source_results"][0]["status"] == "PARTIAL"
    assert result["status"] == "completed"


# ---- TEST Q: zero-result SUCCESS does not fail the run -----------------------


def test_Q_zero_result_source_does_not_fail_run(conn):
    registry = {"a": _cfg("a")}
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        result = run_daily_ingestion(conn, registry, "run-q")
    assert result["source_results"][0]["status"] == "SUCCESS"
    assert result["status"] == "completed"


# ---- TEST R + W: unexpected source exception isolated; earlier committed ---
# ---- source's data survives -------------------------------------------------


def test_R_and_W_unexpected_exception_isolated_and_prior_source_preserved(conn):
    registry = {"source_a": _cfg("source_a", category="AI_NEWS"),
                "source_b": _cfg("source_b", category="ECONOMY_NEWS"),
                "source_c": _cfg("source_c", category="SOCIETY_NEWS")}

    def adapter(source_config, sleep=None):
        if source_config.source_name == "source_a":
            return AdapterOutcome(records=[_record("source_a", "a1"), _record("source_a", "a2")])
        if source_config.source_name == "source_b":
            raise KeyError("totally unexpected bug, not an HttpError subclass")
        return AdapterOutcome(records=[_record("source_c", "c1")])

    with patch("ingestion.pipeline.get_adapter", return_value=adapter):
        result = run_daily_ingestion(conn, registry, "run-rw")

    statuses = {r["source_name"]: r["status"] for r in result["source_results"]}
    assert statuses["source_a"] == "SUCCESS"
    assert statuses["source_b"] == "FAILED"
    assert statuses["source_c"] == "SUCCESS"  # execution continued after source_b's crash

    a_count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source_name='source_a'").fetchone()[0]
    c_count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source_name='source_c'").fetchone()[0]
    assert a_count == 2
    assert c_count == 1

    reason = conn.execute(
        "SELECT failure_reason FROM run_source_status WHERE source_name='source_b'"
    ).fetchone()["failure_reason"]
    assert "KeyError" in reason


# ---- TEST S: global start failure -> source execution never begins ----------


def test_S_global_start_failure_means_zero_adapter_calls(conn):
    registry = {"a": _cfg("a")}
    conn.execute("DROP TABLE run_metadata")  # forces start_run to fail before any source runs

    with patch("ingestion.pipeline.get_adapter") as mock_get_adapter:
        with pytest.raises(Exception):
            run_daily_ingestion(conn, registry, "run-s")
    mock_get_adapter.assert_not_called()

    raw_count = conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]
    assert raw_count == 0


# ---- TEST X: run finalization status mapping matches runs.status contract --


def test_X_finalized_status_values_are_only_running_completed_or_failed(conn):
    registry = {"a": _cfg("a")}
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        run_daily_ingestion(conn, registry, "run-x")
    row = conn.execute("SELECT status, finished_at FROM runs WHERE run_id='run-x'").fetchone()
    assert row["status"] in ("completed", "failed")
    assert row["finished_at"] is not None
