"""music.cross_platform: the >=2-real-sources evidence gate. A synthetic
fixture (one entity observed by two source_names, simulating a future
resolved/merged entity) proves the labeling logic; today's real
single-source-per-entity production state must emit nothing."""

import pytest

from db.database import connect, init_db
from music.cross_platform import detect_cross_platform_signals
from music.derived_signals import compute_velocity_signals


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_entity(conn, source):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'A', 'ORIGINAL', 'RESOLVED', '2026-08-01T00:00:00+00:00', ?)""",
        (source,),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_observation(conn, entity_id, rank, observed_at, source_name, metric_name):
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, ?, ?, ?, 'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, source_name, metric_name, rank, observed_at, observed_at),
    )
    conn.commit()


# ---- current production reality: separate entities per source -> nothing --


def test_two_separate_entities_never_produce_a_label(conn):
    """Today's real collectors each create their OWN entity keyed to their
    own platform ID -- no entity resolution/merge step exists yet, so two
    distinct entities (even if they represent the same real song) must
    never be combined into a cross-platform label."""
    e_apple = _insert_entity(conn, "apple_music")
    e_spotify = _insert_entity(conn, "spotify_chart")
    _insert_observation(conn, e_apple, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e_apple, 2, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e_spotify, 8, "2026-08-11T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    _insert_observation(conn, e_spotify, 1, "2026-08-12T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    compute_velocity_signals(conn, "2026-08-12", "spotify_chart")

    labels = detect_cross_platform_signals(conn, "2026-08-12")
    assert labels == []


def test_single_source_only_emits_nothing(conn):
    e1 = _insert_entity(conn, "apple_music")
    _insert_observation(conn, e1, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    compute_velocity_signals(conn, "2026-08-12", "apple_music")

    labels = detect_cross_platform_signals(conn, "2026-08-12")
    assert labels == []


# ---- synthetic fixture: proves the labeling logic itself works -----------
# Simulates a future state where entity resolution has already merged two
# platforms' observations onto ONE music_entity_id.


def test_one_entity_with_positive_velocity_on_two_sources_is_a_hit(conn):
    e1 = _insert_entity(conn, "apple_music")
    _insert_observation(conn, e1, 5, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 2, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 8, "2026-08-11T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    compute_velocity_signals(conn, "2026-08-12", "spotify_chart")

    labels = detect_cross_platform_signals(conn, "2026-08-12")
    assert len(labels) == 1
    assert labels[0]["label"] == "CROSS_PLATFORM_HIT"
    assert labels[0]["sources"] == ["apple_music", "spotify_chart"]
    assert labels[0]["music_entity_id"] == e1


def test_negative_velocity_on_one_side_does_not_count_as_evidence(conn):
    e1 = _insert_entity(conn, "apple_music")
    _insert_observation(conn, e1, 2, "2026-08-11T00:00:00+00:00", "apple_music", "apple_music_chart_position")
    _insert_observation(conn, e1, 5, "2026-08-12T00:00:00+00:00", "apple_music", "apple_music_chart_position")  # moved DOWN
    _insert_observation(conn, e1, 8, "2026-08-11T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    _insert_observation(conn, e1, 1, "2026-08-12T00:00:00+00:00", "spotify_chart", "spotify_chart_rank")
    compute_velocity_signals(conn, "2026-08-12", "apple_music")
    compute_velocity_signals(conn, "2026-08-12", "spotify_chart")

    labels = detect_cross_platform_signals(conn, "2026-08-12")
    assert labels == []
