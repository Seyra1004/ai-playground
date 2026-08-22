"""scripts/check_final_delivery_failure_v2.py: time-gated (07:50 KST),
deduplicated final-failure Kakao alert -- never during the normal
07:10/07:25 retry window, at most ONE alert per product/report_date,
completely isolated from real DAILY/MUSIC delivery idempotency.
kakao.client.send_memo is always mocked -- no real Kakao API call."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from delivery import build_idempotency_key, decide_delivery_action, record_delivery
from kakao.client import KakaoSendError
from scripts.check_final_delivery_failure_v2 import main

_KST = timezone(timedelta(hours=9))
_BEFORE_GATE = lambda: datetime(2026, 8, 19, 7, 25, tzinfo=_KST)  # before 07:50 KST
_AFTER_GATE = lambda: datetime(2026, 8, 19, 7, 55, tzinfo=_KST)  # at/after 07:50 KST


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path)
    return path


def _insert_run(path, run_id="run-1", run_date="2026-08-19"):
    conn = connect(db_path=path)
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    runs_row_id = conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]
    conn.close()
    return runs_row_id


def _mark_real_delivery_sent(path, report_type, report_date="2026-08-19"):
    runs_row_id = _insert_run(path, run_id=f"run-{report_type}", run_date=report_date)
    conn = connect(db_path=path)
    record_delivery(runs_row_id, report_date, report_type, "kakao_memo", "hash", "sent", conn=conn)
    conn.commit()
    conn.close()


def test_too_early_no_ops_without_force(db_path):
    with patch("scripts.check_final_delivery_failure_v2.send_memo") as mock_send:
        exit_code = main(["--db-path", str(db_path), "--report-date", "2026-08-19"], now_fn=_BEFORE_GATE)
    assert exit_code == 0
    mock_send.assert_not_called()


def test_at_gate_time_evaluates_for_real_without_force(db_path):
    """07:55 (the real last retry slot) must evaluate for real even
    without --force -- --force is a manual/test-only bypass, not what the
    real retry timer ever passes."""
    with patch("scripts.check_final_delivery_failure_v2.send_memo") as mock_send:
        exit_code = main(["--db-path", str(db_path), "--report-date", "2026-08-19"], now_fn=_AFTER_GATE)
    assert exit_code == 0
    assert mock_send.call_count == 2  # both MUSIC and DAILY genuinely never sent


def test_force_skips_alert_when_both_products_already_sent(db_path):
    _mark_real_delivery_sent(db_path, "SUPER_NEWS_MUSIC_V2")
    _mark_real_delivery_sent(db_path, "SUPER_NEWS_DAILY_V2")
    with patch("scripts.check_final_delivery_failure_v2.send_memo") as mock_send:
        exit_code = main(["--db-path", str(db_path), "--report-date", "2026-08-19", "--force"])
    assert exit_code == 0
    mock_send.assert_not_called()


def test_force_sends_exactly_one_alert_for_genuinely_failed_product(db_path):
    _mark_real_delivery_sent(db_path, "SUPER_NEWS_MUSIC_V2")  # MUSIC OK, DAILY never sent

    with patch("scripts.check_final_delivery_failure_v2.send_memo") as mock_send:
        exit_code = main(["--db-path", str(db_path), "--report-date", "2026-08-19", "--force"])

    assert exit_code == 0
    mock_send.assert_called_once()
    sent_text = mock_send.call_args.args[0]
    assert "DAILY" in sent_text
    assert "MUSIC" not in sent_text
    assert "재시도 실패" in sent_text

    conn = connect(db_path=db_path)
    row = conn.execute(
        "SELECT status FROM delivery_history WHERE report_date='2026-08-19' "
        "AND report_type='SUPER_NEWS_DAILY_V2_FAILURE_ALERT'"
    ).fetchone()
    conn.close()
    assert row["status"] == "sent"


def test_alert_not_repeated_once_already_sent_today(db_path):
    with patch("scripts.check_final_delivery_failure_v2.send_memo"):
        main(["--db-path", str(db_path), "--report-date", "2026-08-19", "--force"])  # both fail -> 2 alerts

    with patch("scripts.check_final_delivery_failure_v2.send_memo") as mock_send_second:
        exit_code = main(["--db-path", str(db_path), "--report-date", "2026-08-19", "--force"])

    assert exit_code == 0
    mock_send_second.assert_not_called()  # already alerted -- no repeat spam


def test_alert_send_failure_does_not_crash_and_still_records_failed(db_path):
    with patch("scripts.check_final_delivery_failure_v2.send_memo", side_effect=KakaoSendError("kakao down")):
        exit_code = main(["--db-path", str(db_path), "--report-date", "2026-08-19", "--force"])

    assert exit_code == 0  # journal is authoritative -- this never crashes/fails the script
    conn = connect(db_path=db_path)
    rows = conn.execute(
        "SELECT report_type, status FROM delivery_history WHERE report_date='2026-08-19' "
        "AND report_type LIKE '%_FAILURE_ALERT'"
    ).fetchall()
    conn.close()
    assert {r["report_type"]: r["status"] for r in rows} == {
        "SUPER_NEWS_MUSIC_V2_FAILURE_ALERT": "failed",
        "SUPER_NEWS_DAILY_V2_FAILURE_ALERT": "failed",
    }


def test_alert_never_touches_real_daily_music_idempotency(db_path):
    with patch("scripts.check_final_delivery_failure_v2.send_memo"):
        main(["--db-path", str(db_path), "--report-date", "2026-08-19", "--force"])

    conn = connect(db_path=db_path)
    real_key_music = build_idempotency_key("2026-08-19", "SUPER_NEWS_MUSIC_V2", "kakao_memo")
    real_key_daily = build_idempotency_key("2026-08-19", "SUPER_NEWS_DAILY_V2", "kakao_memo")
    # A real DAILY/MUSIC delivery must still see "send" (never "skip_duplicate")
    # after an alert was sent -- the alert lives in a completely separate
    # idempotency namespace and can never mask/skip a genuine future delivery.
    assert decide_delivery_action(real_key_music, conn=conn) == "send"
    assert decide_delivery_action(real_key_daily, conn=conn) == "send"
    conn.close()


def test_module_import_forces_no_paid_api_env_var():
    import os
    assert os.environ.get("SUPER_NEWS_NO_PAID_API") == "1"
