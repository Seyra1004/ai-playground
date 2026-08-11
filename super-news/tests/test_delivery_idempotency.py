import sqlite3

import pytest

import delivery
from db.database import connect, init_db


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, ?, ?)",
        ("r1", "2026-08-12", "2026-08-12T00:00:00+00:00", "running"),
    )
    conn.commit()
    yield conn, db_path
    conn.close()


def _runs_row_id(conn):
    return conn.execute("SELECT id FROM runs WHERE run_id = 'r1'").fetchone()[0]


def test_no_prior_delivery_means_send(db):
    conn, _ = db
    key = delivery.build_idempotency_key("2026-08-12", "DAILY", "kakao_memo")
    assert delivery.decide_delivery_action(key, conn=conn) == "send"


def test_sent_blocks_further_sends(db):
    conn, _ = db
    runs_row_id = _runs_row_id(conn)
    key = delivery.build_idempotency_key("2026-08-12", "DAILY", "kakao_memo")

    delivery.record_delivery(
        runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn
    )
    conn.commit()

    assert delivery.decide_delivery_action(key, conn=conn) == "skip_duplicate"


def test_failed_allows_retry(db):
    conn, _ = db
    runs_row_id = _runs_row_id(conn)
    key = delivery.build_idempotency_key("2026-08-12", "DAILY", "kakao_memo")

    delivery.record_delivery(
        runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "failed", conn=conn
    )
    conn.commit()

    assert delivery.decide_delivery_action(key, conn=conn) == "send"


def test_skipped_duplicate_not_conflated_with_sent(db):
    conn, _ = db
    runs_row_id = _runs_row_id(conn)
    key = delivery.build_idempotency_key("2026-08-12", "DAILY", "kakao_memo")

    delivery.record_delivery(
        runs_row_id,
        "2026-08-12",
        "DAILY",
        "kakao_memo",
        "hash1",
        "skipped_duplicate",
        conn=conn,
    )
    conn.commit()

    # A skipped_duplicate row must never itself count as a real send.
    assert delivery.decide_delivery_action(key, conn=conn) == "send"


def test_db_level_backstop_rejects_second_sent_row_for_same_key(db):
    conn, _ = db
    runs_row_id = _runs_row_id(conn)

    delivery.record_delivery(
        runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        delivery.record_delivery(
            runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash2", "sent", conn=conn
        )
        conn.commit()


def test_record_delivery_with_caller_conn_does_not_commit(db):
    conn, db_path = db
    runs_row_id = _runs_row_id(conn)

    delivery.record_delivery(
        runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn
    )
    # Deliberately NOT committed yet — record_delivery must not have done it
    # on our behalf when we passed our own connection in.

    other_conn = connect(db_path=db_path)
    try:
        row = other_conn.execute(
            "SELECT 1 FROM delivery_history WHERE status = 'sent'"
        ).fetchone()
        assert row is None, "record_delivery committed a caller-provided connection"
    finally:
        other_conn.close()

    conn.commit()  # now the caller commits, as the contract expects


def test_record_delivery_rejects_invalid_status(db):
    conn, _ = db
    runs_row_id = _runs_row_id(conn)
    with pytest.raises(ValueError):
        delivery.record_delivery(
            runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "hash1", "bogus", conn=conn
        )


def test_record_delivery_rejects_non_positive_runs_row_id(db):
    conn, _ = db
    with pytest.raises(ValueError):
        delivery.record_delivery(
            0, "2026-08-12", "DAILY", "kakao_memo", "hash1", "sent", conn=conn
        )


def test_record_delivery_rejects_empty_content_hash(db):
    conn, _ = db
    runs_row_id = _runs_row_id(conn)
    with pytest.raises(ValueError):
        delivery.record_delivery(
            runs_row_id, "2026-08-12", "DAILY", "kakao_memo", "", "sent", conn=conn
        )


def test_build_idempotency_key_rejects_colon_in_component():
    with pytest.raises(ValueError):
        delivery.build_idempotency_key("2026-08-12", "DAILY:X", "kakao_memo")


def test_build_idempotency_key_rejects_empty_component():
    with pytest.raises(ValueError):
        delivery.build_idempotency_key("", "DAILY", "kakao_memo")


def test_build_idempotency_key_is_deterministic():
    key1 = delivery.build_idempotency_key("2026-08-12", "DAILY", "kakao_memo")
    key2 = delivery.build_idempotency_key("2026-08-12", "DAILY", "kakao_memo")
    assert key1 == key2 == "2026-08-12:DAILY:kakao_memo"
