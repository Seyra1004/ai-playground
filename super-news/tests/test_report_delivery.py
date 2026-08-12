"""report_delivery: report selection (latest coherent run only), duplicate-
send prevention (reusing delivery.py's idempotency), Kakao API / auth
failure handling, no false-success on a partial multi-message send. Kakao
is always mocked (report_delivery.send_memo) -- no live network/API call."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from kakao.auth import ReauthRequiredError
from kakao.client import KakaoSendError
from report_delivery import NoReportAvailableError, deliver_daily_report, select_latest_reports


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, '2026-08-12', 'x', 'completed')",
        (run_id,),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_report(conn, run_row_id, category, content, report_date="2026-08-12"):
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, ?, ?, ?, ?, 'hash', 'x')""",
        (run_row_id, report_date, category, category, content),
    )
    conn.commit()


# ---- report selection: latest coherent run only -----------------------------


def test_select_latest_reports_uses_the_most_recent_run_only(conn):
    old_run = _insert_run(conn, "run-old")
    _insert_report(conn, old_run, "MUSIC", "old music content")

    new_run = _insert_run(conn, "run-new")
    _insert_report(conn, new_run, "AI", "new AI content")

    reports, source_run_id = select_latest_reports(conn, "2026-08-12")
    assert source_run_id == new_run
    assert reports == {"AI": "new AI content"}  # old run's MUSIC report is NOT mixed in


def test_select_latest_reports_missing_category_is_absent_not_fabricated(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_report(conn, run_row_id, "AI", "AI content")
    reports, _ = select_latest_reports(conn, "2026-08-12")
    assert "ECONOMY" not in reports


def test_select_latest_reports_raises_when_nothing_exists(conn):
    with pytest.raises(NoReportAvailableError):
        select_latest_reports(conn, "2026-08-12")


# ---- successful delivery -----------------------------------------------------


def test_successful_delivery_sends_all_chunks_and_records_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_report(conn, run_row_id, "AI", "short AI content")
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery.send_memo") as mock_send:
        result = deliver_daily_report("2026-08-12", delivery_run_row_id, conn=conn)

    assert result["status"] == "sent"
    assert result["sent_count"] == result["message_count"] == mock_send.call_count

    row = conn.execute(
        "SELECT status FROM delivery_history WHERE report_date='2026-08-12'"
    ).fetchone()
    assert row["status"] == "sent"


# ---- duplicate-send prevention -----------------------------------------------


def test_duplicate_send_is_skipped_and_kakao_never_called(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_report(conn, run_row_id, "AI", "content")
    delivery_run_1 = _insert_run(conn, "run-delivery-1")

    with patch("report_delivery.send_memo"):
        first = deliver_daily_report("2026-08-12", delivery_run_1, conn=conn)
    assert first["status"] == "sent"

    delivery_run_2 = _insert_run(conn, "run-delivery-2")
    with patch("report_delivery.send_memo") as mock_send:
        second = deliver_daily_report("2026-08-12", delivery_run_2, conn=conn)

    assert second["status"] == "skipped_duplicate"
    mock_send.assert_not_called()

    sent_count = conn.execute(
        "SELECT COUNT(*) FROM delivery_history WHERE report_date='2026-08-12' AND status='sent'"
    ).fetchone()[0]
    assert sent_count == 1  # never duplicated


# ---- Kakao API failure handling: no false-success ----------------------------


def test_kakao_send_error_is_recorded_as_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_report(conn, run_row_id, "AI", "content")
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery.send_memo", side_effect=KakaoSendError("boom")):
        result = deliver_daily_report("2026-08-12", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    assert result["sent_count"] == 0
    row = conn.execute(
        "SELECT status FROM delivery_history WHERE report_date='2026-08-12'"
    ).fetchone()
    assert row["status"] == "failed"


def test_kakao_auth_failure_is_recorded_as_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_report(conn, run_row_id, "AI", "content")
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery.send_memo", side_effect=ReauthRequiredError("token dead")):
        result = deliver_daily_report("2026-08-12", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    assert "ReauthRequiredError" in result["reason"]


# ---- partial multi-message send: no false-success ----------------------------


def test_partial_multi_message_send_is_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    # Long enough content that split_message() produces >1 chunk.
    long_content = "\n".join(f"headline {i} with some padding text to lengthen it" for i in range(20))
    _insert_report(conn, run_row_id, "AI", long_content)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    # First chunk succeeds, second raises -- a real partial send.
    with patch("report_delivery.send_memo", side_effect=[None, KakaoSendError("boom")]):
        result = deliver_daily_report("2026-08-12", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    assert result["message_count"] > 1
    assert 0 < result["sent_count"] < result["message_count"]
    row = conn.execute(
        "SELECT status FROM delivery_history WHERE report_date='2026-08-12'"
    ).fetchone()
    assert row["status"] == "failed"  # never "sent" when only some chunks landed
