"""Manually-run daily ingestion CLI (Phase 2B). Runs every enabled source
in sources.yaml through Phase 2A's ingestion pipeline under one daily run.

    .venv\\Scripts\\python.exe scripts\\run_daily_ingestion.py

Does NOT register any scheduled/automatic execution (no Task Scheduler, no
cron) — that is a separate, later phase. This is a manually-invoked
command only.

Exit code contract:
  0 = orchestration completed (all sources succeeded, or a degraded-but-
      usable mix of SUCCESS/PARTIAL/FAILED — see run_source_status for
      the per-source detail)
  1 = global failure (registry valid but no enabled sources, duplicate
      run_id, DB start/finalize failure) OR every enabled source failed
  2 = configuration/invocation error (registry failed to load/validate)
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
from ingestion.orchestrator import GlobalFailureError, run_daily_ingestion
from ingestion.registry import SourceRegistryError, load_source_registry

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "sources.yaml"

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2


def _generate_run_id():
    """daily-ingestion-<UTC timestamp>-<short collision suffix> — no uuid
    framework needed for this; the timestamp alone is already almost
    certainly unique for a once-daily job, and the short suffix (stdlib
    `secrets`, not a new dependency) closes the gap for manual re-runs
    within the same second."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"daily-ingestion-{timestamp}-{suffix}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run SUPER NEWS daily ingestion across all enabled registered sources."
    )
    parser.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY_PATH,
        help="Path to sources.yaml (default: project root sources.yaml).",
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Business run_id to use (default: auto-generated daily-ingestion-<UTC timestamp>-<suffix>).",
    )
    parser.add_argument(
        "--validate-config", action="store_true",
        help="Validate the registry only. No network calls, no DB writes. Exits 0 if valid, 2 if not.",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db). "
             "Intended for development/manual verification against a scratch DB.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    try:
        registry = load_source_registry(args.registry)
    except SourceRegistryError as exc:
        print(f"Configuration error: {exc}")
        return EXIT_CONFIG_ERROR

    if args.validate_config:
        print(f"Registry OK: {len(registry)} source(s) defined.")
        return EXIT_OK

    db_path = args.db_path if args.db_path is not None else DB_PATH
    run_id = args.run_id or _generate_run_id()

    init_db(db_path=db_path)
    conn = connect(db_path=db_path)
    try:
        try:
            result = run_daily_ingestion(conn, registry, run_id)
        except GlobalFailureError as exc:
            logger.error("run_id=%s global failure: %s", run_id, type(exc).__name__)
            print(f"Run failed before/without completing: {exc}")
            return EXIT_RUN_FAILURE
        except Exception as exc:
            logger.error("run_id=%s unexpected failure: %s", run_id, type(exc).__name__)
            print("Run failed due to an unexpected error. See the log for details.")
            return EXIT_RUN_FAILURE
    finally:
        conn.close()

    print(f"run_id={result['run_id']} status={result['status']}")
    for source_result in result["source_results"]:
        print(
            f"  source={source_result['source_name']} category={source_result['category']} "
            f"status={source_result['status']} items_collected={source_result['items_collected']}"
        )

    return EXIT_OK if result["status"] == "completed" else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
