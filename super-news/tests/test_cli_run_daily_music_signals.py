"""Daily music-signals CLI: computes VELOCITY for every active source from
already-persisted observations. No network, no mocking needed -- this
script never calls an external API."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_music_signals as cli  # noqa: E402

from db.database import connect, init_db


def _insert_entity_and_observations(db_path, source_name, metric_name, ranks_by_date):
    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('A', 'A', 'ORIGINAL', 'RESOLVED', ?, ?)""",
        (next(iter(ranks_by_date.values()))[1], source_name),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for rank, observed_at in ranks_by_date.values():
        conn.execute(
            """INSERT INTO music_observations
               (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
                unit, region, evidence_type, observed_at, collected_at)
               VALUES (?, NULL, ?, ?, ?, 'chart_position', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
            (entity_id, source_name, metric_name, rank, observed_at, observed_at),
        )
    conn.commit()
    conn.close()
    return entity_id


# ---- successful run -> exit 0, VELOCITY rows written for both sources ------


def test_successful_run_writes_velocity_for_all_active_sources(tmp_path):
    db_path = tmp_path / "test.db"
    _insert_entity_and_observations(db_path, "apple_music", "apple_music_chart_position", {
        "d1": (5, "2026-08-11T00:00:00+00:00"), "d2": (2, "2026-08-12T00:00:00+00:00"),
    })
    _insert_entity_and_observations(db_path, "spotify_chart", "spotify_chart_rank", {
        "d1": (8, "2026-08-11T00:00:00+00:00"), "d2": (1, "2026-08-12T00:00:00+00:00"),
    })

    exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-12"])
    assert exit_code == cli.EXIT_OK

    raw = sqlite3.connect(db_path)
    try:
        count = raw.execute("SELECT COUNT(*) FROM derived_signals WHERE signal_type='VELOCITY'").fetchone()[0]
        assert count == 2
        run_count = raw.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE 'daily-music-signals-%'").fetchone()[0]
        assert run_count == 1
        statuses = raw.execute(
            "SELECT source_name, status FROM run_source_status WHERE source_name LIKE '%_signals'"
        ).fetchall()
        assert dict(statuses) == {"apple_music_signals": "SUCCESS", "spotify_chart_signals": "SUCCESS"}
    finally:
        raw.close()


# ---- no observations at all -> still exit 0 (SUCCESS with 0 written, not a failure) --


def test_no_observations_yet_is_success_with_zero_written(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)

    exit_code = cli.main(["--db-path", str(db_path), "--report-date", "2026-08-12"])
    assert exit_code == cli.EXIT_OK

    raw = sqlite3.connect(db_path)
    try:
        count = raw.execute("SELECT COUNT(*) FROM derived_signals").fetchone()[0]
        assert count == 0
    finally:
        raw.close()


# ---- invalid invocation -> exit 2 -------------------------------------------


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

    exit_code = cli.main(["--db-path", str(custom_db)])
    assert exit_code == cli.EXIT_OK
    assert custom_db.exists()
    assert not decoy_default_db.exists()
