"""scripts/publish_v2_product_pages.py CLI contract: exit-code mapping,
--report-date/--no-push honored, thin CLI->orchestrator integration, and
that it NEVER touches --db-path/Kakao (unlike publish_and_deliver_v2.py).
report.release_v2.run_daily_v2_product_pages_publish is always mocked
here -- its own real behavior is covered by tests/test_release_v2.py; no
real git/network call happens in this file."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import publish_v2_product_pages as cli  # noqa: E402


def _pass_result():
    return {
        "status": "PASS", "reason": None,
        "local_check": {"ok": True}, "publish": {"published": True, "pushed": True},
        "external_check": {"ok": True},
    }


def _blocked_result(status, reason="something failed"):
    return {"status": status, "reason": reason, "local_check": None, "publish": None, "external_check": None}


def test_pass_exits_ok():
    with patch(
        "publish_v2_product_pages.run_daily_v2_product_pages_publish", return_value=_pass_result()
    ) as mock_publish:
        exit_code = cli.main(["--report-date", "2026-08-19"])
    assert exit_code == cli.EXIT_OK
    mock_publish.assert_called_once()
    assert mock_publish.call_args.args[0] == "2026-08-19"  # report_date_kst positional


def test_publish_blocked_exits_release_failure():
    with patch(
        "publish_v2_product_pages.run_daily_v2_product_pages_publish",
        return_value=_blocked_result("PUBLISH_BLOCKED"),
    ):
        exit_code = cli.main(["--report-date", "2026-08-19"])
    assert exit_code == cli.EXIT_RELEASE_FAILURE


def test_external_verification_failure_exits_release_failure():
    with patch(
        "publish_v2_product_pages.run_daily_v2_product_pages_publish",
        return_value=_blocked_result("EXTERNAL_VERIFICATION_FAILED"),
    ):
        exit_code = cli.main(["--report-date", "2026-08-19"])
    assert exit_code == cli.EXIT_RELEASE_FAILURE


def test_default_report_date_is_today_kst():
    with patch(
        "publish_v2_product_pages.run_daily_v2_product_pages_publish", return_value=_pass_result()
    ) as mock_publish:
        cli.main([])
    report_date = mock_publish.call_args.args[0]
    assert len(report_date) == 10 and report_date.count("-") == 2  # YYYY-MM-DD shape


def test_no_push_flag_propagated():
    with patch(
        "publish_v2_product_pages.run_daily_v2_product_pages_publish", return_value=_pass_result()
    ) as mock_publish:
        cli.main(["--report-date", "2026-08-19", "--no-push"])
    assert mock_publish.call_args.kwargs["push"] is False


def test_push_is_default_true():
    with patch(
        "publish_v2_product_pages.run_daily_v2_product_pages_publish", return_value=_pass_result()
    ) as mock_publish:
        cli.main(["--report-date", "2026-08-19"])
    assert mock_publish.call_args.kwargs["push"] is True


def test_invalid_invocation_exits_config_error():
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--not-a-real-flag"])
    assert exc_info.value.code == cli.EXIT_CONFIG_ERROR


def test_never_imports_kakao_send_path():
    """This CLI must have no dependency at all on report_delivery_v2 /
    Kakao -- it is web-publish-only by design (see module docstring)."""
    assert not hasattr(cli, "deliver_music_digest_v2")
    assert not hasattr(cli, "deliver_daily_digest_v2")
    assert not hasattr(cli, "send_memo")
