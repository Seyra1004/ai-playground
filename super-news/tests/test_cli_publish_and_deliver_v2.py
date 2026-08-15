"""scripts/publish_and_deliver_v2.py CLI contract: exit-code mapping,
--db-path/--report-date/--no-push honored, thin CLI->orchestrator
integration. report.release_v2.run_daily_v2_release is always mocked here
-- its own real behavior is covered by tests/test_release_v2.py; no real
git/network/Kakao call happens in this file."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import publish_and_deliver_v2 as cli  # noqa: E402


def _pass_result():
    return {
        "status": "PASS", "reason": None,
        "local_check": {"ok": True}, "publish": {"published": True, "pushed": True},
        "external_check": {"ok": True}, "delivery": {"status": "sent"},
        "consistency": {"status": "CONSISTENT", "consistent": True},
    }


def _blocked_result(status, reason="something failed"):
    return {
        "status": status, "reason": reason,
        "local_check": None, "publish": None, "external_check": None,
        "delivery": None, "consistency": None,
    }


def test_pass_exits_ok(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("publish_and_deliver_v2.run_daily_v2_release", return_value=_pass_result()) as mock_release:
        exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-15"])
    assert exit_code == cli.EXIT_OK
    mock_release.assert_called_once()
    _, kwargs = mock_release.call_args
    assert mock_release.call_args.args[1] == "2026-08-15"  # report_date_kst positional


def test_publish_blocked_exits_release_failure(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("publish_and_deliver_v2.run_daily_v2_release", return_value=_blocked_result("PUBLISH_BLOCKED")):
        exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-15"])
    assert exit_code == cli.EXIT_RELEASE_FAILURE


def test_post_send_consistency_failure_exits_release_failure(tmp_path):
    db_path = tmp_path / "test.db"
    result = _pass_result()
    result["status"] = "POST_SEND_CONSISTENCY_FAILED"
    result["consistency"] = {"status": "MISMATCH", "consistent": False}
    with patch("publish_and_deliver_v2.run_daily_v2_release", return_value=result):
        exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-15"])
    assert exit_code == cli.EXIT_RELEASE_FAILURE  # a real send is never enough for exit 0


def test_default_report_date_is_today_kst(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("publish_and_deliver_v2.run_daily_v2_release", return_value=_pass_result()) as mock_release:
        cli.main(["--db-path", str(db_path)])
    report_date = mock_release.call_args.args[1]
    assert len(report_date) == 10 and report_date.count("-") == 2  # YYYY-MM-DD shape


def test_no_push_flag_propagated(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("publish_and_deliver_v2.run_daily_v2_release", return_value=_pass_result()) as mock_release:
        cli.main(["--db-path", str(db_path), "--report-date", "2026-08-15", "--no-push"])
    assert mock_release.call_args.kwargs["push"] is False


def test_push_is_default_true(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("publish_and_deliver_v2.run_daily_v2_release", return_value=_pass_result()) as mock_release:
        cli.main(["--db-path", str(db_path), "--report-date", "2026-08-15"])
    assert mock_release.call_args.kwargs["push"] is True


def test_invalid_invocation_exits_config_error():
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--not-a-real-flag"])
    assert exc_info.value.code == cli.EXIT_CONFIG_ERROR
