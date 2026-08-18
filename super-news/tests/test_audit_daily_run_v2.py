"""scripts/audit_daily_run_v2.py: read-only log/DB parsing, zero side
effects. Uses synthetic log lines (matching the real logging_setup.py
format) rather than depending on this machine's live logs/super_news.log,
so these tests never depend on -- or mutate -- real production history."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_daily_run_v2 as audit  # noqa: E402

from db.database import connect, init_db


def _success_block_lines(date="2026-08-18"):
    return [
        f"{date} 07:00:00,000 INFO __main__: === SUPER NEWS full daily pipeline START "
        f"dry_run=False db_path=C:\\fake\\super_news.db SUPER_NEWS_NO_PAID_API=1 ===",
        f"{date} 07:00:01,000 INFO __main__: stage=ingestion STARTING cmd=x",
        "run_id=daily-ingestion-20260818T000000Z-aaa111 status=completed",
        f"{date} 07:00:20,000 INFO __main__: stage=ingestion SUCCESS exit=0 elapsed=20.0s",
        f"{date} 07:00:20,100 INFO __main__: stage=apple_music STARTING cmd=x",
        "run_id=daily-music-apple-kr-20260818T000020Z-bbb222 status=completed",
        f"{date} 07:00:22,000 INFO __main__: stage=apple_music SUCCESS exit=0 elapsed=1.9s",
        f"{date} 07:00:22,100 INFO __main__: stage=spotify_music STARTING cmd=x",
        "run_id=daily-music-spotify-20260818T000022Z-ccc333 status=completed",
        f"{date} 07:00:23,000 INFO __main__: stage=spotify_music SUCCESS exit=0 elapsed=0.6s",
        f"{date} 07:00:23,100 INFO __main__: stage=derived_signals STARTING cmd=x",
        "run_id=daily-music-signals-20260818T000023Z-ddd444 status=completed",
        f"{date} 07:00:23,500 INFO __main__: stage=derived_signals SUCCESS exit=0 elapsed=0.4s",
        f"{date} 07:00:23,600 INFO __main__: stage=report_intelligence STARTING cmd=x",
        "run_id=daily-report-20260818T000023Z-eee555 status=completed",
        f"{date} 07:01:10,000 INFO __main__: stage=report_intelligence SUCCESS exit=0 elapsed=44.9s",
        f"{date} 07:01:10,100 INFO __main__: stage=producer_intelligence STARTING cmd=x",
        "run_id=producer-intelligence-20260818T000110Z-fff666 status=completed",
        f"{date} 07:03:05,000 INFO __main__: stage=producer_intelligence SUCCESS exit=0 elapsed=115.0s",
        f"{date} 07:03:05,100 INFO __main__: stage=news_intelligence STARTING cmd=x",
        "run_id=news-intelligence-20260818T000305Z-ggg777 status=completed",
        f"{date} 07:03:49,000 INFO __main__: stage=news_intelligence SUCCESS exit=0 elapsed=43.9s",
        f"{date} 07:03:49,100 INFO __main__: stage=music_trend_intelligence STARTING cmd=x",
        "run_id=music-trend-intelligence-20260818T000349Z-hhh888 status=completed",
        f"{date} 07:04:58,000 INFO __main__: stage=music_trend_intelligence SUCCESS exit=0 elapsed=68.8s",
        f"{date} 07:04:58,100 INFO __main__: stage=kakao_delivery STARTING cmd=x",
        "run_id=daily-kakao-delivery-v2-20260818T000458Z-iii999 status=completed",
        f"{date} 07:05:28,000 INFO __main__: stage=kakao_delivery SUCCESS exit=0 elapsed=30.4s",
        f"{date} 07:05:28,000 INFO __main__: === SUPER NEWS full daily pipeline END "
        f"any_upstream_failure=False delivery_exit=0 ===",
    ]


def _dry_run_block_lines(date="2026-08-18"):
    lines = _success_block_lines(date)
    return [
        line.replace("dry_run=False", "dry_run=True") if "pipeline START" in line else line
        for line in lines
    ]


def _failed_block_lines(date="2026-08-17"):
    lines = _success_block_lines(date)
    out = []
    for line in lines:
        if "stage=producer_intelligence SUCCESS" in line:
            out.append(line.replace("SUCCESS exit=0", "FAILED exit=1"))
        elif "pipeline END" in line:
            out.append(line.replace("any_upstream_failure=False", "any_upstream_failure=True"))
        else:
            out.append(line)
    return out


def test_finds_real_block_and_parses_all_nine_stages():
    block = audit._find_pipeline_block("2026-08-18", lines=_success_block_lines())
    assert block is not None
    assert block["dry_run"] == "False"
    stages = audit._stage_results_from_block(block)
    assert [s["label"] for s in stages] == list(audit._EXPECTED_STAGES)
    assert all(s["status"] == "SUCCESS" for s in stages)


def test_prefers_real_run_over_dry_run_for_same_date():
    lines = _dry_run_block_lines("2026-08-18") + _success_block_lines("2026-08-18")
    block = audit._find_pipeline_block("2026-08-18", lines=lines)
    assert block["dry_run"] == "False"


def test_falls_back_to_dry_run_when_no_real_run_exists():
    block = audit._find_pipeline_block("2026-08-18", lines=_dry_run_block_lines())
    assert block["dry_run"] == "True"


def test_returns_none_for_a_date_with_no_matching_block():
    block = audit._find_pipeline_block("2026-08-19", lines=_success_block_lines("2026-08-18"))
    assert block is None


def test_detects_failed_stage():
    block = audit._find_pipeline_block("2026-08-17", lines=_failed_block_lines())
    stages = audit._stage_results_from_block(block)
    failed = [s["label"] for s in stages if s["status"] == "FAILED"]
    assert failed == ["producer_intelligence"]


def test_stage_run_ids_are_extracted_correctly():
    block = audit._find_pipeline_block("2026-08-18", lines=_success_block_lines())
    stages = audit._stage_results_from_block(block)
    by_label = {s["label"]: s for s in stages}
    assert by_label["report_intelligence"]["run_id"] == "daily-report-20260818T000023Z-eee555"
    assert by_label["music_trend_intelligence"]["run_id"] == "music-trend-intelligence-20260818T000349Z-hhh888"
    assert by_label["kakao_delivery"]["run_id"] == "daily-kakao-delivery-v2-20260818T000458Z-iii999"


# ---- CLI-level: zero side effects, correct exit code -----------------------


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c, db_path
    c.close()


def _row_counts(db_path):
    c = sqlite3.connect(db_path)
    counts = {
        table: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("runs", "llm_interpretations", "delivery_history")
    }
    c.close()
    return counts


def test_main_makes_zero_db_writes_when_nothing_found(conn, capsys):
    _, db_path = conn
    before = _row_counts(db_path)

    with patch("audit_daily_run_v2._log_lines", return_value=iter([])):
        exit_code = audit.main(["--date", "2026-08-18", "--db-path", str(db_path), "--no-http"])

    after = _row_counts(db_path)
    assert before == after
    assert exit_code == audit.EXIT_OK
    out = capsys.readouterr().out
    assert "RUN_FOUND=false" in out
    assert "FINAL_AUDIT_RESULT=FAIL" in out


def test_no_http_flag_skips_the_real_network_call_entirely(conn, capsys):
    """--no-http must genuinely skip the HTTP GET, not just print a
    different label -- patch requests.get to raise if it's ever called."""
    _, db_path = conn
    with patch("audit_daily_run_v2._log_lines", return_value=iter([])):
        with patch("requests.get", side_effect=AssertionError("requests.get must not be called with --no-http")):
            exit_code = audit.main(["--date", "2026-08-18", "--db-path", str(db_path), "--no-http"])
    assert exit_code == audit.EXIT_OK
    out = capsys.readouterr().out
    assert "PUBLIC_MUSIC_VERIFICATION=SKIPPED" in out


def test_main_makes_zero_db_writes_on_a_real_found_run(conn, capsys):
    _, db_path = conn
    before = _row_counts(db_path)
    with patch("audit_daily_run_v2._log_lines", return_value=iter(_success_block_lines())):
        exit_code = audit.main(["--date", "2026-08-18", "--db-path", str(db_path), "--no-http"])
    after = _row_counts(db_path)
    assert before == after
    assert exit_code == audit.EXIT_OK
    out = capsys.readouterr().out
    assert "RUN_FOUND=true" in out
    assert "STAGES_SUCCEEDED=9" in out
