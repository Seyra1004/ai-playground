"""End-to-end V2 pipeline wiring: Spotify collection -> derived signals ->
web_data_v2 sees real Early Signal data. Proves the wiring, not just each
piece in isolation. Spotify network is mocked; no live call."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_music_signals as signals_cli  # noqa: E402
import run_daily_music_spotify as spotify_cli  # noqa: E402

from db.database import connect
from report.web_data_v2 import build_dashboard_data_v2


def _entry(spotify_id, name, artist, rank):
    return {
        "chartEntryData": {"currentRank": rank, "previousRank": 0, "entryStatus": "MOVED_UP"},
        "trackMetadata": {"trackName": name, "trackUri": f"spotify:track:{spotify_id}",
                           "artists": [{"name": artist}], "releaseDate": ""},
    }


def test_spotify_collection_then_signals_then_web_data_v2_sees_real_early_signal(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)

    # Day 1: track at rank 10.
    day1 = [_entry("1", "Song", "Artist", 10)]
    with patch("music.spotify_orchestrator.fetch_global_top_tracks", return_value=(day1, "2026-08-05")):
        exit_code = spotify_cli.main(["--db-path", str(db_path)])
    assert exit_code == spotify_cli.EXIT_OK

    # Day 2: track jumps to rank 2 (+8) -- a real Early Signal candidate.
    day2 = [_entry("1", "Song", "Artist", 2)]
    with patch("music.spotify_orchestrator.fetch_global_top_tracks", return_value=(day2, "2026-08-12")):
        exit_code = spotify_cli.main(["--db-path", str(db_path)])
    assert exit_code == spotify_cli.EXIT_OK

    # Wire the signals stage.
    exit_code = signals_cli.main(["--db-path", str(db_path), "--report-date", "2026-08-12"])
    assert exit_code == signals_cli.EXIT_OK

    # web_data_v2 must now see the real, persisted Early Signal candidate.
    conn = connect(db_path=db_path)
    try:
        data = build_dashboard_data_v2(conn, "2026-08-12")
    finally:
        conn.close()

    assert data["spotify_chart"]["state"] == "NORMAL"
    candidates = data["intelligence"]["early_signal"]["spotify_chart"]
    assert len(candidates) == 1
    assert candidates[0]["canonical_artist"] == "Artist"
    assert candidates[0]["rank_delta"] == 8.0
    # Cross-platform must still emit nothing: only one real source has data.
    assert data["intelligence"]["cross_platform"] == []
    # Forecast must still be honest: not enough real history yet.
    assert data["intelligence"]["outlook"]["spotify_chart"]["status"] == "INSUFFICIENT_HISTORY"
    # TikTok must never be fabricated.
    assert data["tiktok_chart"]["state"] == "UNAVAILABLE"
