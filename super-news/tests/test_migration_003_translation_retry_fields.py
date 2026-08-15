"""translation_cache retry-fields migration: safe SQLite table-rebuild.

Uses a SYNTHETIC old-schema fixture (schema.sql's translation_cache with
failure_kind/attempt_count/retry_after/last_attempt_at stripped back out,
executed fresh) rather than relying on the real table's current shape --
proves the migration works against a populated, pre-migration
production-shaped table, not just an empty one.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "db" / "migrations" / "003_add_translation_retry_fields.py"
)
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

_OLD_TRANSLATION_CACHE_SQL = """CREATE TABLE IF NOT EXISTS translation_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cache_key TEXT NOT NULL,
  source_lang TEXT,
  target_lang TEXT NOT NULL,
  original_text TEXT NOT NULL,
  translated_text TEXT,
  status TEXT NOT NULL CHECK(status IN ('TRANSLATED','TRANSLATION_UNAVAILABLE','FAILED')),
  provider TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);"""


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_003", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration_003 = _load_migration_module()


def _old_schema_sql():
    """The real schema.sql with translation_cache's CREATE TABLE swapped
    back to its pre-Phase-3A.1 shape (no retry-bookkeeping columns), byte-
    for-byte identical otherwise."""
    current = _SCHEMA_PATH.read_text(encoding="utf-8")
    import re

    old = re.sub(
        r"CREATE TABLE IF NOT EXISTS translation_cache \([^;]*\);",
        _OLD_TRANSLATION_CACHE_SQL,
        current,
        count=1,
        flags=re.DOTALL,
    )
    assert old != current, "fixture setup failed to locate translation_cache's CREATE TABLE in schema.sql"
    assert "failure_kind" not in _extract_translation_cache_sql(old)
    return old


def _extract_translation_cache_sql(schema_sql):
    import re

    match = re.search(r"CREATE TABLE IF NOT EXISTS translation_cache \([^;]*\);", schema_sql, re.DOTALL)
    return match.group(0)


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


def _seed_row(conn, cache_key="key-1", status="TRANSLATED", translated_text="번역됨"):
    conn.execute(
        """INSERT INTO translation_cache
           (cache_key, source_lang, target_lang, original_text, translated_text, status, provider, created_at, updated_at)
           VALUES (?, NULL, 'ko', 'Original Text', ?, ?, 'FakeProvider', '2026-08-14T00:00:00+00:00', '2026-08-14T00:00:00+00:00')""",
        (cache_key, translated_text, status),
    )
    conn.commit()


def test_migration_applies_against_populated_old_schema(old_schema_conn):
    _seed_row(old_schema_conn)
    ran = migration_003.apply_migration(old_schema_conn)
    assert ran is True


def test_existing_rows_survive_migration_with_safe_defaults(old_schema_conn):
    _seed_row(old_schema_conn, cache_key="key-translated", status="TRANSLATED", translated_text="번역됨")
    _seed_row(old_schema_conn, cache_key="key-unavailable", status="TRANSLATION_UNAVAILABLE", translated_text=None)
    _seed_row(old_schema_conn, cache_key="key-failed", status="FAILED", translated_text=None)

    migration_003.apply_migration(old_schema_conn)

    rows = {
        row["cache_key"]: row
        for row in old_schema_conn.execute("SELECT * FROM translation_cache").fetchall()
    }
    assert set(rows) == {"key-translated", "key-unavailable", "key-failed"}
    assert rows["key-translated"]["status"] == "TRANSLATED"
    assert rows["key-translated"]["translated_text"] == "번역됨"
    for row in rows.values():
        assert row["failure_kind"] is None
        assert row["attempt_count"] == 0
        assert row["retry_after"] is None
        assert row["last_attempt_at"] is None

    count = old_schema_conn.execute("SELECT COUNT(*) FROM translation_cache").fetchone()[0]
    assert count == 3


def test_failure_kind_becomes_valid_after_migration(old_schema_conn):
    _seed_row(old_schema_conn)

    # Pre-migration: the failure_kind column doesn't exist at all.
    with pytest.raises(sqlite3.OperationalError):
        old_schema_conn.execute("SELECT failure_kind FROM translation_cache")

    migration_003.apply_migration(old_schema_conn)

    old_schema_conn.execute(
        """INSERT INTO translation_cache
           (cache_key, source_lang, target_lang, original_text, translated_text, status,
            failure_kind, attempt_count, retry_after, last_attempt_at, provider, created_at, updated_at)
           VALUES ('key-2', NULL, 'ko', 'Other Text', NULL, 'FAILED',
                   'TRANSIENT', 1, '2026-08-14T01:00:00+00:00', '2026-08-14T00:00:00+00:00',
                   'FakeProvider', '2026-08-14T00:00:00+00:00', '2026-08-14T00:00:00+00:00')"""
    )
    old_schema_conn.commit()
    row = old_schema_conn.execute(
        "SELECT failure_kind FROM translation_cache WHERE cache_key='key-2'"
    ).fetchone()
    assert row["failure_kind"] == "TRANSIENT"

    # A genuinely-unknown failure_kind must still be rejected -- the CHECK
    # is real, not silently dropped by the rebuild.
    with pytest.raises(sqlite3.IntegrityError):
        old_schema_conn.execute(
            """INSERT INTO translation_cache
               (cache_key, source_lang, target_lang, original_text, translated_text, status,
                failure_kind, attempt_count, provider, created_at, updated_at)
               VALUES ('key-3', NULL, 'ko', 'Bad Text', NULL, 'FAILED',
                       'NOT_A_KIND', 1, 'FakeProvider', '2026-08-14T00:00:00+00:00', '2026-08-14T00:00:00+00:00')"""
        )


def test_migration_is_idempotent(old_schema_conn):
    _seed_row(old_schema_conn)
    first = migration_003.apply_migration(old_schema_conn)
    second = migration_003.apply_migration(old_schema_conn)
    assert first is True
    assert second is False


def test_migration_no_op_against_already_current_schema(tmp_path):
    from db.database import connect, init_db

    db_path = tmp_path / "current.db"
    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        ran = migration_003.apply_migration(conn)
        assert ran is False
        conn.execute(
            """INSERT INTO translation_cache
               (cache_key, source_lang, target_lang, original_text, translated_text, status,
                failure_kind, attempt_count, provider, created_at, updated_at)
               VALUES ('key-x', NULL, 'ko', 'Text', NULL, 'FAILED',
                       'TRANSIENT', 1, 'FakeProvider', '2026-08-14T00:00:00+00:00', '2026-08-14T00:00:00+00:00')"""
        )
        conn.commit()
    finally:
        conn.close()
