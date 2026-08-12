"""Manually-run Kakao delivery CLI for the latest persisted Report V1 output.

    .venv\\Scripts\\python.exe scripts\\deliver_daily_report.py

Never regenerates a report and never calls Anthropic -- reads whatever is
already persisted in `reports` for today's KST date (see
report_delivery.select_latest_reports) and sends it. Does not register any
scheduled/automatic execution (no Task Scheduler, no cron/systemd) --
manually-invoked only.

Exit code contract:
  0 = delivery status "sent" or "skipped_duplicate" (both are non-failure
      outcomes -- a duplicate-guard skip is working as intended, not an error)
  1 = delivery status "failed", or no report exists yet for today
      (NoReportAvailableError), or a global run-start failure (duplicate
      run_id, run_metadata failure)
  2 = CLI invocation error (argparse's own handling, e.g. an unknown flag)
"""

import argparse
import logging
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH
from db.database import connect, init_db
from ingestion.orchestrator import GlobalFailureError, finalize_run, start_run
from report_delivery import NoReportAvailableError, deliver_daily_report

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))


def _generate_run_id():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"daily-delivery-{timestamp}-{suffix}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Deliver SUPER NEWS's latest persisted daily report via Kakao.")
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH
    run_id = _generate_run_id()
    report_date_kst = datetime.now(_KST).strftime("%Y-%m-%d")

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        try:
            runs_row_id = start_run(conn, run_id, report_date_kst, registry_hash=None)
        except GlobalFailureError as exc:
            logger.error("run_id=%s global failure: %s", run_id, type(exc).__name__)
            print(f"Run failed before/without completing: {exc}")
            return EXIT_RUN_FAILURE

        try:
            result = deliver_daily_report(report_date_kst, runs_row_id, conn=conn)
            final_status = finalize_run(
                conn, runs_row_id, [{"source_name": "kakao_delivery", "category": "DAILY_DIGEST",
                                      "status": "SUCCESS" if result["status"] in ("sent", "skipped_duplicate") else "FAILED"}],
            )
        except NoReportAvailableError as exc:
            logger.error("run_id=%s no report available: %s", run_id, exc)
            finalize_run(
                conn, runs_row_id, [],
                override_status="failed", override_failure_stage="no_report_available",
            )
            print(f"No report available to deliver: {exc}")
            return EXIT_RUN_FAILURE
    finally:
        conn.close()

    print(f"run_id={run_id} report_date={report_date_kst} run_status={final_status}")
    print(
        f"  delivery_status={result['status']} sent={result['sent_count']}/{result['message_count']}"
    )
    if result["reason"]:
        print(f"  reason={result['reason']}")

    return EXIT_OK if result["status"] in ("sent", "skipped_duplicate") else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
