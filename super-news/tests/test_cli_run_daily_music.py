"""TEST A-E from the Daily Music CLI contract: exit-code mapping,
--db-path override honored (default/production DB untouched), and thin
CLI->DB->orchestrator integration. Apple network is mocked; no live call."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_music as cli  # noqa: E402

import sqlite3


def _song(apple_id, name, artist="Some Artist"):
    return {"id": apple_id, "name": name, "artistName": artist}


# ---- TEST A: successful outcome -> exit 0 ------------------------------------


def test_A_successful_collection_exits_ok(tmp_path):
    db_path = tmp_path / "test.db"
    songs = [_song("1", "First")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_OK


# ---- TEST B: failed outcome -> exit 1 ----------------------------------------


def test_B_failed_collection_exits_run_failure(tmp_path):
    db_path = tmp_path / "test.db"
    with patch("music.orchestrator.fetch_kr_most_played", return_value=[]):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_RUN_FAILURE


# ---- TEST C: invalid invocation -> exit 2 (argparse) -------------------------


def test_C_invalid_invocation_exits_config_error():
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--bogus-flag"])
    assert excinfo.value.code == cli.EXIT_CONFIG_ERROR


# ---- TEST D: --db-path override honored; default DB untouched --------------


def test_D_db_path_override_honored_default_untouched(tmp_path, monkeypatch):
    custom_db = tmp_path / "custom.db"
    decoy_default_db = tmp_path / "should_not_be_touched.db"
    monkeypatch.setattr(cli, "DB_PATH", decoy_default_db)

    songs = [_song("1", "First")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        exit_code = cli.main(["--db-path", str(custom_db)])

    assert exit_code == cli.EXIT_OK
    assert custom_db.exists()
    assert not decoy_default_db.exists()


def test_no_db_path_uses_default(tmp_path, monkeypatch):
    default_db = tmp_path / "default.db"
    monkeypatch.setattr(cli, "DB_PATH", default_db)

    songs = [_song("1", "First")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        exit_code = cli.main([])

    assert exit_code == cli.EXIT_OK
    assert default_db.exists()


# ---- TEST E: thin CLI -> DB -> orchestrator integration ---------------------


def test_E_thin_integration_writes_real_rows(tmp_path):
    db_path = tmp_path / "test.db"
    songs = [_song("111", "Track One"), _song("222", "Track Two")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        exit_code = cli.main(["--db-path", str(db_path)])
    assert exit_code == cli.EXIT_OK

    raw = sqlite3.connect(db_path)
    try:
        run_count = raw.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE 'daily-music-apple-kr-%'").fetchone()[0]
        assert run_count == 1
        status_row = raw.execute(
            "SELECT status, items_collected FROM run_source_status WHERE source_name='apple_music'"
        ).fetchone()
        assert status_row == ("SUCCESS", 2)
        obs_count = raw.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
        assert obs_count == 2
        alias_count = raw.execute(
            "SELECT COUNT(*) FROM music_entity_aliases WHERE alias_type='APPLE_MUSIC_ID'"
        ).fetchone()[0]
        assert alias_count == 2
    finally:
        raw.close()
