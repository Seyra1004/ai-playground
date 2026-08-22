from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date as _date

_NON_WORD_RE = re.compile(r"[^\w가-힣]")
_WHITESPACE_RE = re.compile(r"\s+")


def compute_topic_fingerprint(topic: str) -> str:
    """Deterministic normalized fingerprint used both to dedupe same-day
    candidates and to detect a topic that ran too recently."""
    normalized = _WHITESPACE_RE.sub("", topic.strip().lower())
    normalized = _NON_WORD_RE.sub("", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def dedupe_candidates(candidates: list) -> list:
    seen = set()
    deduped = []
    for c in candidates:
        fp = compute_topic_fingerprint(c.topic)
        if fp in seen:
            continue
        seen.add(fp)
        deduped.append(c)
    return deduped


def make_run_id(account_id: str, run_date: str) -> str:
    return f"{account_id}:{run_date}"


def get_run(conn: sqlite3.Connection, account_id: str, run_date: str):
    return conn.execute(
        "SELECT * FROM runs WHERE run_id = ?", (make_run_id(account_id, run_date),)
    ).fetchone()


def upsert_run(
    conn: sqlite3.Connection,
    account_id: str,
    run_date: str,
    status: str,
    content_id: str = None,
    topic_fingerprint: str = None,
    retry_count: int = 0,
    started_at: str = None,
    finished_at: str = None,
) -> None:
    run_id = make_run_id(account_id, run_date)
    existing = get_run(conn, account_id, run_date)
    if existing is None:
        conn.execute(
            "INSERT INTO runs (run_id, account_id, run_date, content_id, topic_fingerprint, status, "
            "retry_count, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, account_id, run_date, content_id, topic_fingerprint, status, retry_count, started_at, finished_at),
        )
    else:
        conn.execute(
            "UPDATE runs SET "
            "content_id=COALESCE(?, content_id), "
            "topic_fingerprint=COALESCE(?, topic_fingerprint), "
            "status=?, retry_count=?, "
            "started_at=COALESCE(?, started_at), "
            "finished_at=COALESCE(?, finished_at) "
            "WHERE run_id=?",
            (content_id, topic_fingerprint, status, retry_count, started_at, finished_at, run_id),
        )
    conn.commit()


def recent_topic_fingerprints(conn: sqlite3.Connection, account_id: str, before_date: str, window_days: int) -> set:
    """Fingerprints of topics this account already published/reviewed within
    `window_days` before `before_date`, used to penalize picking the same
    topic again too soon."""
    rows = conn.execute(
        "SELECT run_date, topic_fingerprint FROM runs WHERE account_id=? AND topic_fingerprint IS NOT NULL "
        "AND status IN ('COMPLETE', 'NEEDS_REVIEW') AND run_date < ?",
        (account_id, before_date),
    ).fetchall()

    cutoff = _date.fromisoformat(before_date).toordinal() - window_days
    result = set()
    for r in rows:
        try:
            d = _date.fromisoformat(r["run_date"]).toordinal()
        except ValueError:
            continue
        if d >= cutoff:
            result.add(r["topic_fingerprint"])
    return result


def apply_recency_penalty(candidates: list, recent_fingerprints: set) -> None:
    """Mutates candidates in place: any candidate whose topic fingerprint
    matches recent history gets its duplication_penalty_signal pushed above
    core.scoring's DUPLICATION_REJECT_THRESHOLD, so the existing scoring gate
    (not a second copy of that logic) rejects it."""
    for c in candidates:
        fp = compute_topic_fingerprint(c.topic)
        if fp in recent_fingerprints:
            c.duplication_penalty_signal = max(c.duplication_penalty_signal, 0.9)
