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
import time

from db.database import connect
from delivery import build_idempotency_key, decide_delivery_action, record_delivery
from kakao.auth import KakaoAuthError
from kakao.client import MAX_TEXT_LENGTH, KakaoSendError, send_feed_memo, send_memo
from config import MissingSecretError, get_optional_env
from report.kakao_render import split_message
from report.kakao_render_v2 import (
    render_daily_kakao_digest,
    render_full_digest_text,
    render_kakao_digest,
    render_music_kakao_digest,
)
from report.web_data_v2 import build_dashboard_data_v2

logger = logging.getLogger(__name__)

REPORT_TYPE = "DAILY_DIGEST_V2"
DESTINATION = "kakao_memo"

# SUPER NEWS MUSIC / SUPER NEWS DAILY (Kakao delivery split phase): two
# fully independent products, each with its OWN report_type -> own
# idempotency key space. Deliberately DISTINCT from the legacy REPORT_TYPE
# above (still used by deliver_daily_report_v2/deliver_daily_summary_v2,
# left intact for manual/audit use) -- a legacy 'sent' row must never skip
# a MUSIC or DAILY send, and vice versa; each product's own send history is
# tracked and idempotent independently, per-date.
MUSIC_REPORT_TYPE = "SUPER_NEWS_MUSIC_V2"
DAILY_REPORT_TYPE = "SUPER_NEWS_DAILY_V2"

# Minimal retry: a real transient Kakao send failure (network blip, 5xx) is
# retried once more after a short fixed backoff before being recorded as
# 'failed'. Never retries a deterministic validation failure differently --
# KakaoValidationError is a KakaoSendError subclass and would fail
# identically on every attempt, so retrying it is harmless, just redundant.
_MAX_SEND_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 3

# Same failure-mode contract as report_delivery.py's own _DELIVERY_FAILURE_TYPES.
_DELIVERY_FAILURE_TYPES = (KakaoAuthError, KakaoSendError, MissingSecretError)


def _resolve_v2_link_url(page=None):
    """kakao.client.send_memo() already treats KAKAO_DEFAULT_LINK_URL as
    required and validates it against the domain registered in the Kakao
    app's own 링크 설정 -- that domain check is host-level, not path-level,
    so a path appended to the SAME already-registered base needs no new
    Kakao app configuration. Without this, V2's own '전체 브리핑 -> ' CTA
    would silently inherit V1's shared default link (the site root, which
    serves V1's stale content, not the real V2.1 dashboard this message is
    actually about).

    `page` (e.g. "music.html"/"daily.html") targets the standalone
    SUPER NEWS MUSIC / SUPER NEWS DAILY product pages (report.web_render_v2's
    render_music_page_html_v2/render_daily_page_html_v2) instead of the
    combined dashboard -- used only by deliver_music_digest_v2/
    deliver_daily_digest_v2 below; every other caller passes nothing and
    keeps linking to the combined "<base>/v2/" dashboard exactly as before.

    KAKAO_V2_LINK_URL overrides the derivation when set (for every caller,
    page-specific or not -- an explicit human override always wins).
    Returns None (matching send_memo's own default parameter) when the
    base itself isn't configured, so send_memo's existing MissingSecretError
    contract for a completely unset link is unchanged."""
    override = get_optional_env("KAKAO_V2_LINK_URL")
    if override:
        return override
    base = get_optional_env("KAKAO_DEFAULT_LINK_URL")
    if not base:
        return None
    if page:
        return base.rstrip("/") + f"/v2/{page}"
    return base.rstrip("/") + "/v2/"


def _resolve_v2_archive_url(product, report_date_kst):
    """The permanent DATE-FIXED archive URL for `product` ("music"/
    "daily") at `report_date_kst` -- report.release_v2._dated_archive_
    path's own docs/v2/reports/<product>/<date>.html convention, ADDED
    2026-08-22 for the Kakao two-link requirement (date-fixed archive +
    always-current latest page). Built from report_date_kst, NEVER from
    "today" -- a message sent for an earlier date always resolves to that
    SAME earlier date's own archive file, immutably. Same KAKAO_DEFAULT_
    LINK_URL/KAKAO_V2_LINK_URL base resolution as _resolve_v2_link_url
    (KAKAO_V2_LINK_URL override intentionally does NOT apply here -- an
    override replaces the "latest" link's own base, but the date-fixed
    archive must still always resolve deterministically from base + date,
    never a fixed override that would silently point every date at the
    same URL). Returns None when the base itself isn't configured, same
    contract as _resolve_v2_link_url."""
    base = get_optional_env("KAKAO_DEFAULT_LINK_URL")
    if not base:
        return None
    return base.rstrip("/") + f"/v2/reports/{product}/{report_date_kst}.html"
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


def _music_has_any_real_content(dashboard_data_v2):
    news = dashboard_data_v2["news"]
    news_has_items = any(news[c]["items"] for c in ("TIKTOK", "SPOTIFY"))
    spotify_has_data = dashboard_data_v2["spotify_chart"]["state"] == "NORMAL"
    music_trend_has_data = dashboard_data_v2["music_trend_intelligence"]["state"] == "NORMAL"
    producer_has_data = dashboard_data_v2["producer_intelligence"]["state"] == "NORMAL"
    return news_has_items or spotify_has_data or music_trend_has_data or producer_has_data


def _daily_has_any_real_content(dashboard_data_v2):
    news = dashboard_data_v2["news"]
    return any(news[c]["items"] for c in ("AI", "ECONOMY", "SOCIETY"))


def _send_with_retry(send_fn, product_label, report_date_kst):
    """Attempts `send_fn()` (a zero-arg callable -- e.g. a send_memo or
    send_feed_memo call already bound to its own args) up to
    _MAX_SEND_ATTEMPTS times with a short fixed backoff between attempts,
    logging every attempt (product/date/attempt number/outcome) so a real
    failure sequence is diagnosable from logs alone. Re-raises the last
    failure's exception, unmodified, if every attempt fails -- callers
    keep their existing _DELIVERY_FAILURE_TYPES handling unchanged."""
    last_exc = None
    for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
        try:
            send_fn()
            logger.info(
                "report_date=%s product=%s send attempt=%d/%d status=SUCCESS",
                report_date_kst, product_label, attempt, _MAX_SEND_ATTEMPTS,
            )
            return
        except _DELIVERY_FAILURE_TYPES as exc:
            last_exc = exc
            logger.error(
                "report_date=%s product=%s send attempt=%d/%d status=FAILED error=%s",
                report_date_kst, product_label, attempt, _MAX_SEND_ATTEMPTS, type(exc).__name__,
            )
            if attempt < _MAX_SEND_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS)
    raise last_exc


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


_CTA_BUTTON_TITLE = "전체 브리핑"


def deliver_daily_summary_v2(report_date_kst, runs_row_id, conn=None):
    """KAKAO PRODUCT CONTRACT (quality-hardening phase): Kakao is ONLY a
    daily notification + short executive summary + entry point to the
    full web dashboard -- never the full news container (that's the V2.1
    web dashboard, report.web_data_v2/report.web_render_v2). This is the
    PRODUCTION DAILY delivery path: exactly ONE real Kakao message
    (report.kakao_render_v2.render_kakao_digest -- a single, deterministic,
    <=200-char string; never report.kakao_render.split_message, never
    multiple send_memo() calls). Shares the EXACT SAME idempotency key
    space as deliver_daily_report_v2 above (REPORT_TYPE=DAILY_DIGEST_V2) --
    deliberately, not a distinct key: whichever V2 delivery path runs
    first for a given report_date_kst is the one real send for that date,
    so at most one of {this compact summary, the older full multi-chunk
    digest} can ever be 'sent' for the same date, never both. The daily
    automated pipeline calls ONLY this function; deliver_daily_report_v2
    remains available for manual/audit invocation but is naturally blocked
    by this same idempotency guard once either one has sent for a date.

    Returns {"status": "sent"|"skipped_duplicate"|"failed", "reason":
    str|None} -- no `message_count`/`sent_count` chunk bookkeeping (there
    is exactly one message by construction). NoDashboardDataError
    propagates exactly as deliver_daily_report_v2's own precondition
    contract already documents."""
    owns_conn = conn is None
    active_conn = conn if conn is not None else connect()
    try:
        idempotency_key = build_idempotency_key(report_date_kst, REPORT_TYPE, DESTINATION)
        action = decide_delivery_action(idempotency_key, conn=active_conn)
        if action == "skip_duplicate":
            logger.info("report_date=%s V2 summary delivery skipped (already sent).", report_date_kst)
            return {"status": "skipped_duplicate", "reason": None}

        dashboard_data_v2 = build_dashboard_data_v2(active_conn, report_date_kst)
        if not _dashboard_has_any_real_content(dashboard_data_v2):
            raise NoDashboardDataError(
                f"No real V2.1 dashboard content exists yet for report_date={report_date_kst!r}."
            )

        text = render_kakao_digest(dashboard_data_v2)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        v2_link_url = _resolve_v2_link_url()

        try:
            send_memo(text, link_url=v2_link_url, button_title=_CTA_BUTTON_TITLE)
            status, failure_reason = "sent", None
        except _DELIVERY_FAILURE_TYPES as exc:
            status = "failed"
            failure_reason = f"{type(exc).__name__}: {exc}"
            logger.error("report_date=%s V2 summary delivery FAILED: %s", report_date_kst, type(exc).__name__)

        record_delivery(
            runs_row_id, report_date_kst, REPORT_TYPE, DESTINATION, content_hash, status,
            conn=active_conn, report_id=None,
        )
        active_conn.commit()
        logger.info("report_date=%s V2 summary delivery status=%s", report_date_kst, status)

        return {"status": status, "reason": failure_reason}
    finally:
        if owns_conn:
            active_conn.close()


# =============================================================================
# SUPER NEWS MUSIC / SUPER NEWS DAILY (Kakao delivery split phase) -- two
# fully independent daily products. Each is exactly ONE real Kakao message
# (report.kakao_render_v2.render_music_kakao_digest / render_daily_kakao_
# digest), retried via _send_with_retry, and idempotent under its OWN
# report_type (MUSIC_REPORT_TYPE / DAILY_REPORT_TYPE) -- distinct from each
# other AND from the legacy REPORT_TYPE above, so any combination of
# {legacy, MUSIC, DAILY} sends for the same date are tracked and gated
# completely independently; no combination ever blocks or is blocked by
# another. This is what scripts/run_daily_kakao_delivery_v2.py (the single
# daily production entrypoint) actually calls.
# =============================================================================


def deliver_music_digest_v2(report_date_kst, runs_row_id, conn=None, dashboard_data_v2=None):
    """SUPER NEWS MUSIC daily send. dashboard_data_v2 may be passed in
    pre-built (e.g. by a caller also sending DAILY from the same
    build_dashboard_data_v2() call) to avoid rebuilding it twice; if
    omitted, this builds it itself. Returns {"status": "sent"|
    "skipped_duplicate"|"failed", "reason": str|None}. NoDashboardDataError
    propagates when there is genuinely no real MUSIC content for this date
    (mirrors every other delivery function in this module)."""
    owns_conn = conn is None
    active_conn = conn if conn is not None else connect()
    try:
        idempotency_key = build_idempotency_key(report_date_kst, MUSIC_REPORT_TYPE, DESTINATION)
        action = decide_delivery_action(idempotency_key, conn=active_conn)
        if action == "skip_duplicate":
            logger.info("report_date=%s MUSIC delivery skipped (already sent).", report_date_kst)
            return {"status": "skipped_duplicate", "reason": None}

        if dashboard_data_v2 is None:
            dashboard_data_v2 = build_dashboard_data_v2(active_conn, report_date_kst)
        if not _music_has_any_real_content(dashboard_data_v2):
            raise NoDashboardDataError(
                f"No real MUSIC content exists yet for report_date={report_date_kst!r}."
            )

        text = render_music_kakao_digest(dashboard_data_v2)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        latest_url = _resolve_v2_link_url("music.html")
        dated_url = _resolve_v2_archive_url("music", report_date_kst)

        try:
            _send_with_retry(
                lambda: send_feed_memo(
                    "SUPER NEWS MUSIC", text, link_url=latest_url,
                    buttons=[("오늘 MUSIC", dated_url), ("최신 MUSIC", latest_url)],
                ),
                "MUSIC", report_date_kst,
            )
            status, failure_reason = "sent", None
        except _DELIVERY_FAILURE_TYPES as exc:
            status = "failed"
            failure_reason = f"{type(exc).__name__}: {exc}"

        record_delivery(
            runs_row_id, report_date_kst, MUSIC_REPORT_TYPE, DESTINATION, content_hash, status,
            conn=active_conn, report_id=None,
        )
        active_conn.commit()
        logger.info("report_date=%s MUSIC delivery status=%s", report_date_kst, status)

        return {"status": status, "reason": failure_reason}
    finally:
        if owns_conn:
            active_conn.close()


def deliver_daily_digest_v2(report_date_kst, runs_row_id, conn=None, dashboard_data_v2=None):
    """SUPER NEWS DAILY daily send (AI/ECONOMY/SOCIETY only -- never Music).
    Same contract shape as deliver_music_digest_v2 above, independent
    report_type/idempotency key."""
    owns_conn = conn is None
    active_conn = conn if conn is not None else connect()
    try:
        idempotency_key = build_idempotency_key(report_date_kst, DAILY_REPORT_TYPE, DESTINATION)
        action = decide_delivery_action(idempotency_key, conn=active_conn)
        if action == "skip_duplicate":
            logger.info("report_date=%s DAILY delivery skipped (already sent).", report_date_kst)
            return {"status": "skipped_duplicate", "reason": None}

        if dashboard_data_v2 is None:
            dashboard_data_v2 = build_dashboard_data_v2(active_conn, report_date_kst)
        if not _daily_has_any_real_content(dashboard_data_v2):
            raise NoDashboardDataError(
                f"No real DAILY (AI/ECONOMY/SOCIETY) content exists yet for report_date={report_date_kst!r}."
            )

        text = render_daily_kakao_digest(dashboard_data_v2)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        latest_url = _resolve_v2_link_url("daily.html")
        dated_url = _resolve_v2_archive_url("daily", report_date_kst)

        try:
            _send_with_retry(
                lambda: send_feed_memo(
                    "SUPER NEWS DAILY", text, link_url=latest_url,
                    buttons=[("오늘 DAILY", dated_url), ("최신 DAILY", latest_url)],
                ),
                "DAILY", report_date_kst,
            )
            status, failure_reason = "sent", None
        except _DELIVERY_FAILURE_TYPES as exc:
            status = "failed"
            failure_reason = f"{type(exc).__name__}: {exc}"

        record_delivery(
            runs_row_id, report_date_kst, DAILY_REPORT_TYPE, DESTINATION, content_hash, status,
            conn=active_conn, report_id=None,
        )
        active_conn.commit()
        logger.info("report_date=%s DAILY delivery status=%s", report_date_kst, status)

        return {"status": status, "reason": failure_reason}
    finally:
        if owns_conn:
            active_conn.close()
