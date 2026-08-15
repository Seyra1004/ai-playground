"""report.persistence: atomic single-transaction write, including rollback
on a mid-transaction failure (DB transaction rollback)."""

import sqlite3

import pytest

from db.database import connect, init_db
from report.persistence import PRODUCER_INTELLIGENCE_CATEGORY, persist_producer_intelligence, persist_report_run


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id="run-1"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, '2026-08-12', 'x', 'completed')",
        (run_id,),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_normalized_item(conn, key):
    conn.execute(
        """INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, title, collected_at)
           VALUES ('s', ?, 'rss', 'https://x', 'AI news', '2026-08-12T00:00:00+00:00')""",
        (key,),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at)
           VALUES (?, 'AI', ?, 'AI news', '2026-08-12T00:00:00+00:00')""",
        (raw_id, f"ev-{key}"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _candidates(item_id):
    return {
        "AI": [{"id": item_id, "entity_name": "OpenAI", "normalized_title": "AI news",
                "source_count": 1, "item_ids": [item_id]}],
        "ECONOMY": [],
        "SOCIETY": [],
    }


def _news_result(item_id):
    return {
        "model_used": "fake-model", "prompt_version": "v1", "input_hash": "hash1",
        "input_tokens": 10, "output_tokens": 5, "estimated_cost": None,
        "output_text": f'{{"AI": [{{"id": {item_id}, "reason": "x"}}], "ECONOMY": [], "SOCIETY": []}}',
    }


def _valid_selections(item_id):
    return {"AI": [{"id": item_id, "reason": "x"}], "ECONOMY": [], "SOCIETY": []}


EMPTY_MUSIC_DIFF = {"observed_at": None, "entries": []}


def test_generates_reports_and_run_category_status(conn):
    runs_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "k1")
    outcome = persist_report_run(
        conn, runs_row_id, "2026-08-12", _news_result(item_id), _valid_selections(item_id), {},
        _candidates(item_id), EMPTY_MUSIC_DIFF, "no music",
    )
    assert outcome["AI"]["status"] == "REPORT_GENERATED"
    assert outcome["ECONOMY"]["status"] == "REPORT_GENERATED"  # empty selections, still a valid empty report
    assert outcome["MUSIC"]["status"] == "NOT_READY"

    reports = conn.execute("SELECT category FROM reports WHERE run_id=?", (runs_row_id,)).fetchall()
    assert {r["category"] for r in reports} == {"AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY"}

    status_rows = conn.execute(
        "SELECT category, status FROM run_category_status WHERE run_id=?", (runs_row_id,)
    ).fetchall()
    assert len(status_rows) == 6


def test_interpretation_items_provenance_written(conn):
    runs_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "k1")
    persist_report_run(
        conn, runs_row_id, "2026-08-12", _news_result(item_id), _valid_selections(item_id), {},
        _candidates(item_id), EMPTY_MUSIC_DIFF, "no music",
    )
    interp_id = conn.execute("SELECT id FROM llm_interpretations WHERE run_id=?", (runs_row_id,)).fetchone()["id"]
    items = conn.execute(
        "SELECT normalized_item_id FROM interpretation_items WHERE interpretation_id=?", (interp_id,)
    ).fetchall()
    assert [row["normalized_item_id"] for row in items] == [item_id]


def test_validation_error_category_becomes_report_failed(conn):
    from report.validation import CategoryValidationError

    runs_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "k1")
    outcome = persist_report_run(
        conn, runs_row_id, "2026-08-12", _news_result(item_id), {"ECONOMY": [], "SOCIETY": []},
        {"AI": CategoryValidationError("AI", "hallucinated id 999")},
        _candidates(item_id), EMPTY_MUSIC_DIFF, "no music",
    )
    assert outcome["AI"]["status"] == "REPORT_FAILED"
    row = conn.execute(
        "SELECT failure_stage, failure_reason FROM run_category_status WHERE run_id=? AND category='AI'",
        (runs_row_id,),
    ).fetchone()
    assert row["failure_stage"] == "LLM"
    assert "999" in row["failure_reason"]
    # No reports row for the failed category.
    assert conn.execute(
        "SELECT COUNT(*) FROM reports WHERE run_id=? AND category='AI'", (runs_row_id,)
    ).fetchone()[0] == 0


def test_none_news_result_yields_not_ready_for_all_news_categories(conn):
    runs_row_id = _insert_run(conn)
    outcome = persist_report_run(
        conn, runs_row_id, "2026-08-12", None, {}, {},
        {"AI": [], "ECONOMY": [], "SOCIETY": []}, EMPTY_MUSIC_DIFF, "no music",
    )
    assert outcome["AI"]["status"] == "NOT_READY"
    assert outcome["ECONOMY"]["status"] == "NOT_READY"
    assert outcome["SOCIETY"]["status"] == "NOT_READY"
    assert conn.execute("SELECT COUNT(*) FROM llm_interpretations WHERE run_id=?", (runs_row_id,)).fetchone()[0] == 0


# ---- DB transaction rollback -------------------------------------------------


def test_mid_transaction_failure_rolls_back_everything(conn):
    runs_row_id = _insert_run(conn)
    item_id = _insert_normalized_item(conn, "k1")

    # Pre-occupy the MUSIC report_type slot for this run so the MUSIC insert
    # inside persist_report_run collides with the UNIQUE(run_id, report_type)
    # index -- forcing a failure AFTER the AI report has already been
    # inserted (but not yet committed) in the same transaction.
    conn.execute(
        """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
           VALUES (?, '2026-08-12', 'MUSIC', 'MUSIC', 'preexisting', 'hash-x', 'x')""",
        (runs_row_id,),
    )
    conn.commit()

    music_diff_with_content = {
        "observed_at": "2026-08-12T00:00:00+00:00",
        "entries": [{"music_entity_id": 1, "rank": 1, "canonical_artist": "A", "canonical_title": "B",
                     "is_new": True, "rank_delta": None}],
    }

    with pytest.raises(sqlite3.IntegrityError):
        persist_report_run(
            conn, runs_row_id, "2026-08-12", _news_result(item_id), _valid_selections(item_id), {},
            _candidates(item_id), music_diff_with_content, "music content",
        )

    # The AI report insert that happened earlier in the same transaction
    # must have been rolled back too -- only the pre-existing MUSIC row
    # survives, nothing new was added.
    reports = conn.execute("SELECT category FROM reports WHERE run_id=?", (runs_row_id,)).fetchall()
    assert [r["category"] for r in reports] == ["MUSIC"]
    assert conn.execute(
        "SELECT COUNT(*) FROM run_category_status WHERE run_id=?", (runs_row_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM llm_interpretations WHERE run_id=?", (runs_row_id,)
    ).fetchone()[0] == 0


# ---- persist_producer_intelligence: independent of persist_report_run ------


def _synthesis_result(input_hash="hash1", output_text='{"insights": []}'):
    return {
        "input_hash": input_hash, "prompt_version": "v1", "model_used": "fake-model",
        "output_text": output_text, "input_tokens": 10, "output_tokens": 5, "estimated_cost": None,
    }


def test_persist_producer_intelligence_writes_one_row(conn):
    runs_row_id = _insert_run(conn, "run-pi")
    result_id = persist_producer_intelligence(conn, runs_row_id, _synthesis_result())
    conn.commit()

    row = conn.execute("SELECT * FROM llm_interpretations WHERE id = ?", (result_id,)).fetchone()
    assert row["run_id"] == runs_row_id
    assert row["category"] == PRODUCER_INTELLIGENCE_CATEGORY
    assert row["output_text"] == '{"insights": []}'
    assert row["input_hash"] == "hash1"


def test_persist_producer_intelligence_never_touches_reports_table(conn):
    runs_row_id = _insert_run(conn, "run-pi2")
    persist_producer_intelligence(conn, runs_row_id, _synthesis_result())
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM reports WHERE run_id=?", (runs_row_id,)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM run_category_status WHERE run_id=?", (runs_row_id,)
    ).fetchone()[0] == 0


def test_persist_producer_intelligence_independent_of_news_report_failure(conn):
    """A prior news-report rollback (see the IntegrityError test above)
    must not affect Producer Intelligence persistence in a later,
    independent transaction -- proven here by writing it against a run
    that never had any persist_report_run call at all."""
    runs_row_id = _insert_run(conn, "run-pi3")
    persist_producer_intelligence(conn, runs_row_id, _synthesis_result(input_hash="hash2"))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM llm_interpretations WHERE run_id = ? AND category = ?",
        (runs_row_id, PRODUCER_INTELLIGENCE_CATEGORY),
    ).fetchone()
    assert row is not None
