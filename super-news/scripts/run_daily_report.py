"""Manually-run daily report-generation CLI.

    .venv\\Scripts\\python.exe scripts\\run_daily_report.py

Wires argument parsing -> DB connection -> report.orchestrator invocation ->
result rendering -> exit code. Does not send anything to Kakao and does not
register any scheduled/automatic execution -- manually-invoked only.

Exit code contract:
  0 = run_daily_report() reported a "completed" run
  1 = the run reported "failed" (see run_category_status for per-category
      detail), or a global run-start failure (duplicate run_id,
      run_metadata failure)
  2 = CLI invocation error (argparse's own handling, e.g. an unknown flag)
"""

import argparse
import logging
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH
from db.database import connect, init_db
from ingestion.orchestrator import GlobalFailureError
from report.orchestrator import run_daily_report

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2


def _generate_run_id():
    """daily-report-<UTC timestamp>-<short suffix> -- distinct prefix from
    the news/music collection CLIs' run_ids so all three never collide in
    the shared runs.run_id UNIQUE space."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"daily-report-{timestamp}-{suffix}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Run SUPER NEWS's daily report generation.")
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

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        try:
            result = run_daily_report(conn, run_id)
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

    print(f"run_id={result['run_id']} status={result['status']}")
    for category, outcome in sorted(result["category_outcomes"].items()):
        print(f"  category={category} status={outcome['status']} report_id={outcome['report_id']}")

    return EXIT_OK if result["status"] == "completed" else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
