"""End-to-end proof that music/entity_resolution.py actually closes the
gap music/cross_platform.py's own docstring diagnosed: two different
sources' collectors, observing the same real-world track, now attach to
the SAME music_entity_id -- and music.cross_platform.
detect_cross_platform_signals (unchanged logic) starts actually returning
a hit once that's true. Real scratch SQLite; no live network calls."""

import pytest

from db.database import connect, init_db
from music.apple_music import collect_kr_most_played_observations
from music.cross_platform import detect_cross_platform_signals
from music.derived_signals import compute_velocity_signals
from music.spotify_chart import collect_global_top_tracks_observations


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _spotify_entry(spotify_id, name, artist, rank):
    return {
        "chartEntryData": {"currentRank": rank, "previousRank": 0, "entryStatus": "MOVED_UP"},
        "trackMetadata": {"trackName": name, "trackUri": f"spotify:track:{spotify_id}",
                           "artists": [{"name": artist}], "releaseDate": ""},
    }


def _apple_song(apple_id, name, artist):
    return {"id": apple_id, "name": name, "artistName": artist, "releaseDate": "2026-01-01",
            "kind": "songs", "artistId": "999", "url": f"https://music.apple.com/kr/song/{apple_id}"}


def _entity_count_for_artist_title(conn, artist, title):
    return conn.execute(
        "SELECT COUNT(*) FROM music_entities WHERE canonical_artist = ? AND canonical_title = ?",
        (artist, title),
    ).fetchone()[0]


# ---- same real track, two sources -> ONE entity, not two -------------------


def test_same_track_across_spotify_and_apple_music_shares_one_entity(conn):
    collect_global_top_tracks_observations(
        conn, [_spotify_entry("sp1", "Dynamite", "BTS", 5)], "2026-08-13",
        observed_at="2026-08-13T00:00:00+00:00",
    )
    collect_kr_most_played_observations(
        conn, [_apple_song("am1", "Dynamite", "BTS")], observed_at="2026-08-13T01:00:00+00:00",
    )

    assert _entity_count_for_artist_title(conn, "BTS", "Dynamite") == 1

    entity_id = conn.execute(
        "SELECT id FROM music_entities WHERE canonical_artist = 'BTS' AND canonical_title = 'Dynamite'"
    ).fetchone()["id"]
    alias_sources = {
        row["alias_type"] for row in conn.execute(
            "SELECT alias_type FROM music_entity_aliases WHERE music_entity_id = ?", (entity_id,)
        ).fetchall()
    }
    assert alias_sources == {"SPOTIFY_ID", "APPLE_MUSIC_ID"}


def test_remix_on_second_source_never_merges_with_original(conn):
    collect_global_top_tracks_observations(
        conn, [_spotify_entry("sp2", "Butter", "BTS", 3)], "2026-08-13",
        observed_at="2026-08-13T00:00:00+00:00",
    )
    collect_kr_most_played_observations(
        conn, [_apple_song("am2", "Butter (Remix)", "BTS")], observed_at="2026-08-13T01:00:00+00:00",
    )
    assert _entity_count_for_artist_title(conn, "BTS", "Butter") == 1
    assert _entity_count_for_artist_title(conn, "BTS", "Butter (Remix)") == 1


# ---- cross_platform.py's own (unchanged) logic now actually fires ----------


def test_cross_platform_signal_fires_once_two_sources_resolve_to_one_entity(conn):
    day1 = "2026-08-12T00:00:00+00:00"
    day2 = "2026-08-13T00:00:00+00:00"

    # Day 1: both sources see the track at a lower rank/position. Apple
    # Music's chart_position is the song's 1-indexed LIST position (no
    # explicit rank field in its feed) -- two filler songs ahead of it
    # gives it position 3 on day 1.
    collect_global_top_tracks_observations(conn, [_spotify_entry("sp3", "Hype Boy", "NewJeans", 9)],
                                            "2026-08-12", observed_at=day1)
    collect_kr_most_played_observations(
        conn,
        [_apple_song("filler1", "Filler One", "Filler Artist"),
         _apple_song("filler2", "Filler Two", "Filler Artist"),
         _apple_song("am3", "Hype Boy", "NewJeans")],
        observed_at=day1,
    )

    # Day 2: both sources see it rise -> positive velocity on both.
    collect_global_top_tracks_observations(conn, [_spotify_entry("sp3", "Hype Boy", "NewJeans", 3)],
                                            "2026-08-13", observed_at=day2)
    collect_kr_most_played_observations(conn, [_apple_song("am3", "Hype Boy", "NewJeans")], observed_at=day2)

    compute_velocity_signals(conn, "2026-08-13", "spotify_chart")
    compute_velocity_signals(conn, "2026-08-13", "apple_music")

    hits = detect_cross_platform_signals(conn, "2026-08-13")
    entity_id = conn.execute(
        "SELECT id FROM music_entities WHERE canonical_artist = 'NewJeans' AND canonical_title = 'Hype Boy'"
    ).fetchone()["id"]
    matching = [h for h in hits if h["music_entity_id"] == entity_id]
    assert len(matching) == 1
    assert set(matching[0]["sources"]) == {"spotify_chart", "apple_music"}


def test_single_source_never_becomes_a_cross_platform_signal(conn):
    day1 = "2026-08-12T00:00:00+00:00"
    day2 = "2026-08-13T00:00:00+00:00"
    collect_global_top_tracks_observations(conn, [_spotify_entry("sp4", "Solo Track", "Solo Artist", 9)],
                                            "2026-08-12", observed_at=day1)
    collect_global_top_tracks_observations(conn, [_spotify_entry("sp4", "Solo Track", "Solo Artist", 2)],
                                            "2026-08-13", observed_at=day2)
    compute_velocity_signals(conn, "2026-08-13", "spotify_chart")
    hits = detect_cross_platform_signals(conn, "2026-08-13")
    assert hits == []


# ---- no LLM, no fabrication -------------------------------------------------


def test_entity_resolution_module_never_imports_an_llm():
    import music.entity_resolution as mod
    source = open(mod.__file__, encoding="utf-8").read().lower()
    assert "anthropic" not in source
    assert "llm" not in source


def test_renderer_module_never_imports_db_or_entity_resolution():
    import report.web_render_v2 as mod
    source = open(mod.__file__, encoding="utf-8").read()
    assert "import sqlite3" not in source
    assert "music.entity_resolution" not in source
    assert "conn.execute" not in source
