"""scripts/run_daily_kakao_delivery_v2.py: MUSIC and DAILY are fully
independent products -- an unexpected exception in one (a render bug, a
DB error, a real Kakao API failure -- anything NoDashboardDataError
doesn't already cover) must never prevent the other from being attempted
and must never leave main() before finalize_run() ever runs.

PRODUCTION INCIDENT REGRESSION (2026-08-19, real 07:00 KST send failure):
before this fix, _run_one_product only caught NoDashboardDataError -- any
other exception (the real incident: report/kakao_render_v2.py's own
render_music_kakao_digest AssertionError, hardened separately) propagated
straight past main(), killing the whole process before DAILY (which
always runs AFTER MUSIC) was ever attempted."""

from unittest.mock import Mock

from db.database import connect
from report_delivery_v2 import NoDashboardDataError
from scripts.run_daily_kakao_delivery_v2 import _run_one_product, main


# =============================================================================
# _run_one_product: the exact boundary the real 2026-08-19 incident crashed
# through.
# =============================================================================


def test_no_dashboard_data_error_is_isolated_as_before():
    deliver_fn = Mock(side_effect=NoDashboardDataError("no real content"))
    result = _run_one_product("MUSIC", deliver_fn, "2026-08-19", 1, conn=None, dashboard_data_v2={})
    assert result["status"] == "failed"
    assert "NoDashboardDataError" in result["reason"]


def test_unexpected_assertion_error_is_isolated_not_raised():
    """The exact real 2026-08-19 failure shape: render_music_kakao_digest
    raised an unhandled AssertionError. Must be caught and recorded, never
    propagate."""
    deliver_fn = Mock(side_effect=AssertionError("len(text) <= MAX_TEXT_LENGTH"))
    result = _run_one_product("MUSIC", deliver_fn, "2026-08-19", 1, conn=None, dashboard_data_v2={})
    assert result["status"] == "failed"
    assert "AssertionError" in result["reason"]


def test_unexpected_runtime_error_is_isolated_not_raised():
    deliver_fn = Mock(side_effect=RuntimeError("real Kakao API failure"))
    result = _run_one_product("DAILY", deliver_fn, "2026-08-19", 1, conn=None, dashboard_data_v2={})
    assert result["status"] == "failed"
    assert "RuntimeError" in result["reason"]


def test_successful_result_passes_through_unchanged():
    deliver_fn = Mock(return_value={"status": "sent", "reason": None})
    result = _run_one_product("MUSIC", deliver_fn, "2026-08-19", 1, conn=None, dashboard_data_v2={})
    assert result == {"status": "sent", "reason": None}


# =============================================================================
# main(): end-to-end isolation -- one product's unexpected crash must not
# stop the other, and finalize_run() must always run (no run stuck at
# status='running' the way the real 2026-08-19 incident left one).
# =============================================================================


def test_music_crash_does_not_prevent_daily_from_running(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "scripts.run_daily_kakao_delivery_v2.deliver_music_digest_v2",
        Mock(side_effect=AssertionError("simulated real MUSIC render crash")),
    )
    daily_deliver = Mock(return_value={"status": "sent", "reason": None})
    monkeypatch.setattr("scripts.run_daily_kakao_delivery_v2.deliver_daily_digest_v2", daily_deliver)

    exit_code = main(["--db-path", str(tmp_path / "test.db"), "--report-date", "2026-08-19"])

    daily_deliver.assert_called_once()  # DAILY was still attempted despite MUSIC's crash
    out = capsys.readouterr().out
    assert "MUSIC_STATUS=failed" in out
    assert "DAILY_STATUS=sent" in out
    assert exit_code == 1  # overall run is still a failure (MUSIC really did fail) -- never a false success


def test_daily_crash_does_not_affect_already_computed_music_result(monkeypatch, tmp_path, capsys):
    music_deliver = Mock(return_value={"status": "sent", "reason": None})
    monkeypatch.setattr("scripts.run_daily_kakao_delivery_v2.deliver_music_digest_v2", music_deliver)
    monkeypatch.setattr(
        "scripts.run_daily_kakao_delivery_v2.deliver_daily_digest_v2",
        Mock(side_effect=RuntimeError("simulated real DAILY Kakao API failure")),
    )

    exit_code = main(["--db-path", str(tmp_path / "test.db"), "--report-date", "2026-08-19"])

    music_deliver.assert_called_once()
    out = capsys.readouterr().out
    assert "MUSIC_STATUS=sent" in out
    assert "DAILY_STATUS=failed" in out
    assert exit_code == 1


def test_both_products_crashing_still_completes_the_run_without_raising(monkeypatch, tmp_path, capsys):
    """Worst case: BOTH products hit an unexpected exception. main() must
    still return (never let an exception escape to the caller/systemd) so
    the run row is finalized instead of left stuck at status='running'."""
    monkeypatch.setattr(
        "scripts.run_daily_kakao_delivery_v2.deliver_music_digest_v2",
        Mock(side_effect=AssertionError("simulated")),
    )
    monkeypatch.setattr(
        "scripts.run_daily_kakao_delivery_v2.deliver_daily_digest_v2",
        Mock(side_effect=RuntimeError("simulated")),
    )

    exit_code = main(["--db-path", str(tmp_path / "test.db"), "--report-date", "2026-08-19"])

    out = capsys.readouterr().out
    assert "MUSIC_STATUS=failed" in out
    assert "DAILY_STATUS=failed" in out
    assert exit_code == 1


def test_run_row_is_finalized_not_left_running_when_music_crashes(monkeypatch, tmp_path):
    """Real 2026-08-19 incident artifact: runs.id=23 was left at
    status='running' forever because the unhandled AssertionError killed
    the process before finalize_run() ever executed. Must not recur."""
    monkeypatch.setattr(
        "scripts.run_daily_kakao_delivery_v2.deliver_music_digest_v2",
        Mock(side_effect=AssertionError("simulated")),
    )
    monkeypatch.setattr(
        "scripts.run_daily_kakao_delivery_v2.deliver_daily_digest_v2",
        Mock(return_value={"status": "sent", "reason": None}),
    )

    db_path = tmp_path / "test.db"
    main(["--db-path", str(db_path), "--report-date", "2026-08-19"])

    fresh_conn = connect(db_path=db_path)
    try:
        rows = fresh_conn.execute(
            "SELECT status FROM runs WHERE run_date = '2026-08-19' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    finally:
        fresh_conn.close()
    assert rows and rows[0]["status"] != "running"


def test_module_import_forces_no_paid_api_env_var():
    """PRE-PRODUCTION HARDENING (2026-08-22, confirmed real defect): this
    script is invoked two ways in production -- as run_daily_full_
    pipeline_v2.py's own subprocess stage (already protected, inherits
    its parent's guard) and directly by scripts/deliver_retry.sh /
    super-news-delivery-retry.service, a SEPARATE systemd unit with no
    parent process and (before this fix) no code-level guard of its own.
    Already imported at module load time above -- assert the guard
    actually landed in os.environ, matching the same pattern scripts/
    run_daily_full_pipeline_v2.py and scripts/generate_daily_web_report_v2.py
    already have."""
    import os
    assert os.environ.get("SUPER_NEWS_NO_PAID_API") == "1"
