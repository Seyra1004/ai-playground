"""report.web_data: structured, read-only dashboard data reader. Verifies
the NORMAL/QUIET/DEGRADED classification and that title/reason/source_url
are read from the exact persisted structured facts (llm_interpretations,
normalized_items, raw_items, music_diff) -- no new inference."""

import json

import pytest

from db.database import connect, init_db
from report.web_data import build_dashboard_data


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id="run-1", run_date="2026-08-13"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_normalized_item(conn, key, category, title, source_url="https://example.com/a"):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, title, collected_at)
           VALUES ('s', ?, 'rss', ?, ?, '2026-08-13T00:00:00+00:00')""",
        (key, source_url, title),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, ?, ?, ?, '2026-08-13T00:00:00+00:00')""",
        (raw_id, category, f"ev-{key}", title),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_interpretation(conn, run_row_id, output_dict):
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'NEWS_COMBINED', 'test-model', 'v1', ?, 'MEDIUM', 'x')""",
        (run_row_id, json.dumps(output_dict)),
    )
    conn.commit()


def _insert_reports_marker(conn, run_row_id, report_date="2026-08-13", category="AI"):
    # find_latest_report_run_id() (reused from report_delivery, same
    # resolution Kakao delivery uses) identifies "the latest run for this
    # date" via the `reports` table -- a run with only run_category_status
    # rows and no `reports` row is indistinguishable from "no run at all"
    # to that lookup. Real production runs always write at least one
    # `reports` row whenever any category succeeds; this helper mirrors
    # that for tests that need build_dashboard_data() to resolve a run.
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, ?, ?, ?, 'x', 'hash', 'x')""",
        (run_row_id, report_date, category, category),
    )
    conn.commit()


def _insert_category_status(conn, run_row_id, category, status, items_collected, items_selected):
    conn.execute(
        """INSERT INTO run_category_status
           (run_id, category, status, items_collected, items_selected, retry_count)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (run_row_id, category, status, items_collected, items_selected),
    )
    conn.commit()


# ---- no run at all -> all DEGRADED, never an exception ---------------------


def test_no_run_returns_all_degraded(conn):
    data = build_dashboard_data(conn, "2026-08-13")
    for category in ("AI", "ECONOMY", "SOCIETY"):
        assert data["categories"][category]["state"] == "DEGRADED"
        assert data["categories"][category]["items"] == []
    assert data["categories"]["MUSIC"]["state"] == "DEGRADED"
    assert data["categories"]["MUSIC"]["entries"] == []


# ---- NORMAL: real selection with title/reason/source_url -------------------


def test_normal_state_reads_title_reason_source_url(conn):
    run_row_id = _insert_run(conn)
    _insert_reports_marker(conn, run_row_id)
    item_id = _insert_normalized_item(conn, "k1", "AI_NEWS", "AI Title", "https://example.com/ai")
    _insert_interpretation(conn, run_row_id, {"AI": [{"id": item_id, "reason": "important"}], "ECONOMY": [], "SOCIETY": []})
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    _insert_category_status(conn, run_row_id, "ECONOMY", "REPORT_GENERATED", 0, 0)
    _insert_category_status(conn, run_row_id, "SOCIETY", "REPORT_GENERATED", 0, 0)

    data = build_dashboard_data(conn, "2026-08-13")
    ai = data["categories"]["AI"]
    assert ai["state"] == "NORMAL"
    assert ai["items"] == [{"title": "AI Title", "reason": "important", "source_url": "https://example.com/ai"}]


# ---- QUIET vs DEGRADED must never be confused -------------------------------


def test_quiet_when_candidates_existed_but_nothing_selected(conn):
    run_row_id = _insert_run(conn)
    _insert_reports_marker(conn, run_row_id, category="ECONOMY")
    _insert_interpretation(conn, run_row_id, {"AI": [], "ECONOMY": [], "SOCIETY": []})
    _insert_category_status(conn, run_row_id, "ECONOMY", "REPORT_GENERATED", items_collected=3, items_selected=0)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 0, 0)
    _insert_category_status(conn, run_row_id, "SOCIETY", "REPORT_GENERATED", 0, 0)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["ECONOMY"]["state"] == "QUIET"


def test_degraded_when_zero_candidates_collected(conn):
    # This is the Hankyung-403 shape: status=REPORT_GENERATED (the combined
    # LLM call still ran for other categories) but ECONOMY itself had zero
    # candidates collected -- a source coverage problem, not a quiet day.
    run_row_id = _insert_run(conn)
    _insert_interpretation(conn, run_row_id, {"AI": [], "ECONOMY": [], "SOCIETY": []})
    _insert_category_status(conn, run_row_id, "ECONOMY", "REPORT_GENERATED", items_collected=0, items_selected=0)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 0, 0)
    _insert_category_status(conn, run_row_id, "SOCIETY", "REPORT_GENERATED", 0, 0)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["ECONOMY"]["state"] == "DEGRADED"


def test_degraded_when_not_ready(conn):
    run_row_id = _insert_run(conn)
    _insert_category_status(conn, run_row_id, "AI", "NOT_READY", None, None)
    _insert_category_status(conn, run_row_id, "ECONOMY", "NOT_READY", None, None)
    _insert_category_status(conn, run_row_id, "SOCIETY", "NOT_READY", None, None)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["AI"]["state"] == "DEGRADED"


def test_degraded_when_report_failed(conn):
    run_row_id = _insert_run(conn)
    _insert_category_status(conn, run_row_id, "AI", "REPORT_FAILED", 2, 0)
    _insert_category_status(conn, run_row_id, "ECONOMY", "REPORT_GENERATED", 0, 0)
    _insert_category_status(conn, run_row_id, "SOCIETY", "REPORT_GENERATED", 0, 0)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["AI"]["state"] == "DEGRADED"


# ---- source_url omission (empty string treated as absent) ------------------


def test_empty_source_url_is_treated_as_absent(conn):
    run_row_id = _insert_run(conn)
    _insert_reports_marker(conn, run_row_id)
    item_id = _insert_normalized_item(conn, "k1", "AI_NEWS", "AI Title", source_url="")
    _insert_interpretation(conn, run_row_id, {"AI": [{"id": item_id, "reason": "r"}], "ECONOMY": [], "SOCIETY": []})
    _insert_category_status(conn, run_row_id, "AI", "REPORT_GENERATED", 1, 1)
    _insert_category_status(conn, run_row_id, "ECONOMY", "REPORT_GENERATED", 0, 0)
    _insert_category_status(conn, run_row_id, "SOCIETY", "REPORT_GENERATED", 0, 0)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["AI"]["items"][0]["source_url"] == ""


# ---- music: reuses compute_music_diff directly ------------------------------


def _insert_music_entity_and_observation(conn, artist, title, rank, observed_at="2026-08-13T00:00:00+00:00"):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', ?, 'apple_music')""",
        (artist, title, observed_at),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, 'apple_music', 'apple_music_chart_position', ?, 'chart_position', 'KR',
                   'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, rank, observed_at, observed_at),
    )
    conn.commit()


def test_music_normal_state_uses_compute_music_diff(conn):
    run_row_id = _insert_run(conn)
    _insert_reports_marker(conn, run_row_id, category="MUSIC")
    _insert_music_entity_and_observation(conn, "Artist A", "Song A", 1)
    _insert_category_status(conn, run_row_id, "MUSIC", "REPORT_GENERATED", 1, 1)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["MUSIC"]["state"] == "NORMAL"
    assert data["categories"]["MUSIC"]["entries"][0]["canonical_artist"] == "Artist A"


def test_music_degraded_when_no_snapshot(conn):
    run_row_id = _insert_run(conn)
    _insert_category_status(conn, run_row_id, "MUSIC", "NOT_READY", None, None)

    data = build_dashboard_data(conn, "2026-08-13")
    assert data["categories"]["MUSIC"]["state"] == "DEGRADED"
