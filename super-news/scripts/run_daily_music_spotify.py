"""Manually-run daily Spotify collection CLI (chart + optional Web API
enrichment). Runs music.spotify_orchestrator.run_spotify_collection under
one daily run.

    .venv\\Scripts\\python.exe scripts\\run_daily_music_spotify.py

Fixed V1 scope: source=spotify_chart (GLOBAL, WEEKLY top 10, no auth) +
source=spotify_web (canonical metadata/ISRC enrichment, only runs if
SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are configured -- see
music/spotify_web.py). Does not register any scheduled/automatic execution
-- manually-invoked only, same as scripts/run_daily_music.py.

Exit code contract:
  0 = run_spotify_collection() reported a "completed" run
  1 = the run reported "failed", or a global run-start failure (duplicate
      run_id, run_metadata failure)
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
from music.spotify_orchestrator import run_spotify_collection

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILURE = 1
EXIT_CONFIG_ERROR = 2


def _generate_run_id():
    """daily-music-spotify-<UTC timestamp>-<short suffix> -- distinct
    prefix from the Apple/news CLIs' run_ids so they never collide in the
    shared runs.run_id UNIQUE space."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"daily-music-spotify-{timestamp}-{suffix}"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run SUPER NEWS's daily Spotify collection (chart + optional Web API enrichment)."
    )
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
            result = run_spotify_collection(conn, run_id)
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
            f"  source={source_result['source_name']} status={source_result['status']} "
            f"items_collected={source_result['items_collected']}"
        )
        if source_result["failure_reason"]:
            print(f"    reason={source_result['failure_reason']}")

    return EXIT_OK if result["status"] == "completed" else EXIT_RUN_FAILURE


if __name__ == "__main__":
    sys.exit(main())
