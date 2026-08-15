"""report.publication_consistency: the permanent regression guard for the
real incident where a real Kakao V2 send's report_date silently diverged
from the report_date actually published at docs/v2/ -- a real 200 status
and a real Kakao "sent" result each looked like PASS on their own. Every
branch here must resolve to an explicit status; none may default to
something that reads as "probably fine"."""

from pathlib import Path

import pytest

from db.database import connect, init_db
from delivery import record_delivery
from report.publication_consistency import (
    PublicationConsistencyStatus as Status,
    check_publication_consistency,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id, run_date="2026-08-15"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _record_sent_v2(conn, report_date, run_id="run-kakao"):
    run_row_id = _insert_run(conn, run_id, run_date=report_date)
    record_delivery(run_row_id, report_date, "DAILY_DIGEST_V2", "kakao_memo", "hash", "sent", conn=conn)
    conn.commit()


def _write_page(docs_v2_dir, relative_path, page_date):
    path = docs_v2_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<title>SUPER NEWS V2 — {page_date}</title>", encoding="utf-8")


# ---- no Kakao send yet: explicitly not a pass ---------------------------


def test_no_kakao_send_yet_is_explicit_and_never_consistent(conn, tmp_path):
    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.NO_KAKAO_SEND_YET
    assert result["consistent"] is False


# ---- consistent case ------------------------------------------------------


def test_matching_dates_are_reported_consistent(conn, tmp_path):
    _record_sent_v2(conn, "2026-08-15")
    _write_page(tmp_path, "index.html", "2026.08.15")
    _write_page(tmp_path, "reports/2026-08-15.html", "2026.08.15")

    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.CONSISTENT
    assert result["consistent"] is True
    assert result["kakao_report_date"] == "2026-08-15"
    assert result["public_index_date"] == "2026-08-15"
    assert result["dated_report_date"] == "2026-08-15"


# ---- the real incident: stale public index, real Kakao send for a newer date ----


def test_stale_public_index_is_reported_mismatch_not_pass(conn, tmp_path):
    _record_sent_v2(conn, "2026-08-15")
    _write_page(tmp_path, "index.html", "2026.08.14")  # stale, as in the real incident
    _write_page(tmp_path, "reports/2026-08-15.html", "2026.08.15")

    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.MISMATCH
    assert result["consistent"] is False
    assert result["kakao_report_date"] == "2026-08-15"
    assert result["public_index_date"] == "2026-08-14"


# ---- missing artifacts: explicit failure, never "pending" treated as pass ----


def test_missing_index_is_explicit_failure_not_pending_pass(conn, tmp_path):
    _record_sent_v2(conn, "2026-08-15")
    # index.html never written at all.
    _write_page(tmp_path, "reports/2026-08-15.html", "2026.08.15")

    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.INDEX_MISSING_OR_UNPARSEABLE
    assert result["consistent"] is False


def test_missing_dated_report_is_explicit_failure_not_pending_pass(conn, tmp_path):
    _record_sent_v2(conn, "2026-08-15")
    _write_page(tmp_path, "index.html", "2026.08.15")
    # reports/2026-08-15.html never written at all.

    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.DATED_REPORT_MISSING_OR_UNPARSEABLE
    assert result["consistent"] is False


def test_unparseable_index_title_is_explicit_failure(conn, tmp_path):
    _record_sent_v2(conn, "2026-08-15")
    path = tmp_path / "index.html"
    path.write_text("<title>not a date</title>", encoding="utf-8")
    _write_page(tmp_path, "reports/2026-08-15.html", "2026.08.15")

    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.INDEX_MISSING_OR_UNPARSEABLE
    assert result["consistent"] is False


# ---- three-way mismatch: index and dated report both wrong, differently ----


def test_index_and_dated_report_disagreeing_is_mismatch(conn, tmp_path):
    _record_sent_v2(conn, "2026-08-15")
    _write_page(tmp_path, "index.html", "2026.08.15")
    # Simulates a corrupted/partial regeneration where the dated archive
    # itself doesn't even match the date in its own filename.
    _write_page(tmp_path, "reports/2026-08-15.html", "2026.08.13")

    result = check_publication_consistency(conn, tmp_path)
    assert result["status"] == Status.MISMATCH
    assert result["consistent"] is False
    assert result["dated_report_date"] == "2026-08-13"
