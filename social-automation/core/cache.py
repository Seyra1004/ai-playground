from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Optional


def compute_hash(data: Any) -> str:
    serialized = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def get_cached(conn: sqlite3.Connection, cache_key: str) -> Optional[Any]:
    row = conn.execute("SELECT value FROM cache WHERE cache_key = ?", (cache_key,)).fetchone()
    if row is None:
        return None
    return json.loads(row["value"])


def set_cached(conn: sqlite3.Connection, cache_key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO cache (cache_key, value, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET value = excluded.value, created_at = excluded.created_at",
        (cache_key, json.dumps(value, ensure_ascii=False, default=str), now),
    )
    conn.commit()
