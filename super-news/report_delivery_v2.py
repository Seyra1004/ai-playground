"""Report V2.1 -> Kakao delivery (FINAL MUSIC INTEGRATION / KAKAO E2E
phase). A separate, additive sibling of report_delivery.py (V1) -- V1's
own module, CLI, idempotency key space, and delivery_history rows are
never touched by this file. Exists because V1's live Kakao path only ever
sends V1's `reports` table content (AI/ECONOMY/SOCIETY news + a basic
Apple Music chart-diff line) and structurally cannot carry the completed
Music Intelligence capability (Genre/Production/Producer Reference Radar,
K-pop/A&R, Producer Intelligence) -- see report/kakao_render_v2.py's
render_full_digest_text for the real content this module sends instead.

Reads report.web_data_v2.build_dashboard_data_v2() directly (the same
real, read-only V2.1 data layer every other V2.1 consumer already uses)
-- never re-runs synthesis, never calls an LLM, never writes to
runs/run_category_status/llm_interpretations. Reuses delivery.py's
idempotency (build_idempotency_key/decide_delivery_action/record_delivery)
and kakao/client.py's send_memo() exactly like report_delivery.py does,
under a DISTINCT REPORT_TYPE ("DAILY_DIGEST_V2") so this module's
delivery_history rows can never collide with, shadow, or be mistaken for
V1's own ("DAILY_DIGEST") -- a "sent" V1 digest today does not skip a V2
digest today, and vice versa; each is tracked and idempotent
independently.
"""

import hashlib
import logging

from db.database import connect
from delivery import build_idempotency_key, decide_delivery_action, record_delivery
from kakao.auth import KakaoAuthError
from kakao.client import MAX_TEXT_LENGTH, KakaoSendError, send_memo
from config import MissingSecretError, get_optional_env
from report.kakao_render import split_message
from report.kakao_render_v2 import render_full_digest_text
from report.web_data_v2 import build_dashboard_data_v2

logger = logging.getLogger(__name__)

REPORT_TYPE = "DAILY_DIGEST_V2"
DESTINATION = "kakao_memo"

# Same failure-mode contract as report_delivery.py's own _DELIVERY_FAILURE_TYPES.
_DELIVERY_FAILURE_TYPES = (KakaoAuthError, KakaoSendError, MissingSecretError)


def _resolve_v2_link_url():
    """kakao.client.send_memo() already treats KAKAO_DEFAULT_LINK_URL as
    required and validates it against the domain registered in the Kakao
    app's own 링크 설정 -- that domain check is host-level, not path-level,
    so a path appended to the SAME already-registered base needs no new
    Kakao app configuration. Without this, V2's own '전체 브리핑 -> ' CTA
    would silently inherit V1's shared default link (the site root, which
    serves V1's stale content, not the real V2.1 dashboard this message is
    actually about).

    KAKAO_V2_LINK_URL overrides the derivation when set. Returns None
    (matching send_memo's own default parameter) when the base itself
    isn't configured, so send_memo's existing MissingSecretError contract
    for a completely unset link is unchanged."""
    override = get_optional_env("KAKAO_V2_LINK_URL")
    if override:
        return override
    base = get_optional_env("KAKAO_DEFAULT_LINK_URL")
    if not base:
        return None
    return base.rstrip("/") + "/v2/"


class NoDashboardDataError(RuntimeError):
    """Raised when build_dashboard_data_v2 has literally nothing real to
    report for this date (every section UNAVAILABLE/DEGRADED/empty) --
    there is nothing meaningful to deliver. deliver_daily_report_v2 never
    sends an all-empty placeholder digest in this case; it lets this
    propagate, mirroring report_delivery.py's own NoReportAvailableError
    precondition-error contract."""


def _dashboard_has_any_real_content(dashboard_data_v2):
    news_has_items = any(dashboard_data_v2["news"][c]["items"] for c in dashboard_data_v2["news"])
    spotify_has_data = dashboard_data_v2["spotify_chart"]["state"] == "NORMAL"
    music_trend_has_data = dashboard_data_v2["music_trend_intelligence"]["state"] == "NORMAL"
    producer_has_data = dashboard_data_v2["producer_intelligence"]["state"] == "NORMAL"
    return news_has_items or spotify_has_data or music_trend_has_data or producer_has_data


def deliver_daily_report_v2(report_date_kst, runs_row_id, conn=None):
    """Reads the real, current V2.1 dashboard data for report_date_kst,
    renders + splits the full digest, and sends it via Kakao -- unless a
    'sent' V2 delivery already exists for this date (idempotent, own key
    space -- see module docstring). Returns {"status": "sent"|
    "skipped_duplicate"|"failed", "message_count": int, "sent_count": int,
    "reason": str|None}. Never raises for an ordinary delivery failure
    (missing/expired token, refresh failure, Kakao API error, a partial
    multi-message send) -- those become status="failed" with `reason`
    populated and a 'failed' delivery_history row; "sent" is returned ONLY
    when every chunk was confirmed sent by Kakao. NoDashboardDataError
    (nothing real to deliver at all) propagates -- a precondition error,
    not a delivery-attempt outcome."""
    owns_conn = conn is None
    active_conn = conn if conn is not None else connect()
    try:
        idempotency_key = build_idempotency_key(report_date_kst, REPORT_TYPE, DESTINATION)
        action = decide_delivery_action(idempotency_key, conn=active_conn)
        if action == "skip_duplicate":
            logger.info("report_date=%s V2 delivery skipped (already sent).", report_date_kst)
            return {"status": "skipped_duplicate", "message_count": 0, "sent_count": 0, "reason": None}

        dashboard_data_v2 = build_dashboard_data_v2(active_conn, report_date_kst)
        if not _dashboard_has_any_real_content(dashboard_data_v2):
            raise NoDashboardDataError(
                f"No real V2.1 dashboard content exists yet for report_date={report_date_kst!r}."
            )

        digest_text = render_full_digest_text(dashboard_data_v2)
        chunks = split_message(digest_text, MAX_TEXT_LENGTH)
        content_hash = hashlib.sha256(digest_text.encode("utf-8")).hexdigest()

        v2_link_url = _resolve_v2_link_url()

        sent_count = 0
        failure_reason = None
        for chunk in chunks:
            try:
                send_memo(chunk, link_url=v2_link_url)
                sent_count += 1
            except _DELIVERY_FAILURE_TYPES as exc:
                failure_reason = f"chunk {sent_count + 1}/{len(chunks)} failed: {type(exc).__name__}: {exc}"
                logger.error(
                    "report_date=%s V2 delivery FAILED at chunk %d/%d: %s",
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
            "report_date=%s V2 delivery status=%s sent=%d/%d",
            report_date_kst, status, sent_count, len(chunks),
        )

        return {
            "status": status, "message_count": len(chunks),
            "sent_count": sent_count, "reason": failure_reason,
        }
    finally:
        if owns_conn:
            active_conn.close()
