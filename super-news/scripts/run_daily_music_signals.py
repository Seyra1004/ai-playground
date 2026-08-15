"""Manually-run daily derived-signal computation CLI.

Computes VELOCITY derived signals (music/derived_signals.py) for every
currently-active registered music source (music/registry.ACTIVE_MUSIC_
SOURCES) as one daily run. Must run AFTER all music collectors for the day
(Apple, Spotify) have already persisted their observations -- this script
never fetches from any external API itself, it only reads/writes
music_observations/derived_signals.

    .venv\\Scripts\\python.exe scripts\\run_daily_music_signals.py

Does not register any scheduled/automatic execution -- manually-invoked
only, same as the other daily music CLIs. Downstream consumers (Early
Signal, Catalog Revival, Cross-Platform, Forecast Gate -- see
report/web_data_v2.py) compute directly from whatever this script has
already persisted; they are not run or persisted here, since they're cheap
deterministic reads over already-persisted data, not their own collection
stage (same pattern music.signal_engine.compute_chart_diff already uses).

Exit code contract:
  0 = every active source's VELOCITY computation succeeded (or the run
      degraded but at least one source succeeded -- see
      ingestion.orchestrator._aggregate_run_status)
  1 = every active source's computation failed, or a global run-start
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
from ingestion.orchestrator import GlobalFailureError, finalize_run, start_run
from ingestion.persistence import record_run_source_status
from music.derived_signals import compute_velocity_signals
from music.registry import ACTIVE_MUSIC_SOURCES

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))


def _generate_run_id():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"daily-music-signals-{timestamp}-{secrets.token_hex(3)}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Compute daily VELOCITY derived signals for all active music sources."
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db).",
    )
    parser.add_argument(
        "--report-date", type=str, default=None,
        help="KST calendar date YYYY-MM-DD to compute signals for (default: today KST).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH
    report_date = args.report_date or datetime.now(_KST).strftime("%Y-%m-%d")
    run_id = _generate_run_id()

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        try:
            runs_row_id = start_run(conn, run_id, report_date, registry_hash=None)
        except GlobalFailureError as exc:
            logger.error("run_id=%s global failure: %s", run_id, type(exc).__name__)
            print(f"Run failed before/without completing: {exc}")
            return EXIT_RUN_FAILURE

        source_results = []
        for source_name in ACTIVE_MUSIC_SOURCES:
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                written = compute_velocity_signals(conn, report_date, source_name)
                status, failure_reason = "SUCCESS", None
            except Exception as exc:
                written, status, failure_reason = 0, "FAILED", f"{type(exc).__name__}: {exc}"
            finished_at = datetime.now(timezone.utc).isoformat()

            record_run_source_status(
                conn, run_id=runs_row_id, category="MUSIC", source_name=f"{source_name}_signals",
                status=status, started_at=started_at, finished_at=finished_at,
                items_collected=written, retry_count=0, failure_reason=failure_reason,
            )
            conn.commit()
            source_results.append({
                "source_name": f"{source_name}_signals", "category": "MUSIC",
                "status": status, "items_collected": written, "failure_reason": failure_reason,
            })
            logger.info(
                "derived_signals source=%s status=%s written=%d", source_name, status, written,
            )

        final_status = finalize_run(conn, runs_row_id, source_results)
    finally:
        conn.close()

    print(f"run_id={run_id} status={final_status}")
    for result in source_results:
        print(f"  source={result['source_name']} status={result['status']} items_collected={result['items_collected']}")
        if result["failure_reason"]:
            print(f"    reason={result['failure_reason']}")

    return EXIT_OK if final_status == "completed" else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
