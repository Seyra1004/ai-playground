"""Spotify Chart Observation Vertical Slice: the same 3 consequential
invariants as Apple Music (entity dedup by Spotify ID, rank mapping,
snapshot idempotency), plus a fetch/parse smoke test against the verified
response shape. Real scratch SQLite; Spotify HTTP is mocked -- no live
network call."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from music.spotify_chart import collect_global_top_tracks_observations, fetch_global_top_tracks


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _entry(spotify_id, name, artist="Some Artist", rank=1, previous_rank=0):
    return {
        "chartEntryData": {"currentRank": rank, "previousRank": previous_rank, "entryStatus": "MOVED_UP"},
        "trackMetadata": {
            "trackName": name,
            "trackUri": f"spotify:track:{spotify_id}",
            "artists": [{"name": artist, "spotifyUri": "spotify:artist:x"}],
            "releaseDate": "",
        },
    }


class _FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


# ---- Invariant 1: same Spotify ID never duplicates a music_entity ----------


def test_same_spotify_id_does_not_duplicate_entity(conn):
    entries = [_entry("111", "Track One", rank=1)]
    collect_global_top_tracks_observations(conn, entries, "2026-08-12", observed_at="2026-08-12T00:00:00+00:00")
    collect_global_top_tracks_observations(conn, entries, "2026-08-19", observed_at="2026-08-19T00:00:00+00:00")

    count = conn.execute(
        """SELECT COUNT(*) FROM music_entities
           JOIN music_entity_aliases ON music_entity_aliases.music_entity_id = music_entities.id
           WHERE music_entity_aliases.alias_type='SPOTIFY_ID' AND music_entity_aliases.alias_value='111'"""
    ).fetchone()[0]
    assert count == 1


# ---- Invariant 2: rank maps correctly, up to CHART_LIMIT --------------------


def test_rank_maps_to_correct_chart_position(conn):
    entries = [_entry("1", "First", rank=1), _entry("2", "Second", rank=2), _entry("3", "Third", rank=3)]
    collect_global_top_tracks_observations(conn, entries, "2026-08-12", observed_at="2026-08-12T00:00:00+00:00")

    rows = conn.execute(
        """SELECT music_entity_aliases.alias_value AS spotify_id, music_observations.metric_value
           FROM music_observations
           JOIN music_entity_aliases ON music_entity_aliases.music_entity_id = music_observations.music_entity_id
           WHERE music_entity_aliases.alias_type='SPOTIFY_ID'
           ORDER BY music_observations.metric_value"""
    ).fetchall()
    assert [(r["spotify_id"], r["metric_value"]) for r in rows] == [("1", 1), ("2", 2), ("3", 3)]

    row = conn.execute(
        "SELECT metric_name, region, evidence_type, unit FROM music_observations LIMIT 1"
    ).fetchone()
    assert row["metric_name"] == "spotify_chart_rank"
    assert row["region"] == "GLOBAL"
    assert row["evidence_type"] == "MEASURED_PLATFORM_SIGNAL"
    assert row["unit"] == "chart_rank"


def test_only_top_10_persisted_even_if_more_entries_given(conn):
    entries = [_entry(str(i), f"Track {i}", rank=i) for i in range(1, 21)]
    outcomes = collect_global_top_tracks_observations(conn, entries, "2026-08-12", observed_at="2026-08-12T00:00:00+00:00")
    assert len(outcomes) == 10
    count = conn.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
    assert count == 10


# ---- Invariant 3: snapshot idempotency vs. a genuinely new snapshot --------


def test_same_snapshot_retry_is_idempotent_new_snapshot_is_new_observation(conn):
    entries = [_entry("111", "Track One", rank=1)]

    first = collect_global_top_tracks_observations(conn, entries, "2026-08-12", observed_at="2026-08-12T00:00:00+00:00")
    retry = collect_global_top_tracks_observations(conn, entries, "2026-08-12", observed_at="2026-08-12T00:00:00+00:00")
    next_week = collect_global_top_tracks_observations(conn, entries, "2026-08-19", observed_at="2026-08-19T00:00:00+00:00")

    assert first[0]["status"] == "inserted"
    assert retry[0]["status"] == "duplicate_snapshot"
    assert next_week[0]["status"] == "inserted"

    count = conn.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
    assert count == 2


# ---- variant identity: distinct Spotify IDs never merged -------------------


def test_distinct_spotify_ids_never_merged_even_with_same_title(conn):
    entries = [_entry("111", "Song (Sped Up)", artist="Artist", rank=1),
               _entry("222", "Song", artist="Artist", rank=2)]
    collect_global_top_tracks_observations(conn, entries, "2026-08-12", observed_at="2026-08-12T00:00:00+00:00")

    entity_count = conn.execute("SELECT COUNT(*) FROM music_entities").fetchone()[0]
    assert entity_count == 2


# ---- fetch/parse smoke test (mocked HTTP, no live call) ---------------------


def test_fetch_parses_chart_response_shape():
    fake_body = {
        "chartEntryViewResponses": [
            {
                "displayChart": {"chartMetadata": {"dimensions": {"latestDate": "2026-08-06", "country": "GLOBAL", "recurrence": "WEEKLY"}}},
                "entries": [_entry("1", "First", rank=1), _entry("2", "Second", rank=2)],
            }
        ]
    }
    with patch("music.spotify_chart.request_with_retry", return_value=_FakeResponse(fake_body)) as mock_request:
        entries, chart_date = fetch_global_top_tracks()
    assert [e["trackMetadata"]["trackName"] for e in entries] == ["First", "Second"]
    assert chart_date == "2026-08-06"
    assert mock_request.call_args.args[0] == "GET"
    assert "charts-spotify-com-service.spotify.com" in mock_request.call_args.args[1]


def test_fetch_raises_on_missing_entries_field():
    with patch("music.spotify_chart.request_with_retry", return_value=_FakeResponse({"chartEntryViewResponses": [{}]})):
        with pytest.raises(ValueError):
            fetch_global_top_tracks()
