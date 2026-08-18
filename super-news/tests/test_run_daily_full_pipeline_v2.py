"""scripts/run_daily_full_pipeline_v2.py: stage sequencing, cost-guard
enforcement, --dry-run threading, and exit-code aggregation. Every stage
is a subprocess call in the real script -- subprocess.run is mocked here,
no real ingestion/collection/Kakao network calls happen in this test."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_full_pipeline_v2 as cli  # noqa: E402


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ordered_labels(calls):
    """Extract the --db-path-preceding script filename for each subprocess.run call, in order."""
    labels = []
    for call in calls:
        cmd = call.args[0]
        labels.append(Path(cmd[1]).name)
    return labels


# ---- cost guard ---------------------------------------------------------


def test_module_import_forces_no_paid_api_env_var():
    # Already imported above at module load time -- assert the guard the
    # module docstring promises actually landed in os.environ.
    assert os.environ.get("SUPER_NEWS_NO_PAID_API") == "1"


def test_module_import_forces_claude_cli_llm_provider():
    assert os.environ.get("LLM_PROVIDER") == "claude_cli"


def test_every_stage_subprocess_inherits_the_no_paid_api_guard(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0)) as mock_run:
        exit_code = cli.main(["--db-path", str(db_path)])

    assert exit_code == cli.EXIT_OK
    assert mock_run.call_count == 9  # 4 upstream + 4 intelligence + 1 delivery
    for call in mock_run.call_args_list:
        env = call.kwargs["env"]
        assert env.get("SUPER_NEWS_NO_PAID_API") == "1"
        assert env.get("LLM_PROVIDER") == "claude_cli"


# ---- stage sequencing -----------------------------------------------------


def test_stage_order_is_ingestion_then_music_then_signals_then_intelligence_then_delivery(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0)) as mock_run:
        cli.main(["--db-path", str(db_path)])

    assert _ordered_labels(mock_run.call_args_list) == [
        "run_daily_ingestion.py",
        "run_daily_music.py",
        "run_daily_music_spotify.py",
        "run_daily_music_signals.py",
        "run_daily_report.py",
        "run_daily_producer_intelligence.py",
        "run_daily_news_intelligence.py",
        "run_daily_music_trend_intelligence.py",
        "run_daily_kakao_delivery_v2.py",
    ]


def test_every_stage_receives_the_resolved_db_path(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0)) as mock_run:
        cli.main(["--db-path", str(db_path)])

    for call in mock_run.call_args_list:
        cmd = call.args[0]
        assert "--db-path" in cmd
        assert cmd[cmd.index("--db-path") + 1] == str(db_path)


def test_default_db_path_falls_back_to_config_db_path():
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0)) as mock_run:
        cli.main([])

    cmd = mock_run.call_args_list[0].args[0]
    assert cmd[cmd.index("--db-path") + 1] == str(cli.DB_PATH)


# ---- --dry-run threading ---------------------------------------------------


def test_dry_run_flag_only_reaches_the_delivery_stage(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0)) as mock_run:
        cli.main(["--db-path", str(db_path), "--dry-run"])

    calls = mock_run.call_args_list
    non_delivery_calls, delivery_call = calls[:8], calls[8]
    for call in non_delivery_calls:
        assert "--dry-run" not in call.args[0]
    assert "--dry-run" in delivery_call.args[0]


def test_without_dry_run_flag_delivery_stage_never_gets_it(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0)) as mock_run:
        cli.main(["--db-path", str(db_path)])

    delivery_call = mock_run.call_args_list[8]
    assert "--dry-run" not in delivery_call.args[0]


# ---- exit code aggregation --------------------------------------------------


def test_exit_ok_when_delivery_stage_succeeds_even_if_upstream_stage_failed(tmp_path):
    db_path = tmp_path / "test.db"

    def _side_effect(cmd, **kwargs):
        if Path(cmd[1]).name == "run_daily_music.py":
            return _FakeCompletedProcess(1)  # a real upstream failure
        return _FakeCompletedProcess(0)

    with patch("subprocess.run", side_effect=_side_effect):
        exit_code = cli.main(["--db-path", str(db_path)])

    assert exit_code == cli.EXIT_OK


def test_exit_failure_when_delivery_stage_fails_even_if_all_upstream_succeeded(tmp_path):
    db_path = tmp_path / "test.db"

    def _side_effect(cmd, **kwargs):
        if Path(cmd[1]).name == "run_daily_kakao_delivery_v2.py":
            return _FakeCompletedProcess(1)
        return _FakeCompletedProcess(0)

    with patch("subprocess.run", side_effect=_side_effect):
        exit_code = cli.main(["--db-path", str(db_path)])

    assert exit_code == cli.EXIT_DELIVERY_FAILURE


def test_upstream_failure_never_skips_later_stages(tmp_path):
    db_path = tmp_path / "test.db"

    def _side_effect(cmd, **kwargs):
        if Path(cmd[1]).name == "run_daily_ingestion.py":
            return _FakeCompletedProcess(1)
        return _FakeCompletedProcess(0)

    with patch("subprocess.run", side_effect=_side_effect) as mock_run:
        cli.main(["--db-path", str(db_path)])

    assert mock_run.call_count == 9
