"""music.signal_engine: source-agnostic chart-diff computation. Proves the
engine works for an arbitrary (source_name, metric_name) pair, not just
apple_music -- the point of the V2 source-agnostic refactor."""

import pytest

from db.database import connect, init_db
from music.signal_engine import compute_chart_diff


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_entity(conn, artist, title, source):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', '2026-08-11T00:00:00+00:00', ?)""",
        (artist, title, source),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_observation(conn, entity_id, rank, observed_at, source_name, metric_name):
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, ?, ?, ?, 'chart_position', 'KR',
                   'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, source_name, metric_name, rank, observed_at, observed_at),
    )
    conn.commit()


# ---- genuinely source-agnostic: works for a hypothetical non-apple source --


def test_diff_works_for_an_arbitrary_source_not_apple_music(conn):
    e1 = _insert_entity(conn, "Artist A", "Song A", "hypothetical_source")
    _insert_observation(conn, e1, 3, "2026-08-11T00:00:00+00:00", "hypothetical_source", "some_metric")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "hypothetical_source", "some_metric")

    diff = compute_chart_diff(conn, "2026-08-12", "hypothetical_source", "some_metric")
    assert diff["entries"][0]["rank_delta"] == 2
    assert diff["entries"][0]["is_new"] is False


def test_different_sources_do_not_cross_contaminate(conn):
    e1 = _insert_entity(conn, "Artist A", "Song A", "source_x")
    e2 = _insert_entity(conn, "Artist B", "Song B", "source_y")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "source_x", "metric_x")
    _insert_observation(conn, e2, 1, "2026-08-12T00:00:00+00:00", "source_y", "metric_y")

    diff_x = compute_chart_diff(conn, "2026-08-12", "source_x", "metric_x")
    diff_y = compute_chart_diff(conn, "2026-08-12", "source_y", "metric_y")

    assert [e["music_entity_id"] for e in diff_x["entries"]] == [e1]
    assert [e["music_entity_id"] for e in diff_y["entries"]] == [e2]


def test_new_entry_marked_is_new(conn):
    e1 = _insert_entity(conn, "Artist A", "Song A", "src")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "src", "metric")

    diff = compute_chart_diff(conn, "2026-08-12", "src", "metric")
    assert diff["entries"][0]["is_new"] is True
    assert diff["entries"][0]["rank_delta"] is None


def test_no_snapshot_at_all_returns_empty_diff(conn):
    diff = compute_chart_diff(conn, "2026-08-12", "nonexistent_source", "nonexistent_metric")
    assert diff == {"observed_at": None, "entries": [], "is_first_observation": False}
