from __future__ import annotations

import sqlite3
from typing import Any, Callable

from core.cache import compute_hash, get_cached, set_cached

STAGE_ORDER = [
    "topic_scoring",
    "fact_validation",
    "page_selection",
    "canonical_content",
    "instagram_adapter",
    "threads_adapter",
    "renderer_input",
    "qa",
]


def get_stage_state(conn: sqlite3.Connection, account_id: str, content_id: str, stage: str):
    return conn.execute(
        "SELECT * FROM pipeline_stage_state WHERE account_id=? AND content_id=? AND stage=?",
        (account_id, content_id, stage),
    ).fetchone()


def run_stage(
    conn: sqlite3.Connection,
    account_id: str,
    content_id: str,
    stage: str,
    input_data: Any,
    fn: Callable[[], Any],
    now: str,
):
    """Run one pipeline stage with hash-based restart/cache support.

    Returns (result, cache_hit). If a prior SUCCESS run exists for this
    (account, content, stage) with an identical input_hash, its cached JSON
    output is returned without calling fn(). Otherwise fn() runs, and on
    success both the stage state and the output are persisted so a later
    identical call can reuse them; on failure the stage is marked FAILED and
    the exception propagates so callers can stop the pipeline at that stage.
    """
    input_hash = compute_hash(input_data)
    cache_key = f"{account_id}:{content_id}:{stage}:{input_hash}"
    existing = get_stage_state(conn, account_id, content_id, stage)

    if existing is not None and existing["status"] == "SUCCESS" and existing["input_hash"] == input_hash:
        cached_output = get_cached(conn, cache_key)
        if cached_output is not None:
            return cached_output, True

    retry_count = (existing["retry_count"] + 1) if existing is not None else 0

    conn.execute(
        "INSERT INTO pipeline_stage_state "
        "(account_id, content_id, stage, status, input_hash, output_hash, error, retry_count, started_at, finished_at) "
        "VALUES (?, ?, ?, 'RUNNING', ?, NULL, NULL, ?, ?, NULL) "
        "ON CONFLICT(account_id, content_id, stage) DO UPDATE SET "
        "status='RUNNING', input_hash=excluded.input_hash, error=NULL, "
        "retry_count=excluded.retry_count, started_at=excluded.started_at, finished_at=NULL",
        (account_id, content_id, stage, input_hash, retry_count, now),
    )
    conn.commit()

    try:
        result = fn()
    except Exception as exc:
        conn.execute(
            "UPDATE pipeline_stage_state SET status='FAILED', error=?, finished_at=? "
            "WHERE account_id=? AND content_id=? AND stage=?",
            (str(exc), now, account_id, content_id, stage),
        )
        conn.commit()
        raise

    output_hash = compute_hash(result)
    set_cached(conn, cache_key, result, now)
    conn.execute(
        "UPDATE pipeline_stage_state SET status='SUCCESS', output_hash=?, finished_at=? "
        "WHERE account_id=? AND content_id=? AND stage=?",
        (output_hash, now, account_id, content_id, stage),
    )
    conn.commit()
    return result, False
