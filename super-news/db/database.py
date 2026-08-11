"""SQLite connection + schema initialization for the minimal Phase 1A state:
run tracking and delivery idempotency. No News/Music tables here."""

import sqlite3
from pathlib import Path

from config import DB_PATH, ensure_runtime_dirs

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path=None):
    db_path = Path(db_path) if db_path is not None else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    ensure_runtime_dirs()
    conn = connect(db_path)
    try:
        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
