"""run_category_status.category MUSIC migration: safe SQLite table-rebuild.

Uses a SYNTHETIC old-schema fixture (schema.sql's CHECK list with 'MUSIC'
stripped back out, executed fresh) rather than relying on the real table's
current emptiness -- this proves the migration works against a populated,
pre-migration production-shaped table, not just an empty one.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "db" / "migrations" / "002_add_music_to_run_category_status.py"
)
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_002", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration_002 = _load_migration_module()


def _old_schema_sql():
    """The real schema.sql with the MUSIC value removed from the
    run_category_status CHECK -- i.e. exactly what production looked like
    before this migration, byte-for-byte identical otherwise."""
    current = _SCHEMA_PATH.read_text(encoding="utf-8")
    old = current.replace(
        "('TIKTOK','SPOTIFY','AI','ECONOMY','SOCIETY','MONTHLY_FORECAST','MUSIC')",
        "('TIKTOK','SPOTIFY','AI','ECONOMY','SOCIETY','MONTHLY_FORECAST')",
    )
    assert old != current, "fixture setup failed to locate the CHECK clause in schema.sql"
    return old


@pytest.fixture
def old_schema_conn(tmp_path):
    db_path = tmp_path / "old_schema.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_old_schema_sql())
    conn.commit()
    yield conn
    conn.close()


def _seed_run_and_reports(conn):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('run-1', '2026-08-11', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, '2026-08-11', 'AI', 'AI', 'body', 'hash1', 'x')""",
        (run_row_id,),
    )
    report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return run_row_id, report_id


# ---- B: migration runs successfully against a populated old-schema table ---


def test_B_migration_applies_against_populated_old_schema(old_schema_conn):
    run_row_id, report_id = _seed_run_and_reports(old_schema_conn)
    old_schema_conn.execute(
        """INSERT INTO run_category_status
           (run_id, category, status, report_id, items_collected, items_rejected, items_selected, retry_count)
           VALUES (?, 'AI', 'REPORT_GENERATED', ?, 10, 2, 3, 0)""",
        (run_row_id, report_id),
    )
    old_schema_conn.commit()

    ran = migration_002.apply_migration(old_schema_conn)
    assert ran is True


# ---- C: existing rows survive the migration, byte-for-byte -----------------


def test_C_existing_rows_survive_migration(old_schema_conn):
    run_row_id, report_id = _seed_run_and_reports(old_schema_conn)
    old_schema_conn.execute(
        """INSERT INTO run_category_status
           (run_id, category, status, failure_stage, report_id, items_collected,
            items_rejected, items_selected, failure_reason, retry_count)
           VALUES (?, 'ECONOMY', 'REPORT_FAILED', 'LLM', NULL, 5, 1, 0, 'hallucinated id', 2)""",
        (run_row_id,),
    )
    old_schema_conn.commit()
    before = dict(
        old_schema_conn.execute(
            "SELECT * FROM run_category_status WHERE category='ECONOMY'"
        ).fetchone()
    )

    migration_002.apply_migration(old_schema_conn)

    after_row = old_schema_conn.execute(
        "SELECT * FROM run_category_status WHERE category='ECONOMY'"
    ).fetchone()
    assert after_row is not None
    assert dict(after_row) == before

    count = old_schema_conn.execute("SELECT COUNT(*) FROM run_category_status").fetchone()[0]
    assert count == 1


# ---- D: MUSIC becomes a valid CHECK value post-migration -------------------


def test_D_music_becomes_valid_after_migration(old_schema_conn):
    run_row_id, _ = _seed_run_and_reports(old_schema_conn)

    # Pre-migration: MUSIC is rejected, proving the fixture really is old-schema.
    with pytest.raises(sqlite3.IntegrityError):
        old_schema_conn.execute(
            "INSERT INTO run_category_status (run_id, category, status) VALUES (?, 'MUSIC', 'NOT_READY')",
            (run_row_id,),
        )
    old_schema_conn.rollback()

    migration_002.apply_migration(old_schema_conn)

    old_schema_conn.execute(
        "INSERT INTO run_category_status (run_id, category, status) VALUES (?, 'MUSIC', 'NOT_READY')",
        (run_row_id,),
    )
    old_schema_conn.commit()
    row = old_schema_conn.execute(
        "SELECT category FROM run_category_status WHERE category='MUSIC'"
    ).fetchone()
    assert row["category"] == "MUSIC"

    # A genuinely-unknown category must still be rejected -- the CHECK is
    # extended, not removed.
    with pytest.raises(sqlite3.IntegrityError):
        old_schema_conn.execute(
            "INSERT INTO run_category_status (run_id, category, status) VALUES (?, 'NOT_A_CATEGORY', 'NOT_READY')",
            (run_row_id,),
        )


# ---- idempotency: safe to run twice -----------------------------------------


def test_migration_is_idempotent(old_schema_conn):
    _seed_run_and_reports(old_schema_conn)
    first = migration_002.apply_migration(old_schema_conn)
    second = migration_002.apply_migration(old_schema_conn)
    assert first is True
    assert second is False


def test_migration_no_op_against_already_current_schema(tmp_path):
    from db.database import connect, init_db

    db_path = tmp_path / "current.db"
    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        ran = migration_002.apply_migration(conn)
        assert ran is False
        conn.execute(
            "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('run-x', '2026-08-11', 'x', 'completed')"
        )
        run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO run_category_status (run_id, category, status) VALUES (?, 'MUSIC', 'NOT_READY')",
            (run_row_id,),
        )
        conn.commit()
    finally:
        conn.close()
