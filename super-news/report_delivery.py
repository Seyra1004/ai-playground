"""Report V1 -> Kakao delivery.

REPORT (report/*) -> DELIVERY (this module) is a one-way, read-only
dependency: this module never writes to reports/run_category_status/
llm_interpretations, never re-runs report generation, and never calls the
LLM. It reads the latest persisted, authoritative report set for a KST
date, renders a deterministic digest (report/kakao_render.py -- no LLM
call), splits it to fit Kakao's per-message limit, and sends it via
kakao/client.py's send_memo(), reusing delivery.py's idempotency
(build_idempotency_key/decide_delivery_action/record_delivery) unchanged.

"Authoritative" report set = every `reports` row sharing the single most
recent run_id that produced ANY report for report_date_kst. Categories are
never mixed across different runs -- if AI/ECONOMY/SOCIETY came from run 7
but a stray MUSIC report also exists from run 5, only run 7's rows (whatever
they are) are used; run 5 is not consulted for MUSIC just because run 7
didn't happen to touch it. In practice every category is written by the
same run.report/orchestrator.py execution, so this rarely matters -- it
exists so a delivery attempt provably reflects one coherent report
generation, never a patchwork.
"""

import hashlib
import logging

from db.database import connect
from delivery import build_idempotency_key, decide_delivery_action, record_delivery
from kakao.auth import KakaoAuthError
from kakao.client import MAX_TEXT_LENGTH, KakaoSendError, send_memo
from config import MissingSecretError
from report.kakao_render import render_digest_text, split_message

logger = logging.getLogger(__name__)

REPORT_TYPE = "DAILY_DIGEST"
DESTINATION = "kakao_memo"

# Failure modes that must never crash a delivery attempt or leave a
# dangling `runs`/no delivery_history row -- see deliver_daily_report's
# per-chunk try/except. NoReportAvailableError is deliberately NOT in this
# tuple: "there's nothing to deliver" is a caller precondition problem, not
# a delivery-attempt outcome, so it propagates.
_DELIVERY_FAILURE_TYPES = (KakaoAuthError, KakaoSendError, MissingSecretError)


class NoReportAvailableError(RuntimeError):
    """Raised when zero persisted `reports` rows exist for the requested
    date -- there is nothing to deliver. deliver_daily_report never sends a
    placeholder/empty digest in this case; it lets this propagate."""


def find_latest_report_run_id(conn, report_date_kst):
    row = conn.execute(
        "SELECT MAX(run_id) AS run_id FROM reports WHERE report_date = ?",
        (report_date_kst,),
    ).fetchone()
    return row["run_id"]


def select_latest_reports(conn, report_date_kst):
    """Returns (reports_by_category, source_run_id). reports_by_category
    only contains categories that have a persisted `reports` row from the
    single latest run for this date -- a category with no row (NOT_READY or
    REPORT_FAILED that run) is simply absent, never fabricated. Raises
    NoReportAvailableError if there is no report at all for this date."""
    run_row_id = find_latest_report_run_id(conn, report_date_kst)
    if run_row_id is None:
        raise NoReportAvailableError(f"No persisted report exists for report_date={report_date_kst!r}.")

    rows = conn.execute(
        "SELECT category, content FROM reports WHERE run_id = ? AND report_date = ?",
        (run_row_id, report_date_kst),
    ).fetchall()
    return {row["category"]: row["content"] for row in rows}, run_row_id


def deliver_daily_report(report_date_kst, runs_row_id, conn=None):
    """Reads the latest persisted report set for report_date_kst, renders +
    splits it, and sends it via Kakao -- unless a 'sent' delivery already
    exists for this date (idempotent; see delivery.py's resend policy).
    `runs_row_id` is the caller's own runs.id (a delivery attempt is its own
    run, exactly like report/ingestion orchestrators -- it never reuses the
    report-generation run's id).

    Returns {"status": "sent"|"skipped_duplicate"|"failed",
    "message_count": int, "sent_count": int, "reason": str|None}. Never
    raises for an ordinary delivery failure (missing/expired token, refresh
    failure, Kakao API error, a partial multi-message send) -- those become
    status="failed" with `reason` populated and a 'failed' delivery_history
    row; "sent" is returned ONLY when every chunk was confirmed sent by
    Kakao. NoReportAvailableError (nothing to deliver at all) propagates --
    it's a precondition error, not a delivery-attempt outcome."""
    owns_conn = conn is None
    active_conn = conn if conn is not None else connect()
    try:
        idempotency_key = build_idempotency_key(report_date_kst, REPORT_TYPE, DESTINATION)
        action = decide_delivery_action(idempotency_key, conn=active_conn)
        if action == "skip_duplicate":
            logger.info("report_date=%s delivery skipped (already sent).", report_date_kst)
            return {"status": "skipped_duplicate", "message_count": 0, "sent_count": 0, "reason": None}

        reports_by_category, _source_run_id = select_latest_reports(active_conn, report_date_kst)
        digest_text = render_digest_text(report_date_kst, reports_by_category)
        chunks = split_message(digest_text, MAX_TEXT_LENGTH)
        content_hash = hashlib.sha256(digest_text.encode("utf-8")).hexdigest()

        sent_count = 0
        failure_reason = None
        for chunk in chunks:
            try:
                send_memo(chunk)
                sent_count += 1
            except _DELIVERY_FAILURE_TYPES as exc:
                failure_reason = f"chunk {sent_count + 1}/{len(chunks)} failed: {type(exc).__name__}: {exc}"
                logger.error(
                    "report_date=%s delivery FAILED at chunk %d/%d: %s",
                    report_date_kst, sent_count + 1, len(chunks), type(exc).__name__,
                )
                break

        status = "sent" if sent_count == len(chunks) else "failed"
        record_delivery(
            runs_row_id, report_date_kst, REPORT_TYPE, DESTINATION, content_hash, status,
            conn=active_conn, report_id=None,
        )
        active_conn.commit()
        logger.info(
            "report_date=%s delivery status=%s sent=%d/%d",
            report_date_kst, status, sent_count, len(chunks),
        )

        return {
            "status": status, "message_count": len(chunks),
            "sent_count": sent_count, "reason": failure_reason,
        }
    finally:
        if owns_conn:
            active_conn.close()
