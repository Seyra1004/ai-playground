"""Persistence boundary between adapters and the DB.

Adapters never write SQL — they produce IngestionRecord objects; this
module is the only place that inserts into raw_items / run_source_status.
This is also where the adapter-output-model -> DB-column mapping lives
(e.g. IngestionRecord.extra dict -> raw_items.extra_json JSON string).

Duplicate handling happens per-item, not per-call: one record colliding
with an existing (source_name, source_item_key) does not affect any other
record's insert in the same save_raw_items() call — this is what makes
retry-idempotency and partial-success (PARTIAL status) possible.
"""

import json
import sqlite3


def save_raw_items(conn, records, category=None):
    """Insert each IngestionRecord into raw_items on `conn` (no commit —
    the caller controls the transaction boundary). Returns
    (inserted_count, duplicate_count).

    `category` is the ingestion-time SourceConfig.category for this
    source (a single save_raw_items() call is always scoped to one
    source's fetch, hence one category) — snapshotted onto every row so
    a later sources.yaml edit can never change an already-collected
    item's classification (Category Provenance Correction). Left NULL
    only when the caller doesn't have one (legacy/test call sites).

    `items_collected` in run_source_status is defined as `inserted_count`
    (newly inserted raw rows this call) — see Section 24 of the Phase 2A
    contract. `duplicate_count` is for logging/diagnostics only; it is
    never written to a DB column (no schema change for it)."""
    inserted = 0
    duplicates = 0
    for record in records:
        try:
            conn.execute(
                """INSERT INTO raw_items
                   (source_name, source_item_key, source_type, source_url, title,
                    snippet, published_at, collected_at, region, category, payload_hash, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.source_name,
                    record.source_item_key,
                    record.source_type,
                    record.source_url,
                    record.title,
                    record.snippet,
                    record.published_at,
                    record.collected_at,
                    record.region,
                    category,
                    record.payload_hash,
                    json.dumps(record.extra, ensure_ascii=False) if record.extra is not None else None,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            duplicates += 1
    return inserted, duplicates


def record_run_source_status(
    conn,
    run_id,
    category,
    source_name,
    status,
    started_at,
    finished_at=None,
    items_collected=0,
    retry_count=0,
    failure_reason=None,
):
    """Insert the run_source_status row for (run_id, category,
    source_name). Written exactly once per source attempt, after the
    outcome is final — the schema's CHECK on `status` only allows terminal
    values (SUCCESS/FAILED/PARTIAL/SKIPPED), so there is no intermediate
    'running' row to later update. A second call for the same
    (run_id, category, source_name) is rejected by the frozen
    UNIQUE(run_id, category, source_name) constraint, which is the
    intended immutability, not a bug to work around."""
    conn.execute(
        """INSERT INTO run_source_status
           (run_id, category, source_name, status, started_at, finished_at,
            items_collected, retry_count, failure_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            category,
            source_name,
            status,
            started_at,
            finished_at,
            items_collected,
            retry_count,
            failure_reason,
        ),
    )
