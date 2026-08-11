"""DB Foundation (v5 architecture) schema tests.

Covers TEST A-N from the DB FOUNDATION IMPLEMENTATION CONTRACT v1 test
matrix, plus live schema introspection and init_db() idempotency proof.
Every behavioral assertion drives a real sqlite3 operation against a real
scratch DB and checks the actual outcome (IntegrityError or row state) —
never a string/grep check against schema.sql."""

import sqlite3

import pytest

from db.database import connect, init_db

EXPECTED_TABLES = {
    "runs",
    "delivery_history",
    "music_entities",
    "music_entity_aliases",
    "trend_entities",
    "raw_items",
    "normalized_items",
    "music_observations",
    "derived_signals",
    "llm_interpretations",
    "music_trend_links",
    "trend_signals",
    "interpretation_items",
    "interpretation_observations",
    "interpretation_signals",
    "interpretation_trend_signals",
    "reports",
    "run_source_status",
    "monthly_forecasts",
    "run_category_status",
    "run_metadata",
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path)
    return path


@pytest.fixture
def conn(db_path):
    c = connect(db_path=db_path)
    yield c
    c.close()


# ---- helpers ---------------------------------------------------------------


def _insert_run(conn, run_id="r1"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
        (run_id, "2026-08-12", "2026-08-12T00:00:00+00:00", "running"),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]


def _insert_music_entity(conn, artist="Artist", title="Title", source="test_source"):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', ?, ?)""",
        (artist, title, "2026-08-12T00:00:00+00:00", source),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_trend_entity(conn, trend_type="GENRE", trend_key="uk_garage", label="UK Garage"):
    conn.execute(
        "INSERT INTO trend_entities (trend_type, trend_key, label, first_seen_at) VALUES (?, ?, ?, ?)",
        (trend_type, trend_key, label, "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_interpretation(conn, run_id):
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'AI', 'claude-opus-5', 'v1', 'text', 'MEDIUM', ?)""",
        (run_id, "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_raw_item(conn, source_name="google_news_rss", source_item_key="key1"):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, collected_at)
           VALUES (?, ?, 'rss', 'https://example.com/a', ?)""",
        (source_name, source_item_key, "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_normalized_item(conn, raw_item_id, event_key="event1", category="AI"):
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, ?, ?, 'title', ?)""",
        (raw_item_id, category, event_key, "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---- live schema introspection (Section 21) --------------------------------


def test_pragma_foreign_keys_enabled_on_connection(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_all_expected_tables_exist(db_path):
    raw = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert EXPECTED_TABLES <= tables
    finally:
        raw.close()


def test_delivery_history_report_id_column_and_fk(db_path):
    raw = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in raw.execute("PRAGMA table_info(delivery_history)")}
        assert "report_id" in cols
        fk_list = raw.execute("PRAGMA foreign_key_list(delivery_history)").fetchall()
        report_fks = [r for r in fk_list if r[3] == "report_id"]
        assert len(report_fks) == 1
        assert report_fks[0][2] == "reports"
        assert report_fks[0][6] == "RESTRICT"
    finally:
        raw.close()


def test_representative_unique_indexes_exist(db_path):
    raw = sqlite3.connect(db_path)
    try:
        obs_indexes = {r[1] for r in raw.execute("PRAGMA index_list(music_observations)") if r[2] == 1}
        assert "ux_observation" in obs_indexes
        sig_indexes = {r[1] for r in raw.execute("PRAGMA index_list(derived_signals)") if r[2] == 1}
        assert "ux_derived_signal" in sig_indexes
        report_indexes = {r[1] for r in raw.execute("PRAGMA index_list(reports)") if r[2] == 1}
        assert "ux_report_run_type" in report_indexes
    finally:
        raw.close()


def test_init_db_idempotent_double_run(db_path):
    raw_before = sqlite3.connect(db_path)
    try:
        tables_before = sorted(r[0] for r in raw_before.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        indexes_before = sorted(r[0] for r in raw_before.execute("SELECT name FROM sqlite_master WHERE type='index'"))
    finally:
        raw_before.close()

    init_db(db_path=db_path)  # second run must not raise and must not change the schema

    raw_after = sqlite3.connect(db_path)
    try:
        tables_after = sorted(r[0] for r in raw_after.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        indexes_after = sorted(r[0] for r in raw_after.execute("SELECT name FROM sqlite_master WHERE type='index'"))
    finally:
        raw_after.close()

    assert tables_before == tables_after
    assert indexes_before == indexes_after


# ---- TEST A: invalid FK insert fails ---------------------------------------


def test_A_invalid_fk_insert_fails(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO music_observations
               (music_entity_id, source_name, metric_name, metric_value, unit, region, evidence_type, observed_at, collected_at)
               VALUES (9999, 'youtube_data_api', 'VIEW_COUNT', 100, 'count', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
            ("2026-08-12T00:00:00+00:00", "2026-08-12T00:00:00+00:00"),
        )


# ---- TEST B: invalid CHECK insert fails ------------------------------------


def test_B_invalid_check_insert_fails(conn):
    entity_id = _insert_music_entity(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO music_observations
               (music_entity_id, source_name, metric_name, metric_value, unit, region, evidence_type, observed_at, collected_at)
               VALUES (?, 'youtube_data_api', 'VIEW_COUNT', -5, 'count', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
            (entity_id, "2026-08-12T00:00:00+00:00", "2026-08-12T00:00:00+00:00"),
        )


# ---- TEST C: raw identity duplicate blocked --------------------------------


def test_C_raw_duplicate_identity_blocked(conn):
    _insert_raw_item(conn, source_name="naver_news_api", source_item_key="same_key")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, collected_at)
               VALUES ('naver_news_api', 'same_key', 'api', 'https://example.com/b', ?)""",
            ("2026-08-12T00:00:00+00:00",),
        )


# ---- TEST D: cross-source corroboration preserved --------------------------


def test_D_cross_source_evidence_preserved(conn):
    raw1 = _insert_raw_item(conn, source_name="naver_news_api", source_item_key="k1")
    raw2 = _insert_raw_item(conn, source_name="google_news_rss", source_item_key="k2")
    n1 = _insert_normalized_item(conn, raw1, event_key="same_event")
    n2 = _insert_normalized_item(conn, raw2, event_key="same_event")
    assert n1 != n2
    count = conn.execute(
        "SELECT COUNT(*) FROM normalized_items WHERE event_key = 'same_event'"
    ).fetchone()[0]
    assert count == 2


# ---- TEST E: observation retry does not duplicate --------------------------


def test_E_observation_retry_duplicate_blocked(conn):
    entity_id = _insert_music_entity(conn)
    obs_sql = """INSERT INTO music_observations
       (music_entity_id, source_name, metric_name, metric_value, unit, region, evidence_type, observed_at, collected_at)
       VALUES (?, 'youtube_data_api', 'VIEW_COUNT', 100, 'count', 'KR', 'MEASURED_PLATFORM_SIGNAL', ?, ?)"""
    conn.execute(obs_sql, (entity_id, "2026-08-12T06:00:00+00:00", "2026-08-12T06:04:00+00:00"))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(obs_sql, (entity_id, "2026-08-12T06:00:00+00:00", "2026-08-12T06:06:00+00:00"))


# ---- TEST F: derived-signal retry does not duplicate ------------------------


def test_F_derived_signal_retry_duplicate_blocked(conn):
    entity_id = _insert_music_entity(conn)
    sig_sql = """INSERT INTO derived_signals
       (music_entity_id, signal_type, period_start, period_end, value, unit, computed_at, method_version)
       VALUES (?, 'VELOCITY', '2026-08-11', '2026-08-12', 1.5, 'count_per_day', ?, 'VELOCITY_v1')"""
    conn.execute(sig_sql, (entity_id, "2026-08-12T06:00:00+00:00"))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sig_sql, (entity_id, "2026-08-12T06:10:00+00:00"))


# ---- TEST G: method_version coexistence ------------------------------------


def test_G_different_method_version_both_preserved(conn):
    entity_id = _insert_music_entity(conn)
    conn.execute(
        """INSERT INTO derived_signals
           (music_entity_id, signal_type, period_start, period_end, value, unit, computed_at, method_version)
           VALUES (?, 'VELOCITY', '2026-08-11', '2026-08-12', 1.5, 'count_per_day', ?, 'VELOCITY_v1')""",
        (entity_id, "2026-08-12T06:00:00+00:00"),
    )
    conn.execute(
        """INSERT INTO derived_signals
           (music_entity_id, signal_type, period_start, period_end, value, unit, computed_at, method_version)
           VALUES (?, 'VELOCITY', '2026-08-11', '2026-08-12', 1.8, 'count_per_day', ?, 'VELOCITY_v2')""",
        (entity_id, "2026-08-12T06:05:00+00:00"),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM derived_signals WHERE music_entity_id=? AND period_start='2026-08-11'",
        (entity_id,),
    ).fetchone()[0]
    assert count == 2


# ---- TEST H: classification revision preserves history ---------------------


def test_H_classification_revision_preserves_history(conn):
    run_id = _insert_run(conn)
    entity_id = _insert_music_entity(conn)
    trend_id_1 = _insert_trend_entity(conn, trend_key="uk_garage", label="UK Garage")
    trend_id_2 = _insert_trend_entity(conn, trend_key="speed_garage", label="Speed Garage")
    interp_1 = _insert_interpretation(conn, run_id)
    interp_2 = _insert_interpretation(conn, run_id)

    conn.execute(
        "INSERT INTO music_trend_links (music_entity_id, trend_entity_id, interpretation_id, confidence, created_at) VALUES (?,?,?,?,?)",
        (entity_id, trend_id_1, interp_1, "MEDIUM", "2026-08-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO music_trend_links (music_entity_id, trend_entity_id, interpretation_id, confidence, created_at) VALUES (?,?,?,?,?)",
        (entity_id, trend_id_2, interp_2, "HIGH", "2026-11-01T00:00:00+00:00"),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT trend_entity_id FROM music_trend_links WHERE music_entity_id=? ORDER BY created_at",
        (entity_id,),
    ).fetchall()
    assert [r[0] for r in rows] == [trend_id_1, trend_id_2]


def test_music_trend_links_interpretation_id_not_null(conn):
    entity_id = _insert_music_entity(conn)
    trend_id = _insert_trend_entity(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO music_trend_links (music_entity_id, trend_entity_id, interpretation_id, confidence, created_at) VALUES (?,?,NULL,?,?)",
            (entity_id, trend_id, "MEDIUM", "2026-08-01T00:00:00+00:00"),
        )


# ---- TEST I: same trend classification + same interpretation blocked -------


def test_I_same_classification_same_interpretation_blocked(conn):
    run_id = _insert_run(conn)
    entity_id = _insert_music_entity(conn)
    trend_id = _insert_trend_entity(conn)
    interp_1 = _insert_interpretation(conn, run_id)

    conn.execute(
        "INSERT INTO music_trend_links (music_entity_id, trend_entity_id, interpretation_id, confidence, created_at) VALUES (?,?,?,?,?)",
        (entity_id, trend_id, interp_1, "MEDIUM", "2026-08-01T00:00:00+00:00"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO music_trend_links (music_entity_id, trend_entity_id, interpretation_id, confidence, created_at) VALUES (?,?,?,?,?)",
            (entity_id, trend_id, interp_1, "HIGH", "2026-08-01T00:01:00+00:00"),
        )


# ---- TEST J: same date, different run -> both reports preserved ------------


def test_J_different_run_reports_both_preserved(conn):
    run_1 = _insert_run(conn, "r1")
    run_2 = _insert_run(conn, "r2")
    conn.execute(
        "INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at) VALUES (?,?,?,?,?,?,?)",
        (run_1, "2026-08-12", "DAILY_AI", "AI", "content v1", "hash1", "2026-08-12T06:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at) VALUES (?,?,?,?,?,?,?)",
        (run_2, "2026-08-12", "DAILY_AI", "AI", "content v2 improved", "hash2", "2026-08-12T07:00:00+00:00"),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE report_date='2026-08-12' AND report_type='DAILY_AI'"
    ).fetchone()[0]
    assert count == 2


# ---- TEST K: same run + report_type duplicate blocked ----------------------


def test_K_same_run_report_type_duplicate_blocked(conn):
    run_1 = _insert_run(conn)
    conn.execute(
        "INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at) VALUES (?,?,?,?,?,?,?)",
        (run_1, "2026-08-12", "DAILY_AI", "AI", "content", "hash1", "2026-08-12T06:00:00+00:00"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at) VALUES (?,?,?,?,?,?,?)",
            (run_1, "2026-08-12", "DAILY_AI", "AI", "content again", "hash2", "2026-08-12T06:05:00+00:00"),
        )


# ---- TEST L: forecast revision across runs preserved ------------------------


def test_L_forecast_revision_different_run_both_preserved(conn):
    run_1 = _insert_run(conn, "r1")
    run_2 = _insert_run(conn, "r2")
    trend_id = _insert_trend_entity(conn)
    interp_1 = _insert_interpretation(conn, run_1)
    interp_2 = _insert_interpretation(conn, run_2)

    forecast_sql = """INSERT INTO monthly_forecasts
       (run_id, interpretation_id, trend_entity_id, forecast_cycle, forecast_created_at,
        forecast_for_start, forecast_for_end, prediction_direction, confidence)
       VALUES (?,?,?,?,?,?,?,?,?)"""
    conn.execute(
        forecast_sql,
        (run_1, interp_1, trend_id, "2026-08", "2026-08-01T00:00:00+00:00", "2026-11-01", "2027-02-01", "RISING", "MEDIUM"),
    )
    conn.execute(
        forecast_sql,
        (run_2, interp_2, trend_id, "2026-08", "2026-08-01T12:00:00+00:00", "2026-11-01", "2027-02-01", "STABLE", "LOW"),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM monthly_forecasts WHERE trend_entity_id=?", (trend_id,)
    ).fetchone()[0]
    assert count == 2


# ---- TEST M: same run + target + period duplicate blocked -------------------


def test_M_same_run_target_period_duplicate_blocked(conn):
    run_1 = _insert_run(conn)
    trend_id = _insert_trend_entity(conn)
    interp_1 = _insert_interpretation(conn, run_1)
    forecast_sql = """INSERT INTO monthly_forecasts
       (run_id, interpretation_id, trend_entity_id, forecast_cycle, forecast_created_at,
        forecast_for_start, forecast_for_end, prediction_direction, confidence)
       VALUES (?,?,?,?,?,?,?,?,?)"""
    conn.execute(
        forecast_sql,
        (run_1, interp_1, trend_id, "2026-08", "2026-08-01T00:00:00+00:00", "2026-11-01", "2027-02-01", "RISING", "MEDIUM"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            forecast_sql,
            (run_1, interp_1, trend_id, "2026-08", "2026-08-01T00:05:00+00:00", "2026-11-01", "2027-02-01", "STABLE", "LOW"),
        )


# ---- TEST N: FORECAST_NOT_READY expressible without a forecast row ---------


def test_N_forecast_not_ready_without_forecast_row(conn):
    run_1 = _insert_run(conn)
    conn.execute(
        "INSERT INTO run_category_status (run_id, category, status) VALUES (?, 'MONTHLY_FORECAST', 'NOT_READY')",
        (run_1,),
    )
    conn.commit()
    forecast_count = conn.execute("SELECT COUNT(*) FROM monthly_forecasts").fetchone()[0]
    assert forecast_count == 0
    status = conn.execute(
        "SELECT status FROM run_category_status WHERE run_id=? AND category='MONTHLY_FORECAST'", (run_1,)
    ).fetchone()[0]
    assert status == "NOT_READY"
