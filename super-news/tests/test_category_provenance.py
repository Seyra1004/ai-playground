"""Category Provenance Correction: raw_items.category is an ingestion-time
snapshot that later registry changes must never override. Tests the
required behaviors directly (not implementation trivia)."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.normalize import normalize_raw_item
from ingestion.persistence import save_raw_items
from ingestion.pipeline import run_source_ingestion
from ingestion.records import AdapterOutcome, IngestionRecord
from ingestion.registry import RetryPolicy, SourceConfig


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _cfg(name, category):
    return SourceConfig(
        source_name=name, enabled=True, source_type="rss", category=category,
        region="GLOBAL", endpoint="https://example.com", timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )


def _record(source_name, key):
    return IngestionRecord(
        source_name=source_name, source_item_key=key, source_type="rss",
        source_url=f"https://example.com/{key}", collected_at="2026-08-12T00:00:00+00:00",
        title="Some Title",
    )


def _insert_raw_item(conn, source_name, key, category, title="T", snippet=None):
    conn.execute(
        """INSERT INTO raw_items
           (source_name, source_item_key, source_type, source_url, title, snippet, collected_at, category)
           VALUES (?, ?, 'rss', ?, ?, ?, ?, ?)""",
        (source_name, key, f"https://example.com/{key}", title, snippet, "2026-08-12T00:00:00+00:00", category),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _fetch_raw_row(conn, raw_item_id):
    return conn.execute("SELECT * FROM raw_items WHERE id=?", (raw_item_id,)).fetchone()


def _insert_run(conn, run_id="r1"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
        (run_id, "2026-08-12", "2026-08-12T00:00:00+00:00", "running"),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]


# ---- A: new ingestion stores raw_items.category == SourceConfig.category ---


def test_new_ingestion_snapshots_source_config_category(conn):
    run_id = _insert_run(conn)
    source_config = _cfg("ai_rss", "AI_NEWS")
    outcome = AdapterOutcome(records=[_record("ai_rss", "k1")])
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: outcome):
        run_source_ingestion(conn, run_id, source_config)
    row = conn.execute("SELECT category FROM raw_items WHERE source_item_key='k1'").fetchone()
    assert row["category"] == "AI_NEWS"


# ---- B: registry change after ingestion cannot mutate historical category --


def test_registry_change_does_not_mutate_already_stored_category(conn):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", category="AI_NEWS", title="T")
    changed_registry = {"ai_rss": _cfg("ai_rss", "ECONOMY_NEWS")}  # registry now disagrees
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), changed_registry)
    assert outcome.status == "normalized"
    row = conn.execute("SELECT category FROM normalized_items WHERE id=?", (outcome.normalized_item_id,)).fetchone()
    assert row["category"] == "AI_NEWS"  # ingestion-time value wins, registry is ignored


# ---- C: NULL raw category + source present in registry -> fallback works ---


def test_null_category_with_source_in_registry_uses_registry_fallback(conn):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", category=None, title="T")
    registry = {"ai_rss": _cfg("ai_rss", "AI_NEWS")}
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), registry)
    assert outcome.status == "normalized"
    row = conn.execute("SELECT category FROM normalized_items WHERE id=?", (outcome.normalized_item_id,)).fetchone()
    assert row["category"] == "AI_NEWS"


# ---- D: NULL raw category + source missing from registry -> REJECTED ------


def test_null_category_with_source_missing_from_registry_is_rejected(conn):
    raw_id = _insert_raw_item(conn, "removed_source", "k1", category=None, title="T")
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), {})
    assert outcome.status == "rejected"
    count = conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]
    assert count == 0


# ---- E: existing duplicate-ingestion/idempotency behavior is unchanged -----


def test_duplicate_ingestion_idempotency_unchanged(conn):
    run_id = _insert_run(conn)
    source_config = _cfg("ai_rss", "AI_NEWS")
    outcome = AdapterOutcome(records=[_record("ai_rss", "k1"), _record("ai_rss", "k1")])
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: outcome):
        result = run_source_ingestion(conn, run_id, source_config)
    count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE source_item_key='k1'").fetchone()[0]
    assert count == 1
    assert result["items_collected"] == 1


# ---- F: existing normalization behavior for valid current rows is intact --


def test_normalization_of_fresh_row_with_category_present_is_unaffected(conn):
    run_id = _insert_run(conn)
    source_config = _cfg("ai_rss", "AI_NEWS")
    outcome = AdapterOutcome(records=[_record("ai_rss", "k1")])
    with patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: outcome):
        run_source_ingestion(conn, run_id, source_config)
    raw_row = conn.execute("SELECT * FROM raw_items WHERE source_item_key='k1'").fetchone()
    result = normalize_raw_item(conn, raw_row, {"ai_rss": source_config})
    assert result.status == "normalized"
    row = conn.execute("SELECT category, normalized_title FROM normalized_items WHERE id=?", (result.normalized_item_id,)).fetchone()
    assert row["category"] == "AI_NEWS"
    assert row["normalized_title"] == "Some Title"


# ---- G: fresh scratch DB initializes successfully with the new column -----


def test_fresh_scratch_db_initializes_with_category_column(tmp_path):
    db_path = tmp_path / "fresh.db"
    init_db(db_path=db_path)
    raw = connect(db_path=db_path)
    try:
        cols = {row[1] for row in raw.execute("PRAGMA table_info(raw_items)")}
        assert "category" in cols
        # save_raw_items without an explicit category still succeeds (NULL).
        inserted, duplicates = save_raw_items(raw, [_record("ai_rss", "k1")])
        raw.commit()
        assert inserted == 1
        row = raw.execute("SELECT category FROM raw_items WHERE source_item_key='k1'").fetchone()
        assert row["category"] is None
    finally:
        raw.close()
