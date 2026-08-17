"""SUPER NEWS single daily production Kakao delivery entrypoint --
sends SUPER NEWS MUSIC and SUPER NEWS DAILY as two independent messages
for the given KST report date.

    .venv\\Scripts\\python.exe scripts\\run_daily_kakao_delivery_v2.py [--report-date YYYY-MM-DD] [--db-path PATH]

Reads whatever is already real and persisted (report.web_data_v2.
build_dashboard_data_v2, built ONCE and reused for both products) --
never regenerates a report, never re-runs synthesis. build_dashboard_data_v2
DOES translate non-Korean titles via report.translation.translate_and_cache;
that is a real, cached, additive feature -- set SUPER_NEWS_NO_PAID_API=1 in
the process environment for a dry run that guarantees zero outbound API
calls (see report/translation.py's build_translation_provider()).

MUSIC and DAILY are independent products: each has its own idempotency key
(report_delivery_v2.MUSIC_REPORT_TYPE / DAILY_REPORT_TYPE), so a failure or
duplicate-skip of one never blocks or is affected by the other. Re-running
this script for a date that already sent both is always a safe, cheap
no-op (both skip as duplicates, exit 0).

Exit code contract:
  0 = both MUSIC and DAILY ended in a non-failure state ("sent" or
      "skipped_duplicate")
  1 = either product ended "failed", or a global run-start failure
      (duplicate run_id, run_metadata failure). NoDashboardDataError for a
      product is treated as that product's own non-fatal "failed"-style
      outcome here (reported, not raised past main()) so one product
      having no real content yet doesn't prevent the other from sending.
  2 = CLI invocation error (argparse's own handling)
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
from report.web_data_v2 import build_dashboard_data_v2
from report_delivery_v2 import (
    NoDashboardDataError,
    deliver_daily_digest_v2,
    deliver_music_digest_v2,
)

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))


def _generate_run_id():
    """daily-kakao-delivery-v2-<UTC timestamp>-<short suffix> -- distinct
    prefix from every other delivery CLI's own run_ids so the two never
    collide in the shared runs.run_id UNIQUE space."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"daily-kakao-delivery-v2-{timestamp}-{suffix}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Send SUPER NEWS MUSIC and SUPER NEWS DAILY as two independent Kakao messages."
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db).",
    )
    parser.add_argument(
        "--report-date", type=str, default=None,
        help="KST report date YYYY-MM-DD to deliver for (default: today, KST).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Build dashboard data and render both digest texts, but never call "
            "send_memo (no real Kakao network call) and never touch "
            "delivery_history/runs (no idempotency-state mutation). Does NOT by "
            "itself guarantee zero paid API calls -- set SUPER_NEWS_NO_PAID_API=1 "
            "in the process environment for that (see report/translation.py)."
        ),
    )
    return parser.parse_args(argv)


def _run_one_product(label, deliver_fn, report_date_kst, runs_row_id, conn, dashboard_data_v2):
    try:
        result = deliver_fn(report_date_kst, runs_row_id, conn=conn, dashboard_data_v2=dashboard_data_v2)
    except NoDashboardDataError as exc:
        logger.error("report_date=%s product=%s no dashboard content available: %s", report_date_kst, label, exc)
        return {"status": "failed", "reason": f"NoDashboardDataError: {exc}"}
    return result


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH
    run_id = _generate_run_id()
    report_date_kst = args.report_date or datetime.now(_KST).strftime("%Y-%m-%d")

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)

    if args.dry_run:
        # No runs/delivery_history writes at all -- a dry run must be a
        # pure read + render, safe to run any number of times with no
        # effect on real idempotency state. Imported here (not module
        # top-level) since it's dry-run-only.
        from report.kakao_render_v2 import render_daily_kakao_digest, render_music_kakao_digest

        try:
            dashboard_data_v2 = build_dashboard_data_v2(conn, report_date_kst)
        finally:
            conn.close()

        print(f"DRY_RUN=true report_date={report_date_kst}")
        try:
            music_text = render_music_kakao_digest(dashboard_data_v2)
            print(f"MUSIC_TEXT_LENGTH={len(music_text)}")
            print("--- MUSIC ---")
            print(music_text)
        except Exception as exc:
            print(f"MUSIC_RENDER_FAILED={type(exc).__name__}: {exc}")
        try:
            daily_text = render_daily_kakao_digest(dashboard_data_v2)
            print(f"DAILY_TEXT_LENGTH={len(daily_text)}")
            print("--- DAILY ---")
            print(daily_text)
        except Exception as exc:
            print(f"DAILY_RENDER_FAILED={type(exc).__name__}: {exc}")
        print("DRY_RUN_NOTE=no Kakao network call made, no delivery_history/runs rows written")
        return EXIT_OK

    try:
        try:
            runs_row_id = start_run(conn, run_id, report_date_kst, registry_hash=None)
        except GlobalFailureError as exc:
            logger.error("run_id=%s global failure: %s", run_id, type(exc).__name__)
            print(f"Run failed before/without completing: {exc}")
            return EXIT_RUN_FAILURE

        dashboard_data_v2 = build_dashboard_data_v2(conn, report_date_kst)

        music_result = _run_one_product(
            "MUSIC", deliver_music_digest_v2, report_date_kst, runs_row_id, conn, dashboard_data_v2
        )
        daily_result = _run_one_product(
            "DAILY", deliver_daily_digest_v2, report_date_kst, runs_row_id, conn, dashboard_data_v2
        )

        both_ok = music_result["status"] in ("sent", "skipped_duplicate") and daily_result["status"] in (
            "sent", "skipped_duplicate",
        )
        final_status = finalize_run(
            conn, runs_row_id,
            [
                {"source_name": "kakao_delivery_music_v2", "category": "MUSIC_DIGEST_V2",
                 "status": "SUCCESS" if music_result["status"] in ("sent", "skipped_duplicate") else "FAILED"},
                {"source_name": "kakao_delivery_daily_v2", "category": "DAILY_DIGEST_V2",
                 "status": "SUCCESS" if daily_result["status"] in ("sent", "skipped_duplicate") else "FAILED"},
            ],
        )
    finally:
        conn.close()

    print(f"run_id={run_id} report_date={report_date_kst} run_status={final_status}")
    print(f"  MUSIC_STATUS={music_result['status']}" + (f" reason={music_result['reason']}" if music_result["reason"] else ""))
    print(f"  DAILY_STATUS={daily_result['status']}" + (f" reason={daily_result['reason']}" if daily_result["reason"] else ""))

    return EXIT_OK if both_ok else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
