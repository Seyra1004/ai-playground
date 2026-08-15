"""One-time migration: add failure_kind/attempt_count/retry_after/
last_attempt_at to translation_cache (Phase 3A.1, translation failure-cache
retry safety). SQLite has no ALTER TABLE ... ADD COLUMN ... CHECK in one
step for the CHECK-constrained failure_kind column, so this uses the same
safe rebuild pattern as 002_add_music_to_run_category_status.py: create a
new table with the additive columns, copy every existing row across
(defaulting the new columns to their safe "never attempted a retry yet"
values), drop the old table, rename the new one into place, recreate the
unique index. Runs inside one transaction -- either the whole rebuild lands
or none of it does.

Existing TRANSLATED/TRANSLATION_UNAVAILABLE/FAILED rows are preserved
byte-for-byte on every original column; only the four new columns are
populated (attempt_count=0, the rest NULL) since no pre-migration row ever
had retry bookkeeping.

Idempotent: if the live schema already has the new columns (this migration
already ran), apply_migration() is a no-op. Safe to invoke more than once,
including accidentally in production.
"""

import sqlite3

_NEW_TABLE_SQL = """
CREATE TABLE translation_cache_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cache_key TEXT NOT NULL,
  source_lang TEXT,
  target_lang TEXT NOT NULL,
  original_text TEXT NOT NULL,
  translated_text TEXT,
  status TEXT NOT NULL CHECK(status IN ('TRANSLATED','TRANSLATION_UNAVAILABLE','FAILED')),
  failure_kind TEXT CHECK(failure_kind IN ('TRANSIENT','PERMANENT') OR failure_kind IS NULL),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  retry_after TEXT,
  last_attempt_at TEXT,
  provider TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

_OLD_COLUMNS = (
    "id, cache_key, source_lang, target_lang, original_text, translated_text, "
    "status, provider, created_at, updated_at"
)
_NEW_COLUMNS = (
    "id, cache_key, source_lang, target_lang, original_text, translated_text, "
    "status, failure_kind, attempt_count, retry_after, last_attempt_at, provider, created_at, updated_at"
)


def _already_migrated(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='translation_cache'"
    ).fetchone()
    if row is None:
        # Table doesn't exist yet at all -- nothing for this migration to do;
        # a fresh init_db() run will create it with the up-to-date schema.sql.
        return True
    return "failure_kind" in row[0]


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
            f"INSERT INTO translation_cache_new "
            f"(id, cache_key, source_lang, target_lang, original_text, translated_text, "
            f"status, failure_kind, attempt_count, retry_after, last_attempt_at, provider, created_at, updated_at) "
            f"SELECT id, cache_key, source_lang, target_lang, original_text, translated_text, "
            f"status, NULL, 0, NULL, NULL, provider, created_at, updated_at "
            f"FROM translation_cache"
        )
        conn.execute("DROP TABLE translation_cache")
        conn.execute("ALTER TABLE translation_cache_new RENAME TO translation_cache")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_translation_cache_key "
            "ON translation_cache(cache_key, target_lang)"
        )
        integrity_errors = conn.execute("PRAGMA foreign_key_check(translation_cache)").fetchall()
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
