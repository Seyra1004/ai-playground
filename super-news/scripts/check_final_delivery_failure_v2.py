"""SUPER NEWS final-failure alert (PRE-PRODUCTION HARDENING, ADDED
2026-08-22) -- the smallest safe way to avoid a silently-missed DAILY/
MUSIC Kakao delivery: after the bounded retry window
(super-news-delivery-retry.timer's own 3 fixed slots, 07:10/07:25/07:55
KST) has genuinely run out, sends ONE short Korean "나에게 보내기" alert
per product/report_date if that product still has no successful delivery
-- never during implementation/testing, never speculatively.

    .venv\\Scripts\\python.exe scripts\\check_final_delivery_failure_v2.py [--report-date YYYY-MM-DD] [--db-path PATH] [--force]

TIME-GATED, no new systemd unit/timer needed: called from the END of
scripts/deliver_retry.sh (which already runs at exactly those 3 slots)
-- before the real 07:55 (last) slot, main() no-ops immediately (real
retries may still succeed, an alert here would be a false alarm). Only at
or after _ALERT_EARLIEST_KST (07:50, a few minutes before the last real
retry slot fires, so the alert always reflects that slot's own outcome)
does this evaluate for real. `--force` (test/manual-diagnosis only) skips
the time gate.

DEDUPLICATION reuses the EXISTING, already-tested delivery.py idempotency
infrastructure under a DISTINCT report_type/destination namespace
("<PRODUCT>_V2_FAILURE_ALERT" / "kakao_alert") -- completely separate
from MUSIC_REPORT_TYPE/DAILY_REPORT_TYPE, so this can never affect, skip,
or be skipped by a real DAILY/MUSIC delivery's own idempotency. At most
ONE alert per product/report_date, ever (a 'sent' alert row blocks
every later check the same way a real delivery's own 'sent' row does).

Never raises for an ordinary Kakao send failure -- if Kakao itself is
unavailable when an alert is due, that failure is logged (journal remains
authoritative) and this exits 0 regardless, so a Kakao outage can never
turn into a crashed retry-alert timer or a stuck run.

No secrets, no stack trace, no repeated spam -- see _ALERT_TEXT.

Exit code contract: always 0 on normal completion (including "nothing due
yet" and "alert send itself failed") -- this is a best-effort notifier,
never a stage whose own failure should cascade. 2 = CLI invocation error.
"""

import argparse
import logging
import os
import secrets
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

os.environ["SUPER_NEWS_NO_PAID_API"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH
from db.database import connect
from delivery import build_idempotency_key, decide_delivery_action, record_delivery
from ingestion.orchestrator import finalize_run, start_run
from kakao.client import KakaoSendError, KakaoValidationError, send_memo

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2

_KST = timezone(timedelta(hours=9))
_ALERT_EARLIEST_KST = time(7, 50)
_ALERT_DESTINATION = "kakao_alert"

# report_type -> (real production report_type this alert watches, Korean product label)
_WATCHED_PRODUCTS = (
    ("SUPER_NEWS_MUSIC_V2", "MUSIC"),
    ("SUPER_NEWS_DAILY_V2", "DAILY"),
)

_ALERT_TEXT = (
    "[SUPER NEWS 오류]\n"
    "07:00 자동 발송 실패\n"
    "PRODUCT: {product}\n"
    "STAGE: kakao_delivery\n"
    "재시도 실패\n"
    "확인 필요"
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Send one deduplicated Kakao alert per product if today's delivery genuinely never succeeded."
    )
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--report-date", type=str, default=None)
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the time gate (07:50 KST) -- test/manual-diagnosis only, never used by the real retry timer.",
    )
    return parser.parse_args(argv)


def main(argv=None, now_fn=None):
    """`now_fn` (optional): a zero-arg callable returning the current KST
    datetime, for deterministic time-gate tests -- never real wall-clock
    time in a test. Defaults to datetime.now(_KST)."""
    logging_setup.setup_logging()
    args = _parse_args(argv)

    now_fn = now_fn or (lambda: datetime.now(_KST))
    now_kst = now_fn()
    if not args.force and now_kst.time() < _ALERT_EARLIEST_KST:
        print(f"too early ({now_kst.strftime('%H:%M')} KST < {_ALERT_EARLIEST_KST.strftime('%H:%M')} KST) -- retries may still succeed, no check performed.")
        return EXIT_OK

    db_path = args.db_path if args.db_path is not None else DB_PATH
    report_date_kst = args.report_date or now_kst.strftime("%Y-%m-%d")

    conn = connect(db_path=db_path)
    try:
        due = []
        for real_report_type, product_label in _WATCHED_PRODUCTS:
            real_key = build_idempotency_key(report_date_kst, real_report_type, "kakao_memo")
            if decide_delivery_action(real_key, conn=conn) == "skip_duplicate":
                print(f"product={product_label} status=OK (already sent) -- no alert needed.")
                continue

            alert_report_type = f"{real_report_type}_FAILURE_ALERT"
            alert_key = build_idempotency_key(report_date_kst, alert_report_type, _ALERT_DESTINATION)
            if decide_delivery_action(alert_key, conn=conn) == "skip_duplicate":
                print(f"product={product_label} status=STILL_FAILED -- alert already sent today, not repeating.")
                continue

            due.append((alert_report_type, product_label))

        if not due:
            return EXIT_OK

        run_id = f"final-failure-alert-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
        runs_row_id = start_run(conn, run_id, report_date_kst, registry_hash=None)

        results = []
        for alert_report_type, product_label in due:
            print(f"product={product_label} status=FINAL_FAILURE -- sending one alert.")
            alert_status = "failed"
            try:
                send_memo(_ALERT_TEXT.format(product=product_label))
                alert_status = "sent"
                logger.info("report_date=%s product=%s FINAL_FAILURE alert sent.", report_date_kst, product_label)
            except (KakaoSendError, KakaoValidationError) as exc:
                # Journal remains authoritative -- an alert-send failure
                # (e.g. Kakao itself down) must never crash this script.
                logger.error(
                    "report_date=%s product=%s FINAL_FAILURE alert send FAILED (journal is authoritative): %s: %s",
                    report_date_kst, product_label, type(exc).__name__, exc,
                )

            record_delivery(
                runs_row_id, report_date_kst, alert_report_type, _ALERT_DESTINATION,
                content_hash=alert_report_type, status=alert_status, conn=conn,
            )
            conn.commit()
            results.append({
                "source_name": f"final_failure_alert_{product_label.lower()}", "category": "FAILURE_ALERT",
                "status": "SUCCESS" if alert_status == "sent" else "FAILED",
            })

        finalize_run(conn, runs_row_id, results)
    finally:
        conn.close()

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
