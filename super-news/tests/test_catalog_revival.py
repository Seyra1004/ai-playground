"""music.catalog_revival: gap-based approximation, no schema dependency."""

import pytest

from db.database import connect, init_db
from music.catalog_revival import detect_catalog_revival_candidates


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_entity(conn, first_seen_at, source="apple_music"):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'A', 'ORIGINAL', 'RESOLVED', ?, ?)""",
        (first_seen_at, source),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_observation(conn, entity_id, rank, observed_at, source_name="apple_music", metric_name="apple_music_chart_position"):
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, ?, ?, ?, 'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, source_name, metric_name, rank, observed_at, observed_at),
    )
    conn.commit()


def test_recent_track_with_no_gap_is_not_a_candidate(conn):
    e1 = _insert_entity(conn, "2026-08-01T00:00:00+00:00")
    _insert_observation(conn, e1, 1, "2026-08-11T00:00:00+00:00")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00")

    candidates = detect_catalog_revival_candidates(conn, "2026-08-12", "apple_music")
    assert candidates == []


def test_old_entity_with_long_gap_is_a_candidate(conn):
    e1 = _insert_entity(conn, "2026-01-01T00:00:00+00:00")  # ~7 months old
    _insert_observation(conn, e1, 5, "2026-06-01T00:00:00+00:00")  # last seen ~2 months ago
    _insert_observation(conn, e1, 3, "2026-08-12T00:00:00+00:00")  # reappears

    candidates = detect_catalog_revival_candidates(conn, "2026-08-12", "apple_music")
    assert len(candidates) == 1
    assert candidates[0]["precision"] == "approximate"
    assert candidates[0]["gap_days"] >= 30
    assert candidates[0]["age_days"] >= 90


def test_only_one_observation_ever_is_not_a_candidate(conn):
    e1 = _insert_entity(conn, "2026-01-01T00:00:00+00:00")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00")

    candidates = detect_catalog_revival_candidates(conn, "2026-08-12", "apple_music")
    assert candidates == []


def test_unknown_source_raises():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="not in music.registry"):
        detect_catalog_revival_candidates(conn, "2026-08-13", "not_a_real_source")
