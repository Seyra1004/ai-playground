"""report_delivery_v2: real-current-dashboard selection (via report.
web_data_v2.build_dashboard_data_v2), duplicate-send prevention (own
idempotency key space, distinct from V1's), Kakao API/auth failure
handling, no false-success on a partial multi-message send, and V1/V2
independence (a 'sent' V1 digest for a date never blocks or is blocked by
a V2 digest for that same date). Kakao is always mocked
(report_delivery_v2.send_memo) -- no live network/API call."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from kakao.auth import ReauthRequiredError
from kakao.client import MAX_TEXT_LENGTH, KakaoSendError
from report_delivery_v2 import NoDashboardDataError, deliver_daily_report_v2, deliver_daily_summary_v2


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id, run_date="2026-08-14"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_producer_intelligence(conn, run_row_id, report_date="2026-08-14"):
    """Real, minimal V2.1 content so build_dashboard_data_v2 has something
    to deliver -- mirrors the exact shape report.web_data_v2.
    _producer_intelligence_section reads."""
    import json

    output = {"insights": [{
        "what_is_moving": "Test hook-first intro trending", "why_it_matters": "signals agree",
        "what_to_watch": "next observation", "what_could_i_make_now": "test a demo hook",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, 'MUSIC_PRODUCER_INTELLIGENCE', 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, json.dumps(output)),
    )
    conn.commit()


# ---- no real content: precondition error, nothing sent ----------------------


def test_no_real_content_raises_and_sends_nothing(conn):
    with patch("report_delivery_v2.send_memo") as mock_send:
        with pytest.raises(NoDashboardDataError):
            deliver_daily_report_v2("2026-08-14", _insert_run(conn, "run-delivery"), conn=conn)
    mock_send.assert_not_called()


# ---- successful delivery -----------------------------------------------------


def test_successful_delivery_sends_all_chunks_and_records_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo") as mock_send:
        result = deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "sent"
    assert result["sent_count"] == result["message_count"] == mock_send.call_count
    assert result["sent_count"] >= 1

    row = conn.execute(
        "SELECT status, report_type FROM delivery_history WHERE report_date='2026-08-14'"
    ).fetchone()
    assert row["status"] == "sent"
    assert row["report_type"] == "DAILY_DIGEST_V2"


# ---- duplicate-send prevention (own key space) -------------------------------


def test_duplicate_send_is_skipped_and_kakao_never_called(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_1 = _insert_run(conn, "run-delivery-1")

    with patch("report_delivery_v2.send_memo"):
        first = deliver_daily_report_v2("2026-08-14", delivery_run_1, conn=conn)
    assert first["status"] == "sent"

    delivery_run_2 = _insert_run(conn, "run-delivery-2")
    with patch("report_delivery_v2.send_memo") as mock_send:
        second = deliver_daily_report_v2("2026-08-14", delivery_run_2, conn=conn)

    assert second["status"] == "skipped_duplicate"
    mock_send.assert_not_called()

    sent_count = conn.execute(
        "SELECT COUNT(*) FROM delivery_history WHERE report_date='2026-08-14' AND status='sent' AND report_type='DAILY_DIGEST_V2'"
    ).fetchone()[0]
    assert sent_count == 1  # never duplicated


def test_v1_sent_digest_never_blocks_or_is_blocked_by_v2(conn):
    """V1 (report_delivery.py, REPORT_TYPE='DAILY_DIGEST') and V2
    (report_delivery_v2.py, REPORT_TYPE='DAILY_DIGEST_V2') must be
    independently idempotent for the same date -- a real V1 send today
    must never cause V2's own duplicate-guard to skip, and vice versa."""
    from delivery import record_delivery

    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)

    # Simulate a prior real V1 'sent' delivery for the same date.
    v1_run_row_id = _insert_run(conn, "run-v1-delivery")
    record_delivery(v1_run_row_id, "2026-08-14", "DAILY_DIGEST", "kakao_memo", "somehash", "sent", conn=conn)
    conn.commit()

    delivery_run_row_id = _insert_run(conn, "run-v2-delivery")
    with patch("report_delivery_v2.send_memo") as mock_send:
        result = deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "sent"  # V1's own 'sent' row never blocks V2
    mock_send.assert_called()


# ---- Kakao API failure handling: no false-success ----------------------------


def test_kakao_send_error_is_recorded_as_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo", side_effect=KakaoSendError("boom")):
        result = deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    assert result["sent_count"] == 0
    row = conn.execute(
        "SELECT status FROM delivery_history WHERE report_date='2026-08-14'"
    ).fetchone()
    assert row["status"] == "failed"


def test_kakao_auth_failure_is_recorded_as_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo", side_effect=ReauthRequiredError("token dead")):
        result = deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    assert "ReauthRequiredError" in result["reason"]


# ---- partial multi-message send: no false-success ----------------------------


def test_partial_multi_message_send_is_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    # First chunk succeeds, second raises -- a real partial send. The real
    # digest for even minimal content already splits into >1 chunk (see
    # report/kakao_render_v2.py's own section structure).
    with patch("report_delivery_v2.send_memo", side_effect=[None, KakaoSendError("boom")]):
        result = deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    assert 0 < result["sent_count"] < result["message_count"]
    row = conn.execute(
        "SELECT status FROM delivery_history WHERE report_date='2026-08-14'"
    ).fetchone()
    assert row["status"] == "failed"  # never "sent" when only some chunks landed


# ---- V2-specific CTA link (real V2 dashboard, not V1's shared default) -------


def test_link_url_derived_from_default_when_v2_override_unset(conn, monkeypatch):
    monkeypatch.delenv("KAKAO_V2_LINK_URL", raising=False)
    monkeypatch.setenv("KAKAO_DEFAULT_LINK_URL", "https://example.github.io/ai-playground")
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo") as mock_send:
        deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert mock_send.call_count >= 1
    for call in mock_send.call_args_list:
        assert call.kwargs["link_url"] == "https://example.github.io/ai-playground/v2/"


def test_link_url_uses_explicit_v2_override_when_set(conn, monkeypatch):
    monkeypatch.setenv("KAKAO_DEFAULT_LINK_URL", "https://example.github.io/ai-playground")
    monkeypatch.setenv("KAKAO_V2_LINK_URL", "https://example.github.io/ai-playground/v2/reports/2026-08-14.html")
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo") as mock_send:
        deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    for call in mock_send.call_args_list:
        assert call.kwargs["link_url"] == "https://example.github.io/ai-playground/v2/reports/2026-08-14.html"


def test_link_url_is_none_when_default_unset(conn, monkeypatch):
    """Mirrors send_memo's own MissingSecretError contract for a completely
    unset link -- report_delivery_v2.py must not mask that by inventing a
    fallback URL of its own."""
    monkeypatch.delenv("KAKAO_V2_LINK_URL", raising=False)
    monkeypatch.delenv("KAKAO_DEFAULT_LINK_URL", raising=False)
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo") as mock_send:
        deliver_daily_report_v2("2026-08-14", delivery_run_row_id, conn=conn)

    for call in mock_send.call_args_list:
        assert call.kwargs["link_url"] is None


# =============================================================================
# deliver_daily_summary_v2: the PRODUCTION DAILY compact-message path
# (KAKAO PRODUCT CONTRACT, quality-hardening phase) -- exactly ONE real
# send_memo() call, never split_message(), sharing the SAME idempotency key
# space as deliver_daily_report_v2 above.
# =============================================================================


def test_summary_sends_exactly_one_message_never_chunked(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo") as mock_send:
        result = deliver_daily_summary_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "sent"
    assert mock_send.call_count == 1  # exactly one Kakao message, never split
    sent_text = mock_send.call_args.args[0]
    assert len(sent_text) <= MAX_TEXT_LENGTH

    row = conn.execute(
        "SELECT status, report_type FROM delivery_history WHERE report_date='2026-08-14'"
    ).fetchone()
    assert row["status"] == "sent"
    assert row["report_type"] == "DAILY_DIGEST_V2"


def test_summary_duplicate_send_is_skipped(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_1 = _insert_run(conn, "run-delivery-1")

    with patch("report_delivery_v2.send_memo"):
        first = deliver_daily_summary_v2("2026-08-14", delivery_run_1, conn=conn)
    assert first["status"] == "sent"

    delivery_run_2 = _insert_run(conn, "run-delivery-2")
    with patch("report_delivery_v2.send_memo") as mock_send:
        second = deliver_daily_summary_v2("2026-08-14", delivery_run_2, conn=conn)

    assert second["status"] == "skipped_duplicate"
    mock_send.assert_not_called()

    sent_count = conn.execute(
        "SELECT COUNT(*) FROM delivery_history WHERE report_date='2026-08-14' AND status='sent'"
    ).fetchone()[0]
    assert sent_count == 1


def test_summary_shares_idempotency_key_with_full_digest_at_most_one_ever_sent(conn):
    """A prior real full-digest send (deliver_daily_report_v2) for this date
    must block the compact summary from ALSO sending -- KAKAO_MESSAGE_COUNT_
    PER_REPORT_DATE=1 holds regardless of which V2 delivery function runs
    first."""
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)

    delivery_run_1 = _insert_run(conn, "run-delivery-1")
    with patch("report_delivery_v2.send_memo"):
        first = deliver_daily_report_v2("2026-08-14", delivery_run_1, conn=conn)
    assert first["status"] == "sent"

    delivery_run_2 = _insert_run(conn, "run-delivery-2")
    with patch("report_delivery_v2.send_memo") as mock_send:
        second = deliver_daily_summary_v2("2026-08-14", delivery_run_2, conn=conn)

    assert second["status"] == "skipped_duplicate"
    mock_send.assert_not_called()
    sent_count = conn.execute(
        "SELECT COUNT(*) FROM delivery_history WHERE report_date='2026-08-14' AND status='sent'"
    ).fetchone()[0]
    assert sent_count == 1


def test_summary_cta_targets_v2_link(conn, monkeypatch):
    monkeypatch.delenv("KAKAO_V2_LINK_URL", raising=False)
    monkeypatch.setenv("KAKAO_DEFAULT_LINK_URL", "https://example.github.io/ai-playground")
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo") as mock_send:
        deliver_daily_summary_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert mock_send.call_args.kwargs["link_url"] == "https://example.github.io/ai-playground/v2/"


def test_summary_no_real_content_raises_and_sends_nothing(conn):
    with patch("report_delivery_v2.send_memo") as mock_send:
        with pytest.raises(NoDashboardDataError):
            deliver_daily_summary_v2("2026-08-14", _insert_run(conn, "run-delivery"), conn=conn)
    mock_send.assert_not_called()


def test_summary_kakao_send_error_is_recorded_as_failed_not_sent(conn):
    run_row_id = _insert_run(conn, "run-1")
    _insert_producer_intelligence(conn, run_row_id)
    delivery_run_row_id = _insert_run(conn, "run-delivery")

    with patch("report_delivery_v2.send_memo", side_effect=KakaoSendError("boom")):
        result = deliver_daily_summary_v2("2026-08-14", delivery_run_row_id, conn=conn)

    assert result["status"] == "failed"
    row = conn.execute("SELECT status FROM delivery_history WHERE report_date='2026-08-14'").fetchone()
    assert row["status"] == "failed"
