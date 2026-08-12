"""One-time migration: add 'MUSIC' to run_category_status.category's CHECK
constraint. SQLite has no ALTER TABLE ... ALTER CONSTRAINT, so this uses the
standard safe rebuild pattern: create a new table with the updated CHECK,
copy every row across unchanged, drop the old table, rename the new one into
place, then recreate the unique index. Runs inside one transaction — either
the whole rebuild lands or none of it does.

Idempotent: if the live schema's CHECK clause already contains 'MUSIC' (this
migration already ran), apply_migration() is a no-op. Safe to invoke more
than once, including accidentally in production.
"""

import sqlite3

_NEW_TABLE_SQL = """
CREATE TABLE run_category_status_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  category TEXT NOT NULL CHECK(category IN
    ('TIKTOK','SPOTIFY','AI','ECONOMY','SOCIETY','MONTHLY_FORECAST','MUSIC')),
  status TEXT NOT NULL CHECK(status IN ('REPORT_GENERATED','REPORT_FAILED','NOT_READY')),
  failure_stage TEXT CHECK(failure_stage IN ('SOURCE','NORMALIZATION','SIGNAL','LLM','REPORT') OR failure_stage IS NULL),
  report_id INTEGER REFERENCES reports(id) ON DELETE RESTRICT,
  items_collected INTEGER,
  items_rejected INTEGER,
  items_selected INTEGER,
  failure_reason TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0
)
"""

_COLUMNS = (
    "id, run_id, category, status, failure_stage, report_id, "
    "items_collected, items_rejected, items_selected, failure_reason, retry_count"
)


def _already_migrated(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='run_category_status'"
    ).fetchone()
    if row is None:
        # Table doesn't exist yet at all -- nothing for this migration to do;
        # a fresh init_db() run will create it with the up-to-date schema.sql.
        return True
    return "MUSIC" in row[0]


def apply_migration(conn):
    """Returns True if the migration actually ran, False if it was already
    applied (no-op). Raises on any failure -- callers must not swallow this;
    a failed migration must not be reported as a successful production run."""
    if _already_migrated(conn):
        return False

    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(_NEW_TABLE_SQL)
        conn.execute(
            f"INSERT INTO run_category_status_new ({_COLUMNS}) "
            f"SELECT {_COLUMNS} FROM run_category_status"
        )
        conn.execute("DROP TABLE run_category_status")
        conn.execute("ALTER TABLE run_category_status_new RENAME TO run_category_status")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_run_category_status "
            "ON run_category_status(run_id, category)"
        )
        integrity_errors = conn.execute("PRAGMA foreign_key_check(run_category_status)").fetchall()
        if integrity_errors:
            raise sqlite3.IntegrityError(
                f"post-migration foreign_key_check found violations: {integrity_errors}"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_was_on else 'OFF'}")

    return True
