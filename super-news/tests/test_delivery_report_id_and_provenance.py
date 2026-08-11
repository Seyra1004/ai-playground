"""TEST O-T from the DB FOUNDATION IMPLEMENTATION CONTRACT v1 test matrix:
delivery_history.report_id backward compatibility / FK behavior, and the
CASCADE (junction) vs RESTRICT (referenced evidence) delete policies for
llm_interpretations provenance."""

import sqlite3

import pytest

import delivery
from db.database import connect, init_db


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    c.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
        ("r1", "2026-08-12", "2026-08-12T00:00:00+00:00", "running"),
    )
    c.commit()
    yield c
    c.close()


def _runs_row_id(conn):
    return conn.execute("SELECT id FROM runs WHERE run_id = 'r1'").fetchone()[0]


def _insert_report(conn, run_id, report_type="DAILY_AI"):
    conn.execute(
        "INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at) VALUES (?,?,?,?,?,?,?)",
        (run_id, "2026-08-12", report_type, "AI", "content", "hash1", "2026-08-12T06:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_interpretation(conn, run_id):
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, output_text, confidence, created_at)
           VALUES (?, 'AI', 'claude-opus-5', 'v1', 'text', 'MEDIUM', ?)""",
        (run_id, "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_normalized_item(conn, source_item_key):
    conn.execute(
        "INSERT INTO raw_items (source_name, source_item_key, source_type, source_url, collected_at) "
        "VALUES ('google_news_rss', ?, 'rss', 'https://example.com', '2026-08-12T00:00:00+00:00')",
        (source_item_key,),
    )
    raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO normalized_items (raw_item_id, category, event_key, normalized_title, created_at) "
        "VALUES (?, 'AI', ?, 'title', '2026-08-12T00:00:00+00:00')",
        (raw_id, source_item_key),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---- TEST O: existing record_delivery() call (no report_id) still works ----


def test_O_record_delivery_without_report_id_succeeds(conn):
    runs_row_id = _runs_row_id(conn)
    delivery.record_delivery(runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn)
    conn.commit()
    row = conn.execute("SELECT report_id FROM delivery_history WHERE status='sent'").fetchone()
    assert row["report_id"] is None


# ---- TEST P: new record_delivery(report_id=...) succeeds --------------------


def test_P_record_delivery_with_report_id_succeeds(conn):
    runs_row_id = _runs_row_id(conn)
    report_id = _insert_report(conn, runs_row_id)
    delivery.record_delivery(
        runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn, report_id=report_id
    )
    conn.commit()
    row = conn.execute("SELECT report_id FROM delivery_history WHERE status='sent'").fetchone()
    assert row["report_id"] == report_id


# ---- TEST Q: nonexistent report_id -> FK failure -----------------------------


def test_Q_nonexistent_report_id_fk_failure(conn):
    runs_row_id = _runs_row_id(conn)
    with pytest.raises(sqlite3.IntegrityError):
        delivery.record_delivery(
            runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn, report_id=99999
        )


# ---- TEST R: RESTRICT on referenced report delete ----------------------------


def test_R_report_delete_restricted_when_referenced(conn):
    runs_row_id = _runs_row_id(conn)
    report_id = _insert_report(conn, runs_row_id)
    delivery.record_delivery(
        runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn, report_id=report_id
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM reports WHERE id=?", (report_id,))


# ---- TEST S: CASCADE on interpretation delete for junction tables -----------


def test_S_interpretation_delete_cascades_junction(conn):
    runs_row_id = _runs_row_id(conn)
    interp_id = _insert_interpretation(conn, runs_row_id)
    norm_id = _insert_normalized_item(conn, "k1")
    conn.execute(
        "INSERT INTO interpretation_items (interpretation_id, normalized_item_id) VALUES (?, ?)",
        (interp_id, norm_id),
    )
    conn.commit()

    conn.execute("DELETE FROM llm_interpretations WHERE id=?", (interp_id,))
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM interpretation_items WHERE interpretation_id=?", (interp_id,)
    ).fetchone()[0]
    assert remaining == 0


# ---- TEST T: RESTRICT on referenced evidence delete --------------------------


def test_T_evidence_delete_restricted_when_referenced_in_junction(conn):
    runs_row_id = _runs_row_id(conn)
    interp_id = _insert_interpretation(conn, runs_row_id)
    norm_id = _insert_normalized_item(conn, "k2")
    conn.execute(
        "INSERT INTO interpretation_items (interpretation_id, normalized_item_id) VALUES (?, ?)",
        (interp_id, norm_id),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM normalized_items WHERE id=?", (norm_id,))
