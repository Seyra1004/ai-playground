"""music.early_signal: per-source candidate selection, never cross-platform,
never below the MIN_RANK_DELTA confidence floor."""

import pytest

from db.database import connect, init_db
from music.derived_signals import compute_velocity_signals
from music.early_signal import select_early_signal_candidates


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


def test_below_min_rank_delta_excluded(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    _insert_observation(conn, e1, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 4, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")  # +1, below MIN_RANK_DELTA=2

    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    candidates = select_early_signal_candidates(conn, "2026-08-12", "apple_music")
    assert candidates == []


def test_qualifying_candidate_tagged_with_source_name(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    _insert_observation(conn, e1, 10, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 3, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")  # +7

    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    candidates = select_early_signal_candidates(conn, "2026-08-12", "apple_music")
    assert len(candidates) == 1
    assert candidates[0]["source_name"] == "apple_music"
    assert candidates[0]["rank_delta"] == 7.0


def test_ordered_by_largest_delta_first(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    e2 = _insert_entity(conn, "B", "B", "apple_music")
    _insert_observation(conn, e1, 10, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 5, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")  # +5
    _insert_observation(conn, e2, 20, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e2, 5, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")  # +15

    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    candidates = select_early_signal_candidates(conn, "2026-08-12", "apple_music")
    assert [c["music_entity_id"] for c in candidates] == [e2, e1]


def test_never_returns_a_different_sources_entity(conn):
    """A Spotify-sourced entity with a huge VELOCITY must never appear when
    querying for apple_music -- no cross-platform blending."""
    e_apple = _insert_entity(conn, "A", "A", "apple_music")
    e_spotify = _insert_entity(conn, "B", "B", "spotify_chart")
    _insert_observation(conn, e_apple, 10, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e_apple, 8, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")  # +2
    _insert_observation(conn, e_spotify, 10, "2026-08-11T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    _insert_observation(conn, e_spotify, 1, "2026-08-12T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")  # +9

    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    compute_velocity_signals(conn, "2026-08-12", "spotify_chart")

    apple_candidates = select_early_signal_candidates(conn, "2026-08-12", "apple_music")
    assert [c["music_entity_id"] for c in apple_candidates] == [e_apple]
    assert all(c["source_name"] == "apple_music" for c in apple_candidates)


def test_no_derived_signals_yet_returns_empty_not_error(conn):
    e1 = _insert_entity(conn, "A", "A", "apple_music")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    # compute_velocity_signals() was never called -- no derived_signals rows exist.
    candidates = select_early_signal_candidates(conn, "2026-08-12", "apple_music")
    assert candidates == []


def test_unknown_source_raises():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="not in music.registry"):
        select_early_signal_candidates(conn, "2026-08-13", "not_a_real_source")
