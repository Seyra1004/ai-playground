"""Apple KR Observation Vertical Slice: the 3 consequential invariants
(entity dedup by Apple ID, chart-position mapping, snapshot idempotency),
plus a fetch/parse smoke test. Real scratch SQLite; Apple HTTP is mocked
-- no live network call."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from music.apple_music import collect_kr_most_played_observations, fetch_kr_most_played


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _song(apple_id, name, artist="Some Artist"):
    return {
        "id": apple_id,
        "name": name,
        "artistName": artist,
        "releaseDate": "2026-01-01",
        "kind": "songs",
        "artistId": "999",
        "url": f"https://music.apple.com/kr/song/{apple_id}",
    }


class _FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


# ---- Invariant 1: same Apple ID never duplicates a music_entity ------------


def test_same_apple_id_does_not_duplicate_entity(conn):
    songs = [_song("111", "Track One")]
    collect_kr_most_played_observations(conn, songs, observed_at="2026-08-12T06:00:00+00:00")
    collect_kr_most_played_observations(conn, songs, observed_at="2026-08-13T06:00:00+00:00")

    count = conn.execute(
        """SELECT COUNT(*) FROM music_entities
           JOIN music_entity_aliases ON music_entity_aliases.music_entity_id = music_entities.id
           WHERE music_entity_aliases.alias_type='APPLE_MUSIC_ID' AND music_entity_aliases.alias_value='111'"""
    ).fetchone()[0]
    assert count == 1


# ---- Invariant 2: array order maps correctly to chart position -------------


def test_array_order_maps_to_correct_chart_position(conn):
    songs = [_song("1", "First"), _song("2", "Second"), _song("3", "Third")]
    collect_kr_most_played_observations(conn, songs, observed_at="2026-08-12T06:00:00+00:00")

    rows = conn.execute(
        """SELECT music_entity_aliases.alias_value AS apple_id, music_observations.metric_value
           FROM music_observations
           JOIN music_entity_aliases ON music_entity_aliases.music_entity_id = music_observations.music_entity_id
           WHERE music_entity_aliases.alias_type='APPLE_MUSIC_ID'
           ORDER BY music_observations.metric_value"""
    ).fetchall()
    assert [(r["apple_id"], r["metric_value"]) for r in rows] == [("1", 1), ("2", 2), ("3", 3)]

    row = conn.execute(
        "SELECT metric_name, region, evidence_type, unit FROM music_observations LIMIT 1"
    ).fetchone()
    assert row["metric_name"] == "apple_music_chart_position"
    assert row["region"] == "KR"
    assert row["evidence_type"] == "MEASURED_PLATFORM_SIGNAL"
    assert row["unit"] == "chart_position"


# ---- Invariant 3: snapshot idempotency vs. a genuinely new snapshot --------


def test_same_snapshot_retry_is_idempotent_new_snapshot_is_new_observation(conn):
    songs = [_song("111", "Track One")]

    first = collect_kr_most_played_observations(conn, songs, observed_at="2026-08-12T06:00:00+00:00")
    retry = collect_kr_most_played_observations(conn, songs, observed_at="2026-08-12T06:00:00+00:00")
    next_day = collect_kr_most_played_observations(conn, songs, observed_at="2026-08-13T06:00:00+00:00")

    assert first[0]["status"] == "inserted"
    assert retry[0]["status"] == "duplicate_snapshot"
    assert next_day[0]["status"] == "inserted"

    count = conn.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
    assert count == 2  # one per distinct observed_at, retry produced nothing new


# ---- fetch/parse smoke test (mocked HTTP, no live call) ---------------------


def test_fetch_parses_feed_results_in_order():
    fake_body = {
        "feed": {
            "title": "인기곡",
            "country": "kr",
            "results": [_song("1", "First"), _song("2", "Second")],
        }
    }
    with patch("music.apple_music.request_with_retry", return_value=_FakeResponse(fake_body)) as mock_request:
        songs = fetch_kr_most_played()
    assert [s["id"] for s in songs] == ["1", "2"]
    assert mock_request.call_args.args[0] == "GET"
    assert "kr/music/most-played" in mock_request.call_args.args[1]
