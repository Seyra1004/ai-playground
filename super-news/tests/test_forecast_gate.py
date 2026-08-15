"""music.forecast_gate: minimum-data gate only -- never fabricates a
forecast, honestly reports INSUFFICIENT_HISTORY until real history exists."""

import pytest

from db.database import connect, init_db
from music.forecast_gate import MIN_HISTORY_DAYS, check_forecast_readiness


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_observation(conn, observed_at, source_name="apple_music"):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'A', 'ORIGINAL', 'RESOLVED', ?, ?)""",
        (observed_at, source_name),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_observations
           (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
            unit, region, evidence_type, observed_at, collected_at)
           VALUES (?, NULL, ?, 'metric', 1, 'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
        (entity_id, source_name, observed_at, observed_at),
    )
    conn.commit()


def test_no_observations_at_all_is_insufficient(conn):
    result = check_forecast_readiness(conn, "apple_music")
    assert result == {"status": "INSUFFICIENT_HISTORY", "days_of_history": 0, "min_required_days": MIN_HISTORY_DAYS}


def test_short_history_is_insufficient(conn):
    _insert_observation(conn, "2026-08-01T00:00:00+00:00")
    _insert_observation(conn, "2026-08-12T00:00:00+00:00")
    result = check_forecast_readiness(conn, "apple_music")
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["days_of_history"] == 11


def test_long_enough_history_is_ready(conn):
    _insert_observation(conn, "2026-01-01T00:00:00+00:00")
    _insert_observation(conn, "2026-08-12T00:00:00+00:00")  # ~223 days
    result = check_forecast_readiness(conn, "apple_music")
    assert result["status"] == "READY"
    assert result["days_of_history"] >= MIN_HISTORY_DAYS


def test_different_source_never_counted(conn):
    _insert_observation(conn, "2026-01-01T00:00:00+00:00", source_name="spotify_chart")
    result = check_forecast_readiness(conn, "apple_music")
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["days_of_history"] == 0
