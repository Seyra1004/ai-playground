"""report.music_diff: deterministic chart-diff computation (no AI)."""

import pytest

from db.database import connect, init_db
from report.music_diff import compute_music_diff, render_music_report


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_entity(conn, artist, title):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', '2026-08-11T00:00:00+00:00', 'apple_music')""",
        (artist, title),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_observation(conn, entity_id, rank, observed_at):
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, 'apple_music', 'apple_music_chart_position', ?, 'chart_position', 'KR',
                   'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, rank, observed_at, observed_at),
    )
    conn.commit()


# ---- new entry ------------------------------------------------------------


def test_new_entry_marked_is_new(conn):
    e1 = _insert_entity(conn, "Artist A", "Song A")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00")

    diff = compute_music_diff(conn, "2026-08-12")
    assert diff["entries"][0]["is_new"] is True
    assert diff["entries"][0]["rank_delta"] is None


# ---- rank delta calculation -------------------------------------------------


def test_rank_delta_calculation(conn):
    e1 = _insert_entity(conn, "Artist A", "Song A")
    e2 = _insert_entity(conn, "Artist B", "Song B")
    _insert_observation(conn, e1, 3, "2026-08-11T00:00:00+00:00")
    _insert_observation(conn, e2, 1, "2026-08-11T00:00:00+00:00")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00")  # moved up 2
    _insert_observation(conn, e2, 4, "2026-08-12T00:00:00+00:00")  # moved down 3

    diff = compute_music_diff(conn, "2026-08-12")
    by_entity = {e["music_entity_id"]: e for e in diff["entries"]}
    assert by_entity[e1]["rank_delta"] == 2
    assert by_entity[e1]["is_new"] is False
    assert by_entity[e2]["rank_delta"] == -3


def test_entries_ordered_by_today_rank(conn):
    e1 = _insert_entity(conn, "A", "A")
    e2 = _insert_entity(conn, "B", "B")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00")
    _insert_observation(conn, e2, 1, "2026-08-12T00:00:00+00:00")

    diff = compute_music_diff(conn, "2026-08-12")
    assert [e["music_entity_id"] for e in diff["entries"]] == [e2, e1]


# ---- no-prior-day music behavior -------------------------------------------


def test_no_prior_snapshot_all_entries_are_new(conn):
    e1 = _insert_entity(conn, "A", "A")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00")
    diff = compute_music_diff(conn, "2026-08-12")
    assert diff["entries"][0]["is_new"] is True


# ---- zero-music behavior ---------------------------------------------------


def test_no_snapshot_at_all_returns_empty_diff(conn):
    diff = compute_music_diff(conn, "2026-08-12")
    assert diff == {"observed_at": None, "entries": [], "is_first_observation": False}
    assert render_music_report(diff) == "오늘 Apple Music KR 차트 데이터가 없습니다."


# ---- render_music_report is deterministic ----------------------------------


def test_render_music_report_deterministic_and_markers(conn):
    e1 = _insert_entity(conn, "Artist A", "Song A")
    e2 = _insert_entity(conn, "Artist B", "Song B")
    _insert_observation(conn, e1, 1, "2026-08-11T00:00:00+00:00")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00")  # down 1
    _insert_observation(conn, e2, 1, "2026-08-12T00:00:00+00:00")  # new

    diff = compute_music_diff(conn, "2026-08-12")
    rendered_once = render_music_report(diff)
    rendered_twice = render_music_report(diff)
    assert rendered_once == rendered_twice
    assert "(NEW)" in rendered_once
    assert "(▼1)" in rendered_once
