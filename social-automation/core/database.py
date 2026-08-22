from __future__ import annotations

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    candidate_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    category TEXT NOT NULL,
    score REAL,
    status TEXT,
    urgent INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT,
    retrieved_at TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    text TEXT NOT NULL,
    source_ids TEXT,
    verified_at TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS contents (
    content_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    page_count INTEGER,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    content_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    role TEXT NOT NULL,
    headline TEXT,
    body TEXT,
    visual_ref TEXT,
    PRIMARY KEY (content_id, page_number)
);

CREATE TABLE IF NOT EXISTS pipeline_stage_state (
    account_id TEXT NOT NULL,
    content_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (account_id, content_id, stage)
);

CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS publications (
    content_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    published_at TEXT,
    external_id TEXT,
    PRIMARY KEY (content_id, platform)
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
