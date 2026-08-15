"""Daily Spotify CLI contract: exit-code mapping, --db-path override
honored, thin CLI->DB->orchestrator integration, and web-layer SKIPPED
(never FAILED) when credentials are absent. Spotify network is mocked; no
live call, no real credentials."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_music_spotify as cli  # noqa: E402


def _entry(spotify_id, name, artist="Some Artist", rank=1):
    return {
        "chartEntryData": {"currentRank": rank, "previousRank": 0, "entryStatus": "MOVED_UP"},
        "trackMetadata": {"trackName": name, "trackUri": f"spotify:track:{spotify_id}",
                           "artists": [{"name": artist}], "releaseDate": ""},
    }


# ---- successful outcome -> exit 0 -------------------------------------------


def test_successful_collection_exits_ok(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)
    entries = [_entry("1", "First")]
    with patch("music.spotify_orchestrator.fetch_global_top_tracks", return_value=(entries, "2026-08-12")):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_OK


# ---- failed outcome -> exit 1 -----------------------------------------------


def test_failed_collection_exits_run_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)
    with patch("music.spotify_orchestrator.fetch_global_top_tracks", return_value=([], "2026-08-12")):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_RUN_FAILURE


# ---- invalid invocation -> exit 2 (argparse) --------------------------------


def test_invalid_invocation_exits_config_error():
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--bogus-flag"])
    assert excinfo.value.code == cli.EXIT_CONFIG_ERROR


# ---- --db-path override honored; default DB untouched ----------------------


def test_db_path_override_honored_default_untouched(tmp_path, monkeypatch):
    custom_db = tmp_path / "custom.db"
    decoy_default_db = tmp_path / "should_not_be_touched.db"
    monkeypatch.setattr(cli, "DB_PATH", decoy_default_db)
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)

    entries = [_entry("1", "First")]
    with patch("music.spotify_orchestrator.fetch_global_top_tracks", return_value=(entries, "2026-08-12")):
        exit_code = cli.main(["--db-path", str(custom_db)])

    assert exit_code == cli.EXIT_OK
    assert custom_db.exists()
    assert not decoy_default_db.exists()


# ---- thin CLI -> DB -> orchestrator integration; web layer SKIPPED ---------


def test_thin_integration_writes_real_rows_web_layer_skipped_without_credentials(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)
    entries = [_entry("111", "Track One"), _entry("222", "Track Two", rank=2)]
    with patch("music.spotify_orchestrator.fetch_global_top_tracks", return_value=(entries, "2026-08-12")):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_OK

    raw = sqlite3.connect(db_path)
    try:
        run_count = raw.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE 'daily-music-spotify-%'").fetchone()[0]
        assert run_count == 1

        chart_status = raw.execute(
            "SELECT status, items_collected FROM run_source_status WHERE source_name='spotify_chart'"
        ).fetchone()
        assert chart_status == ("SUCCESS", 2)

        web_status = raw.execute(
            "SELECT status, items_collected FROM run_source_status WHERE source_name='spotify_web'"
        ).fetchone()
        assert web_status == ("SKIPPED", 0)  # never FAILED just because credentials are absent

        obs_count = raw.execute("SELECT COUNT(*) FROM music_observations WHERE source_name='spotify_chart'").fetchone()[0]
        assert obs_count == 2
        alias_count = raw.execute(
            "SELECT COUNT(*) FROM music_entity_aliases WHERE alias_type='SPOTIFY_ID'"
        ).fetchone()[0]
        assert alias_count == 2
    finally:
        raw.close()
