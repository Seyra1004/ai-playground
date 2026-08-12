"""scripts/generate_daily_web_report.py: writes docs/index.html + a dated
archive file ONLY under the --docs-dir override (tmp_path) -- never the
real repository docs/ directory. No network, no LLM, no Kakao."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_daily_web_report as cli  # noqa: E402

from db.database import connect, init_db


def _insert_run_and_report(db_path, report_date="2026-08-13"):
    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('run-1', ?, 'x', 'completed')",
        (report_date,),
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, title, collected_at)
           VALUES ('s', 'k1', 'rss', 'https://example.com/a', 'AI headline', ?)""",
        (report_date + "T00:00:00+00:00",),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, 'AI_NEWS', 'ev-1', 'AI headline', ?)""",
        (raw_id, report_date + "T00:00:00+00:00"),
    )
    item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, ?, 'AI', 'AI', 'x', 'hash', 'x')""",
        (run_row_id, report_date),
    )
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'NEWS_COMBINED', 'm', 'v1', ?, 'MEDIUM', 'x')""",
        (run_row_id, '{"AI": [{"id": %d, "reason": "test"}], "ECONOMY": [], "SOCIETY": []}' % item_id),
    )
    for category, collected, selected in (("AI", 1, 1), ("ECONOMY", 0, 0), ("SOCIETY", 0, 0)):
        conn.execute(
            """INSERT INTO run_category_status (run_id, category, status, items_collected, items_selected, retry_count)
               VALUES (?, ?, 'REPORT_GENERATED', ?, ?, 0)""",
            (run_row_id, category, collected, selected),
        )
    conn.commit()
    conn.close()


# ---- successful generation -> exit 0, correct files written ---------------


def test_generates_index_and_archive_under_docs_dir_override(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_out"
    _insert_run_and_report(db_path)

    exit_code = cli.main([
        "--db-path", str(db_path),
        "--report-date", "2026-08-13",
        "--docs-dir", str(docs_dir),
    ])
    assert exit_code == cli.EXIT_OK

    index_path = docs_dir / "index.html"
    archive_path = docs_dir / "reports" / "2026-08-13.html"
    assert index_path.exists()
    assert archive_path.exists()
    assert index_path.read_text(encoding="utf-8") == archive_path.read_text(encoding="utf-8")
    assert "AI headline" in index_path.read_text(encoding="utf-8")


def test_never_touches_real_repo_docs_dir(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_out"
    _insert_run_and_report(db_path)

    real_docs_index = cli._DOCS_DIR / "index.html"
    before = real_docs_index.read_text(encoding="utf-8") if real_docs_index.exists() else None

    cli.main([
        "--db-path", str(db_path),
        "--report-date", "2026-08-13",
        "--docs-dir", str(docs_dir),
    ])

    after = real_docs_index.read_text(encoding="utf-8") if real_docs_index.exists() else None
    assert after == before  # untouched, byte-for-byte (or still absent)


# ---- nothing persisted yet -> still exit 0, honest all-DEGRADED page ------


def test_no_report_available_still_writes_an_honest_degraded_page(tmp_path):
    db_path = tmp_path / "empty.db"
    docs_dir = tmp_path / "docs_out"
    init_db(db_path=db_path)

    exit_code = cli.main([
        "--db-path", str(db_path),
        "--report-date", "2026-08-13",
        "--docs-dir", str(docs_dir),
    ])
    assert exit_code == cli.EXIT_OK
    index_path = docs_dir / "index.html"
    assert index_path.exists()
    from report.web_render import DEGRADED_MESSAGE
    assert DEGRADED_MESSAGE in index_path.read_text(encoding="utf-8")


# ---- invalid invocation -> exit 2 (argparse) --------------------------------


def test_invalid_invocation_exits_config_error():
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--bogus-flag"])
    assert excinfo.value.code == cli.EXIT_CONFIG_ERROR


# ---- default report-date is today (KST) ------------------------------------


def test_default_report_date_is_today_kst(tmp_path):
    from datetime import datetime, timedelta, timezone

    _KST = timezone(timedelta(hours=9))
    today = datetime.now(_KST).strftime("%Y-%m-%d")

    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_out"
    _insert_run_and_report(db_path, report_date=today)

    exit_code = cli.main(["--db-path", str(db_path), "--docs-dir", str(docs_dir)])
    assert exit_code == cli.EXIT_OK
    assert (docs_dir / "reports" / f"{today}.html").exists()
