"""Music Trend Intelligence synthesis CLI -- runnable standalone for
manual/local use, mirroring scripts/run_daily_producer_intelligence.py:

    .venv\\Scripts\\python.exe scripts\\run_daily_music_trend_intelligence.py

Wires argument parsing -> DB connection -> report.music_trend_orchestrator
invocation -> result rendering -> exit code. Requires a news+music report
for the target date to already exist (report.web_data_v2.
build_dashboard_data_v2 is what supplies the evidence) -- run
scripts/run_daily_report.py first if invoking manually. Does not send
anything to Kakao. Not wired into scripts/run_daily_pipeline.sh yet (this
capability is new as of the MUSIC INTELLIGENCE COMPLETION phase and is
being run/reviewed manually first, same as Producer Intelligence's own
initial rollout).

Exit code contract:
  0 = "completed_no_evidence" (a legitimate empty day), "completed_with_
      signals", or "completed_reused"
  1 = "failed" (synthesis error or validation failure -- see the printed
      reason and the run's failure_stage in `runs`), or a global run-start
      failure (duplicate run_id, run_metadata failure)
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
from ingestion.orchestrator import GlobalFailureError
from report.music_trend_orchestrator import run_daily_music_trend_intelligence

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))


def _generate_run_id():
    """music-trend-intelligence-<UTC timestamp>-<short suffix> -- distinct
    prefix from every other CLI's run_ids so none collide in the shared
    runs.run_id UNIQUE space."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"music-trend-intelligence-{timestamp}-{suffix}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Run SUPER NEWS's daily Music Trend Intelligence synthesis.")
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db).",
    )
    parser.add_argument(
        "--report-date", type=str, default=None,
        help="KST report date YYYY-MM-DD to synthesize for (default: today, KST).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH
    report_date_kst = args.report_date or datetime.now(_KST).strftime("%Y-%m-%d")
    run_id = _generate_run_id()

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        try:
            result = run_daily_music_trend_intelligence(conn, run_id, report_date_kst)
        except GlobalFailureError as exc:
            logger.error("run_id=%s global failure: %s", run_id, type(exc).__name__)
            print(f"Run failed before/without completing: {exc}")
            return EXIT_RUN_FAILURE
        except Exception:
            logger.error("run_id=%s unexpected failure", run_id, exc_info=True)
            print("Run failed due to an unexpected error. See the log for details.")
            return EXIT_RUN_FAILURE
    finally:
        conn.close()

    print(f"run_id={result['run_id']} report_date={report_date_kst} status={result['status']}")
    if result["reason"]:
        print(f"  reason={result['reason']}")

    return EXIT_OK if result["status"] != "failed" else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
