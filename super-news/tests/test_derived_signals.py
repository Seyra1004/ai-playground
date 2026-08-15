"""music.derived_signals: source-agnostic VELOCITY writer. Real scratch
SQLite, no network."""

import pytest

from db.database import connect, init_db
from music.derived_signals import compute_velocity_signals


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


def test_unknown_source_raises():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="not in music.registry"):
        compute_velocity_signals(conn, "2026-08-13", "not_a_real_source")


def test_new_entry_produces_no_velocity_row(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")

    written = compute_velocity_signals(conn, "2026-08-12", "apple_music")
    assert written == 0
    count = conn.execute("SELECT COUNT(*) FROM derived_signals").fetchone()[0]
    assert count == 0


def test_moved_entry_produces_velocity_row_matching_rank_delta(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    _insert_observation(conn, e1, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")

    written = compute_velocity_signals(conn, "2026-08-12", "apple_music")
    assert written == 1

    row = conn.execute(
        "SELECT signal_type, value, unit, method_version FROM derived_signals WHERE music_entity_id = ?",
        (e1,),
    ).fetchone()
    assert row["signal_type"] == "VELOCITY"
    assert row["value"] == 3.0  # moved from rank 5 to rank 2 -> +3
    assert row["unit"] == "rank_delta"
    assert row["method_version"] == "v1"


def test_idempotent_retry_writes_nothing_new(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    _insert_observation(conn, e1, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")

    first = compute_velocity_signals(conn, "2026-08-12", "apple_music")
    retry = compute_velocity_signals(conn, "2026-08-12", "apple_music")
    assert first == 1
    assert retry == 0
    count = conn.execute("SELECT COUNT(*) FROM derived_signals").fetchone()[0]
    assert count == 1


def test_different_sources_never_cross_contaminate(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    e2 = _insert_entity(conn, "B", "B", "spotify_chart")
    _insert_observation(conn, e1, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e2, 8, "2026-08-11T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    _insert_observation(conn, e2, 1, "2026-08-12T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")

    apple_written = compute_velocity_signals(conn, "2026-08-12", "apple_music")
    spotify_written = compute_velocity_signals(conn, "2026-08-12", "spotify_chart")
    assert apple_written == 1
    assert spotify_written == 1

    values = {row["music_entity_id"]: row["value"] for row in conn.execute("SELECT music_entity_id, value FROM derived_signals")}
    assert values[e1] == 3.0
    assert values[e2] == 7.0
