"""scripts/generate_daily_web_report_v2.py: writes docs/v2/index.html + a
dated archive file ONLY under the --docs-dir override (tmp_path) -- never
the real repository docs/ directory, and never V1's docs/ paths. No
network, no LLM, no Kakao, no preview/seed data."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_daily_web_report_v2 as cli  # noqa: E402

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
           VALUES ('테크아웃렛', 'k1', 'rss', 'https://example.com/a', 'AI headline', ?)""",
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
        (run_row_id, json.dumps({
            "AI": [{"id": item_id, "reason": "test"}], "ECONOMY": [], "SOCIETY": [], "TIKTOK": [], "SPOTIFY": [],
        })),
    )
    for category, collected, selected in (
        ("AI", 1, 1), ("ECONOMY", 0, 0), ("SOCIETY", 0, 0), ("TIKTOK", 0, 0), ("SPOTIFY", 0, 0),
    ):
        conn.execute(
            """INSERT INTO run_category_status (run_id, category, status, items_collected, items_selected, retry_count)
               VALUES (?, ?, 'REPORT_GENERATED', ?, ?, 0)""",
            (run_row_id, category, collected, selected),
        )
    conn.commit()
    conn.close()
    return run_row_id


def _insert_producer_intelligence(db_path, report_date="2026-08-13"):
    conn = connect(db_path=db_path)
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('run-pi', ?, 'x', 'completed')",
        (report_date,),
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    stored = {
        "insights": [{
            "what_is_moving": "Artist X - Track Y rising per E1",
            "why_it_matters": "grounded reasoning",
            "what_to_watch": "whether the rise continues",
            "what_could_i_make_now": "Test the hook-first intro next demo",
            "evidence_refs": ["E1"], "confidence": "MEDIUM",
        }],
        "catalog": [{"ref": "E1", "type": "EARLY_SIGNAL", "summary": "[spotify_chart] Artist X - Track Y (+8 rank)"}],
    }
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, 'MUSIC_PRODUCER_INTELLIGENCE', 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, json.dumps(stored, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


# ---- successful generation -> exit 0, correct files under docs/v2/ --------


def test_generates_index_and_archive_under_docs_v2_dir_override(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)

    exit_code = cli.main([
        "--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir),
    ])
    assert exit_code == cli.EXIT_OK

    index_path = docs_dir / "index.html"
    archive_path = docs_dir / "reports" / "2026-08-13.html"
    assert index_path.exists()
    assert archive_path.exists()
    assert index_path.read_text(encoding="utf-8") == archive_path.read_text(encoding="utf-8")
    assert "AI headline" in index_path.read_text(encoding="utf-8")


def test_never_touches_real_repo_docs_v2_dir(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)

    real_index = cli._DOCS_V2_DIR / "index.html"
    before = real_index.read_text(encoding="utf-8") if real_index.exists() else None

    cli.main(["--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir)])

    after = real_index.read_text(encoding="utf-8") if real_index.exists() else None
    assert after == before


def test_v2_output_path_is_a_distinct_namespace_from_v1():
    """docs/v2/ is a subdirectory of docs/ -- structurally separate from
    V1's own docs/index.html and docs/reports/, never the same file."""
    assert cli._DOCS_V2_DIR.name == "v2"
    assert cli._DOCS_V2_DIR.parent.name == "docs"


# ---- Producer Intelligence: persisted before generation appears -----------


def test_producer_intelligence_persisted_before_generation_appears_in_output(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)
    _insert_producer_intelligence(db_path)

    exit_code = cli.main([
        "--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir),
    ])
    assert exit_code == cli.EXIT_OK
    content = (docs_dir / "index.html").read_text(encoding="utf-8")
    assert "Test the hook-first intro next demo" in content
    assert "[spotify_chart] Artist X - Track Y (+8 rank)" in content


def test_no_producer_intelligence_persisted_shows_honest_empty_state(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)

    exit_code = cli.main([
        "--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir),
    ])
    assert exit_code == cli.EXIT_OK
    content = (docs_dir / "index.html").read_text(encoding="utf-8")
    assert "오늘은 근거가 충분하지 않아 프로듀서 인사이트를 생성하지 않았습니다" in content


# ---- no LLM call, no preview data ------------------------------------------


def test_generator_never_imports_or_calls_an_llm():
    """Checks actual import statements, not prose -- the module's own
    docstring legitimately explains it makes no LLM call, which would
    otherwise false-positive a naive substring check."""
    source = open(cli.__file__, encoding="utf-8").read()
    assert "anthropic" not in source.lower()
    import_lines = [line for line in source.splitlines() if line.strip().startswith(("import ", "from "))]
    assert not any("llm" in line.lower() for line in import_lines)


def test_generator_never_references_preview_seed_data():
    source = open(cli.__file__, encoding="utf-8").read().lower()
    assert "preview" not in source
    assert "seed" not in source
    assert "demo" not in source


def test_no_placeholder_artist_or_track_leaks_into_output(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)

    cli.main(["--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir)])
    content = (docs_dir / "index.html").read_text(encoding="utf-8")
    for placeholder in ("Artist A", "Artist B", "New Artist", "New Song", "Example Artist", "Sample Song"):
        assert placeholder not in content


# ---- nothing persisted yet -> still exit 0, honest empty/unavailable page --


def test_no_report_available_still_writes_an_honest_page(tmp_path):
    db_path = tmp_path / "empty.db"
    docs_dir = tmp_path / "docs_v2_out"
    init_db(db_path=db_path)

    exit_code = cli.main([
        "--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir),
    ])
    assert exit_code == cli.EXIT_OK
    content = (docs_dir / "index.html").read_text(encoding="utf-8")
    assert "TikTok 차트 데이터 소스가 아직 연동되지 않았습니다" in content
    assert "Spotify 차트 데이터가 아직 수집되지 않았습니다" in content


# ---- atomic write: a render failure never leaves partial/corrupt output ---


def test_render_failure_writes_nothing_and_preserves_previous_valid_page(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)

    # First, a real successful generation.
    exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir)])
    assert exit_code == cli.EXIT_OK
    good_content = (docs_dir / "index.html").read_text(encoding="utf-8")

    # Now force the renderer to blow up mid-generation.
    with patch("generate_daily_web_report_v2.render_dashboard_html_v2", side_effect=RuntimeError("boom")):
        exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir)])
    assert exit_code == cli.EXIT_UNEXPECTED_ERROR

    # The previously-written good page must be untouched, byte-for-byte.
    assert (docs_dir / "index.html").read_text(encoding="utf-8") == good_content
    # No leftover temp file.
    assert not (docs_dir / "index.html.tmp").exists()


def test_atomic_write_leaves_no_tmp_file_on_success(tmp_path):
    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path)

    cli.main(["--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir)])
    assert not (docs_dir / "index.html.tmp").exists()
    assert not (docs_dir / "reports" / "2026-08-13.html.tmp").exists()


# ---- invalid invocation -> exit 2 (argparse) --------------------------------


def test_invalid_invocation_exits_config_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--bogus-flag"])
    assert excinfo.value.code == cli.EXIT_CONFIG_ERROR


# ---- default report-date is today (KST) ------------------------------------


def test_default_report_date_is_today_kst(tmp_path):
    from datetime import datetime, timedelta, timezone

    _KST = timezone(timedelta(hours=9))
    today = datetime.now(_KST).strftime("%Y-%m-%d")

    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path, report_date=today)

    exit_code = cli.main(["--db-path", str(db_path), "--docs-dir", str(docs_dir)])
    assert exit_code == cli.EXIT_OK
    assert (docs_dir / "reports" / f"{today}.html").exists()


# ---- SAME-DATE generation: title content date must equal REPORT_DATE -------
# report.release_v2.verify_local_v2_dashboard/report.publication_consistency
# both parse the real page <title> to enforce this invariant downstream --
# these tests prove the REAL generator output actually satisfies it, not
# just a synthetic fixture.


def test_generated_index_title_date_equals_report_date(tmp_path):
    from report.publication_consistency import _extract_page_date

    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path, report_date="2026-08-15")

    exit_code = cli.main(["--db-path", str(db_path), "--docs-dir", str(docs_dir), "--report-date", "2026-08-15"])
    assert exit_code == cli.EXIT_OK
    assert _extract_page_date(docs_dir / "index.html") == "2026-08-15"


def test_generated_dated_report_title_date_equals_report_date(tmp_path):
    from report.publication_consistency import _extract_page_date

    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v2_out"
    _insert_run_and_report(db_path, report_date="2026-08-15")

    exit_code = cli.main(["--db-path", str(db_path), "--docs-dir", str(docs_dir), "--report-date", "2026-08-15"])
    assert exit_code == cli.EXIT_OK
    assert _extract_page_date(docs_dir / "reports" / "2026-08-15.html") == "2026-08-15"


# ---- V1 remains functional and untouched -----------------------------------


def test_v1_generator_module_is_not_imported_by_v2_generator():
    source = open(cli.__file__, encoding="utf-8").read()
    assert "report.web_data " not in source and "from report.web_data import" not in source
    assert "report.web_render " not in source and "from report.web_render import" not in source


def test_v1_cli_still_works_independently(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import generate_daily_web_report as v1_cli

    db_path = tmp_path / "test.db"
    docs_dir = tmp_path / "docs_v1_out"
    _insert_run_and_report(db_path)

    exit_code = v1_cli.main(["--db-path", str(db_path), "--report-date", "2026-08-13", "--docs-dir", str(docs_dir)])
    assert exit_code == v1_cli.EXIT_OK
    assert (docs_dir / "index.html").exists()
