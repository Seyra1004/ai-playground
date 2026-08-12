"""Generates the static HTML dashboard from already-persisted Report V1
output.

    .venv\\Scripts\\python.exe scripts\\generate_daily_web_report.py

Reuses report.web_data.build_dashboard_data() (read-only, structured) and
report.web_render.render_dashboard_html() (presentation-only, no LLM call,
no second content path). Writes docs/index.html (latest) and
docs/reports/<date>.html (archive) at the REPO ROOT -- a sibling of
super-news/, not inside it -- since GitHub Pages serves docs/ from `main`.

Never sends anything, never touches delivery_history or run_category_status
(read-only against them), never commits or pushes -- this only writes local
files. Publishing (commit + push) is a separate, manual step for V1.

build_dashboard_data() never raises for "nothing persisted yet" -- it
returns an honestly all-DEGRADED dashboard instead (see Decision D), so
this CLI always writes a page and exits 0 as long as the DB itself is
reachable. Any other failure is reported generically, without echoing an
internal stack trace.

Exit code contract:
  0 = dashboard generated and both files written (including an
      all-DEGRADED dashboard when nothing has been persisted yet)
  1 = unexpected failure (e.g. DB unreachable)
  2 = CLI invocation error (argparse's own handling)
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH
from db.database import connect
from report.web_data import build_dashboard_data
from report.web_render import render_dashboard_html

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_UNEXPECTED_ERROR = 1
EXIT_CONFIG_ERROR = 2

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

# docs/ lives at the repo root (super-news/scripts/../../docs), a sibling
# of super-news/ -- NOT inside it.
_DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Generate the static SUPER NEWS web dashboard from already-persisted data."
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db).",
    )
    parser.add_argument(
        "--report-date", type=str, default=None,
        help="KST report date YYYY-MM-DD to render (default: today, KST).",
    )
    parser.add_argument(
        "--docs-dir", type=Path, default=None,
        help="Override the docs/ output directory (default: the repo root's docs/). "
             "Intended for test isolation -- production runs should never override this.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH
    report_date_kst = args.report_date or datetime.now(_KST).strftime("%Y-%m-%d")
    docs_dir = args.docs_dir if args.docs_dir is not None else _DOCS_DIR

    try:
        conn = connect(db_path=db_path)
        try:
            dashboard_data = build_dashboard_data(conn, report_date_kst)
        finally:
            conn.close()

        html_content = render_dashboard_html(dashboard_data)

        archive_dir = docs_dir / "reports"
        archive_dir.mkdir(parents=True, exist_ok=True)
        index_path = docs_dir / "index.html"
        archive_path = archive_dir / f"{report_date_kst}.html"
        index_path.write_text(html_content, encoding="utf-8")
        archive_path.write_text(html_content, encoding="utf-8")
    except Exception:
        logger.error("Web dashboard generation failed unexpectedly.", exc_info=True)
        print("Web dashboard generation failed due to an unexpected error. See the log for details.")
        return EXIT_UNEXPECTED_ERROR

    print(f"report_date={report_date_kst}")
    print(f"wrote {index_path}")
    print(f"wrote {archive_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
