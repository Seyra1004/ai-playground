"""scripts/run_daily_pipeline.sh: News Intelligence stage wiring (Phase
3D). Runs the REAL shell script (not a reimplementation) via subprocess,
with SUPER_NEWS_DIR/SUPER_NEWS_PYTHON pointed at a fake python stub so no
real CLI, DB, or network is ever touched -- this proves the actual shell
control flow (ordering, exit-code handling, non-blocking degradation),
not a simulation of it. Requires bash on PATH (already required for every
other shell-level check in this project's own QA history)."""

import shutil
import subprocess
import sys
import textwrap

import pytest

_PIPELINE_SCRIPT = "scripts/run_daily_pipeline.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available on PATH")


_DISPATCHER_TEMPLATE = '''
import os
import sys

script = sys.argv[1] if len(sys.argv) > 1 else ""
argv_rest = sys.argv[2:]
log_path = os.environ["FAKE_PIPELINE_LOG"]
log_line = (script + " " + " ".join(argv_rest)).rstrip()
with open(log_path, "a", encoding="utf-8") as f:
    f.write(log_line + "\\n")

force_fail_script = os.environ.get("FAKE_FORCE_FAIL_SCRIPT")
if force_fail_script and script.endswith(force_fail_script):
    print(f"fake stage FORCED FAILURE: {script}")
    print("status=FAILED")
    sys.exit(1)

if script.endswith("run_daily_news_intelligence.py"):
    status = os.environ.get("FAKE_NI_STATUS", "completed_with_insights")
    exit_code = int(os.environ.get("FAKE_NI_EXIT", "0"))
    print(f"run_id=fake-run report_date=2026-08-14 status={status}")
    if status == "failed":
        print("  reason=synthetic test failure")
    sys.exit(exit_code)

if script.endswith("backup_database.py"):
    if "--capacity-only" in argv_rest:
        exit_code = int(os.environ.get("FAKE_CAPACITY_EXIT", "0"))
        alert_level = os.environ.get("FAKE_R2_ALERT_LEVEL", "OK")
        forecast = os.environ.get("FAKE_R2_CAPACITY_FORECAST", "INSUFFICIENT_HISTORY_FOR_FORECAST")
        print("R2_STORAGE_BYTES=1000")
        print("R2_STORAGE_GB=0.0001")
        print("R2_FREE_ALLOWANCE_GB=10.0")
        print("R2_USAGE_PERCENT=0.01")
        print(f"R2_ALERT_LEVEL={alert_level}")
        print("R2_ESTIMATED_DAYS_TO_THRESHOLD=None")
        print(f"R2_CAPACITY_FORECAST={forecast}")
        sys.exit(exit_code)
    if "--type" in argv_rest:
        backup_type = argv_rest[argv_rest.index("--type") + 1]
        exit_code = int(os.environ.get(f"FAKE_BACKUP_{backup_type.upper()}_EXIT", "0"))
        print(f"r2_object_key=database/2026/08/{backup_type.upper()}_fake.db")
        print(f"upload_verified={exit_code == 0}")
        if exit_code == 3:
            print("R2_CONFIGURATION_REQUIRED")
        elif exit_code != 0:
            print("BACKUP_INVALID")
        sys.exit(exit_code)

# Every other stage: a bland, unambiguous success line + exit 0. Deliberately
# avoids any substring that could accidentally match another stage's own
# DEGRADED grep pattern (status=FAILED/PARTIAL/REPORT_FAILED).
print(f"fake stage ok: {script}")
sys.exit(0)
'''


@pytest.fixture
def fake_pipeline_env(tmp_path):
    """A minimal fake SUPER_NEWS_DIR: just enough for the real script's own
    `cd`/`$PY scripts/x.py` calls to resolve -- scripts/*.py themselves are
    never read (the fake python stub ignores their content, only their
    argv path), but must exist as real files since the real repo's
    scripts/run_daily_pipeline.sh is copied in unmodified and expects to
    find them at those relative paths."""
    real_repo_root = __import__("pathlib").Path(__file__).resolve().parent.parent
    fake_dir = tmp_path / "fake_super_news"
    (fake_dir / "scripts").mkdir(parents=True)
    real_pipeline = (real_repo_root / _PIPELINE_SCRIPT).read_text(encoding="utf-8")
    (fake_dir / _PIPELINE_SCRIPT).write_text(real_pipeline, encoding="utf-8")
    for name in (
        "run_daily_ingestion.py", "run_daily_music.py", "run_daily_music_spotify.py",
        "run_daily_music_signals.py", "run_daily_report.py", "run_daily_producer_intelligence.py",
        "run_daily_news_intelligence.py", "run_daily_music_trend_intelligence.py",
        "generate_daily_web_report_v2.py", "deliver_daily_report.py",
        "backup_database.py",
    ):
        (fake_dir / "scripts" / name).write_text("# fake stub, never actually read\n", encoding="utf-8")

    dispatcher_path = tmp_path / "fake_python_dispatcher.py"
    dispatcher_path.write_text(textwrap.dedent(_DISPATCHER_TEMPLATE), encoding="utf-8")
    log_path = tmp_path / "invocation_log.txt"

    # This Windows dev machine's Git Bash has no `flock` binary (the real
    # script's own non-blocking lock, correct and unchanged for its real
    # Linux production target) -- provide a fake one on PATH for this test
    # only, that always "succeeds" (exit 0) like an uncontended real flock
    # would. Never touches the real script's own lock logic.
    fake_bin_dir = tmp_path / "fake_bin"
    fake_bin_dir.mkdir()
    fake_flock = fake_bin_dir / "flock"
    fake_flock.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_flock.chmod(0o755)

    # $PY is used UNQUOTED in the real script (`$PY scripts/x.py`), so it
    # undergoes bash word-splitting -- a raw "<python.exe> <dispatcher.py>"
    # string would split into two tokens with no shell-quoting applied to
    # either (any embedded quote characters become literal, breaking
    # paths). A tiny wrapper script sidesteps this entirely: SUPER_NEWS_
    # PYTHON is set to this ONE script's path (a single token), which
    # internally invokes the real interpreter + dispatcher with proper
    # argument passing.
    py_wrapper = tmp_path / "fake_py_wrapper.sh"
    py_wrapper.write_text(
        '#!/usr/bin/env bash\nexec "$FAKE_PY_REAL" "$FAKE_PY_DISPATCHER" "$@"\n', encoding="utf-8",
    )
    py_wrapper.chmod(0o755)

    return {
        "fake_dir": fake_dir,
        "dispatcher_path": dispatcher_path,
        "log_path": log_path,
        "real_pipeline_script": fake_dir / _PIPELINE_SCRIPT,
        "fake_bin_dir": fake_bin_dir,
        "py_wrapper": py_wrapper,
    }


def _run_pipeline(fake_pipeline_env, extra_env=None):
    env = dict(**__import__("os").environ)
    env["SUPER_NEWS_DIR"] = str(fake_pipeline_env["fake_dir"])
    env["SUPER_NEWS_PYTHON"] = str(fake_pipeline_env["py_wrapper"])
    env["FAKE_PY_REAL"] = sys.executable
    env["FAKE_PY_DISPATCHER"] = str(fake_pipeline_env["dispatcher_path"])
    env["FAKE_PIPELINE_LOG"] = str(fake_pipeline_env["log_path"])
    env["PATH"] = str(fake_pipeline_env["fake_bin_dir"]) + ":" + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(fake_pipeline_env["real_pipeline_script"])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30,
    )
    return result


def _invocation_log(fake_pipeline_env):
    if not fake_pipeline_env["log_path"].exists():
        return []
    return fake_pipeline_env["log_path"].read_text(encoding="utf-8").strip().splitlines()


# ---- A/I: ordering + report_date convention -------------------------------


def test_A_news_intelligence_runs_before_dashboard_generation(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env)
    log = _invocation_log(fake_pipeline_env)
    ni_index = next(i for i, line in enumerate(log) if line.endswith("run_daily_news_intelligence.py"))
    web_v2_index = next(i for i, line in enumerate(log) if line.endswith("generate_daily_web_report_v2.py"))
    assert ni_index < web_v2_index
    assert result.returncode == 0


def test_I_no_report_date_override_both_default_to_same_today(fake_pipeline_env):
    """Neither News Intelligence nor dashboard generation is passed an
    explicit --report-date by the pipeline (confirmed by grep against the
    real script content) -- both independently default to "today, KST",
    which is how every other stage in this pipeline already behaves (no
    date is threaded between stages at the shell level at all). This is
    the actual mechanism that keeps them aligned, not new plumbing."""
    content = fake_pipeline_env["real_pipeline_script"].read_text(encoding="utf-8")
    ni_line = next(l for l in content.splitlines() if "run_daily_news_intelligence.py" in l and "$PY" in l)
    web_v2_line = next(l for l in content.splitlines() if "generate_daily_web_report_v2.py" in l and "$PY" in l)
    assert "--report-date" not in ni_line
    assert "--report-date" not in web_v2_line


# ---- B-E: every real News Intelligence status keeps the pipeline moving ---


@pytest.mark.parametrize("status", ["completed_with_insights", "completed_reused", "completed_partial", "completed_no_evidence"])
def test_BCDE_every_non_failed_status_lets_dashboard_stage_proceed(fake_pipeline_env, status):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_NI_STATUS": status, "FAKE_NI_EXIT": "0"})
    log = _invocation_log(fake_pipeline_env)
    assert any(line.endswith("generate_daily_web_report_v2.py") for line in log)
    assert any(line.endswith("deliver_daily_report.py") for line in log)
    assert result.returncode == 0
    assert f"NEWS_INTELLIGENCE_STAGE_RESULT: {status}" in result.stdout


# ---- F/G: ordinary News Intelligence failure never blocks real news -------


def test_FG_news_intelligence_failure_does_not_block_dashboard_or_delivery(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_NI_STATUS": "failed", "FAKE_NI_EXIT": "1"})
    log = _invocation_log(fake_pipeline_env)
    assert any(line.endswith("generate_daily_web_report_v2.py") for line in log)
    assert any(line.endswith("deliver_daily_report.py") for line in log)
    assert result.returncode == 0  # News Intelligence is not a "required" stage
    assert "STAGE_RESULT news_intelligence=FAILED exit=1" in result.stdout
    assert "NEWS_INTELLIGENCE_STAGE_RESULT: failed" in result.stdout


def test_completed_partial_visibly_classified_degraded_but_non_blocking(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_NI_STATUS": "completed_partial", "FAKE_NI_EXIT": "0"})
    assert "STAGE_RESULT news_intelligence=DEGRADED exit=0" in result.stdout
    assert result.returncode == 0


# ---- H: runs exactly once --------------------------------------------------


def test_H_news_intelligence_invoked_exactly_once(fake_pipeline_env):
    _run_pipeline(fake_pipeline_env)
    log = _invocation_log(fake_pipeline_env)
    ni_calls = [line for line in log if line.endswith("run_daily_news_intelligence.py")]
    assert len(ni_calls) == 1


# ---- observability ---------------------------------------------------------


def test_observability_start_and_result_markers_present(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_NI_STATUS": "completed_with_insights"})
    assert "NEWS_INTELLIGENCE_STAGE_START" in result.stdout
    assert "NEWS_INTELLIGENCE_STAGE_RESULT: completed_with_insights" in result.stdout
    assert "news_intelligence=" in result.stdout  # in the final SUMMARY line


def test_summary_line_distinguishes_dashboard_news_intelligence_and_delivery(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_NI_STATUS": "completed_with_insights"})
    summary_line = next(l for l in result.stdout.splitlines() if l.startswith("=== SUMMARY"))
    assert "news_intelligence=SUCCESS" in summary_line
    assert "web_v2=SUCCESS" in summary_line
    assert "delivery=SUCCESS" in summary_line


# =============================================================================
# Music Trend Intelligence stage wiring (MUSIC INTELLIGENCE COMPLETION,
# FINAL MUSIC INTEGRATION CHECK phase)
# =============================================================================


def test_music_trend_runs_after_report_and_before_dashboard_generation(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env)
    log = _invocation_log(fake_pipeline_env)
    report_index = next(i for i, line in enumerate(log) if line.endswith("run_daily_report.py"))
    mt_index = next(i for i, line in enumerate(log) if line.endswith("run_daily_music_trend_intelligence.py"))
    web_v2_index = next(i for i, line in enumerate(log) if line.endswith("generate_daily_web_report_v2.py"))
    assert report_index < mt_index < web_v2_index
    assert result.returncode == 0


def test_music_trend_failure_does_not_block_dashboard_or_delivery(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={
        "FAKE_FORCE_FAIL_SCRIPT": "run_daily_music_trend_intelligence.py",
    })
    log = _invocation_log(fake_pipeline_env)
    assert any(line.endswith("generate_daily_web_report_v2.py") for line in log)
    assert any(line.endswith("deliver_daily_report.py") for line in log)
    assert result.returncode == 0  # not a "required" stage -- same precedent as producer/news intelligence
    assert "STAGE_RESULT music_trend_intelligence=FAILED exit=1" in result.stdout


def test_music_trend_success_visibly_classified(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env)
    assert "STAGE_RESULT music_trend_intelligence=SUCCESS exit=0" in result.stdout


def test_music_trend_invoked_exactly_once(fake_pipeline_env):
    _run_pipeline(fake_pipeline_env)
    log = _invocation_log(fake_pipeline_env)
    mt_calls = [line for line in log if line.endswith("run_daily_music_trend_intelligence.py")]
    assert len(mt_calls) == 1


def test_music_trend_no_report_date_override(fake_pipeline_env):
    """Same convention as News Intelligence/dashboard generation (test_I
    above): no --report-date is threaded at the shell level, so this stage
    independently defaults to "today, KST" exactly like every other daily
    stage."""
    content = fake_pipeline_env["real_pipeline_script"].read_text(encoding="utf-8")
    mt_line = next(l for l in content.splitlines() if "run_daily_music_trend_intelligence.py" in l and "$PY" in l)
    assert "--report-date" not in mt_line


def test_summary_line_includes_music_trend_intelligence(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env)
    summary_line = next(l for l in result.stdout.splitlines() if l.startswith("=== SUMMARY"))
    assert "music_trend_intelligence=SUCCESS" in summary_line
    assert "web_v2=SUCCESS" in summary_line


def test_music_trend_not_run_when_pre_backup_fails(fake_pipeline_env):
    """Same PRE/POST R2 contract as every other DB-mutating stage -- a
    failed PRE backup blocks this new stage exactly like it already blocks
    every existing one."""
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_BACKUP_PRE_EXIT": "1"})
    log = _invocation_log(fake_pipeline_env)
    assert not any(l.startswith("scripts/run_daily_music_trend_intelligence.py") for l in log)
    assert result.returncode != 0


# =============================================================================
# Daily pipeline R2 backup integration (PRE / POST / capacity check)
# =============================================================================


def test_R2_A_pre_success_runs_main_pipeline_post_and_capacity(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env)
    log = _invocation_log(fake_pipeline_env)
    assert any(l.startswith("scripts/backup_database.py --type pre") for l in log)
    assert any(l.startswith("scripts/run_daily_ingestion.py") for l in log)
    assert any(l.startswith("scripts/deliver_daily_report.py") for l in log)
    assert any(l.startswith("scripts/backup_database.py --type post") for l in log)
    assert any(l.startswith("scripts/backup_database.py --capacity-only") for l in log)
    assert result.returncode == 0
    assert "BACKUP_PRE_START" in result.stdout
    assert "BACKUP_PRE_RESULT=SUCCESS" in result.stdout
    assert "BACKUP_POST_START" in result.stdout
    assert "BACKUP_POST_RESULT=SUCCESS" in result.stdout


def test_R2_B_pre_failure_blocks_main_pipeline_and_post(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_BACKUP_PRE_EXIT": "1"})
    log = _invocation_log(fake_pipeline_env)
    assert any(l.startswith("scripts/backup_database.py --type pre") for l in log)
    assert not any(l.startswith("scripts/run_daily_ingestion.py") for l in log)
    assert not any(l.startswith("scripts/run_daily_music.py") for l in log)
    assert not any(l.startswith("scripts/deliver_daily_report.py") for l in log)
    assert not any(l.startswith("scripts/backup_database.py --type post") for l in log)
    assert not any(l.startswith("scripts/backup_database.py --capacity-only") for l in log)
    assert result.returncode != 0
    assert "BACKUP_PRE_RESULT=FAILED" in result.stdout


def test_R2_B_pre_r2_not_configured_also_blocks(fake_pipeline_env):
    """backup_database.py's exit code 3 (R2_CONFIGURATION_REQUIRED) is
    just as much "no verified backup exists" as exit 1 -- must block the
    same way."""
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_BACKUP_PRE_EXIT": "3"})
    log = _invocation_log(fake_pipeline_env)
    assert not any(l.startswith("scripts/run_daily_ingestion.py") for l in log)
    assert result.returncode != 0
    assert "BACKUP_PRE_RESULT=FAILED" in result.stdout


def test_R2_C_required_stage_failure_still_attempts_post_backup(fake_pipeline_env):
    """DB may already have been mutated by the time a later required stage
    fails -- POST backup should still be attempted rather than skipped."""
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_FORCE_FAIL_SCRIPT": "run_daily_ingestion.py"})
    log = _invocation_log(fake_pipeline_env)
    assert any(l.startswith("scripts/backup_database.py --type post") for l in log)
    assert any(l.startswith("scripts/backup_database.py --capacity-only") for l in log)
    assert result.returncode != 0  # ingestion is a required stage -- existing contract preserved
    assert "STAGE_RESULT ingestion=FAILED" in result.stdout


def test_R2_D_post_backup_failure_is_visible_no_rollback_attempted(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_BACKUP_POST_EXIT": "1"})
    log = _invocation_log(fake_pipeline_env)
    # The pipeline never attempts anything resembling a delete/rollback
    # COMMAND -- confirmed no such command is ever invoked in the real
    # script (explanatory comments about the ABSENCE of deletion do
    # legitimately use the word "delete", so this checks for actual
    # destructive command invocations, not the word itself).
    content = fake_pipeline_env["real_pipeline_script"].read_text(encoding="utf-8")
    assert "rm -" not in content
    assert "--delete" not in content
    assert result.returncode != 0
    assert "BACKUP_POST_RESULT=FAILED" in result.stdout
    assert "CRITICAL: POST-RUN verified R2 backup failed" in result.stdout
    summary_line = next(l for l in result.stdout.splitlines() if l.startswith("=== SUMMARY"))
    assert "backup_post=FAILED" in summary_line  # never hidden from the final summary


def test_R2_E_capacity_failure_does_not_falsify_backup_success(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_CAPACITY_EXIT": "1"})
    assert "BACKUP_PRE_RESULT=SUCCESS" in result.stdout
    assert "BACKUP_POST_RESULT=SUCCESS" in result.stdout
    assert "STAGE_RESULT capacity_check=FAILED exit=1" in result.stdout  # still visible
    assert result.returncode == 0  # a monitoring-only failure never flips the required-stage exit code


@pytest.mark.parametrize("alert_level,expected_required", [
    ("R2_STORAGE_WARNING_70", "1"),
    ("R2_STORAGE_WARNING_85", "1"),
    ("R2_STORAGE_CRITICAL_95", "1"),
    ("R2_STORAGE_EXCEEDED", "1"),
    ("OK", "0"),
])
def test_R2_FGHIK_capacity_alert_required_flag(fake_pipeline_env, alert_level, expected_required):
    result = _run_pipeline(fake_pipeline_env, extra_env={"FAKE_R2_ALERT_LEVEL": alert_level})
    assert f"CAPACITY_ALERT_REQUIRED={expected_required}" in result.stdout


def test_R2_J_forecast_warning_sets_alert_required(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env, extra_env={
        "FAKE_R2_ALERT_LEVEL": "OK", "FAKE_R2_CAPACITY_FORECAST": "CAPACITY_FORECAST_WARNING",
    })
    assert "CAPACITY_ALERT_REQUIRED=1" in result.stdout


def test_R2_LMN_pre_post_news_intelligence_dashboard_each_exactly_once(fake_pipeline_env):
    result = _run_pipeline(fake_pipeline_env)
    log = _invocation_log(fake_pipeline_env)
    assert len([l for l in log if l.startswith("scripts/backup_database.py --type pre")]) == 1
    assert len([l for l in log if l.startswith("scripts/backup_database.py --type post")]) == 1
    assert len([l for l in log if l.startswith("scripts/run_daily_news_intelligence.py")]) == 1
    assert len([l for l in log if l.startswith("scripts/run_daily_music_trend_intelligence.py")]) == 1
    assert len([l for l in log if l.startswith("scripts/generate_daily_web_report_v2.py")]) == 1
    assert result.returncode == 0


def test_R2_no_automatic_deletion_capability_anywhere_in_script():
    """Static check on the real script content: no rm/delete-shaped
    command exists anywhere -- this project's backup tooling has no
    delete capability at all, by construction, not just by policy."""
    real_repo_root = __import__("pathlib").Path(__file__).resolve().parent.parent
    content = (real_repo_root / _PIPELINE_SCRIPT).read_text(encoding="utf-8")
    assert " rm " not in content and not content.strip().startswith("rm ")
    assert "--delete" not in content
    assert "lifecycle" not in content.lower()
