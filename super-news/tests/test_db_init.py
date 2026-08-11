import sqlite3

import pytest

from db.database import connect, init_db


def test_init_db_creates_expected_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "runs" in tables
        assert "delivery_history" in tables

        runs_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert {
            "id",
            "run_id",
            "run_date",
            "started_at",
            "finished_at",
            "status",
            "failure_stage",
            "notes",
        } <= runs_columns

        delivery_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(delivery_history)")
        }
        assert {
            "id",
            "run_id",
            "report_date",
            "report_type",
            "destination",
            "idempotency_key",
            "content_hash",
            "delivered_at",
            "status",
        } <= delivery_columns

        indexes = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "ux_delivery_sent_once" in indexes
    finally:
        conn.close()


def test_runs_run_id_is_unique(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        conn.execute(
            "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
            ("r1", "2026-08-12", "2026-08-12T00:00:00+00:00", "running"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
                ("r1", "2026-08-13", "2026-08-13T00:00:00+00:00", "running"),
            )
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    init_db(db_path=db_path)  # must not raise on a second call
