"""Generates the static V2.1 Intelligence Dashboard HTML from already-
persisted daily data (news, music charts, cross-platform entity
resolution, Producer Intelligence).

    .venv\\Scripts\\python.exe scripts\\generate_daily_web_report_v2.py

Reuses report.web_data_v2.build_dashboard_data_v2() (read-only, structured
-- reads Producer Intelligence and cross-platform results that Stage 3b/
the music pipeline already persisted; computes no NEW news/music selection
or synthesis here) and report.web_render_v2.render_dashboard_html_v2()
(presentation-only, no LLM call, no second content path, no DB access).
This script only orchestrates those two calls plus file I/O -- it never
reimplements news selection, music calculations, entity resolution,
cross-platform logic, or Producer Intelligence.

COST NOTE (corrects a prior stale claim here): build_dashboard_data_v2()
DOES call report.translation.translate_and_cache() for non-Korean titles/
snippets, which -- only when TRANSLATION_PROVIDER=anthropic and a real
ANTHROPIC_API_KEY are both configured in the environment -- makes a real
paid Anthropic API call for any text not already cached. This is a
legitimate cached, additive translation feature (see report/translation.py
module docstring), not a bug, but it means this script is NOT
unconditionally free to run. Set SUPER_NEWS_NO_PAID_API=1 in the process
environment to force NullTranslationProvider and guarantee zero outbound
API calls for a given run, regardless of TRANSLATION_PROVIDER.

Writes docs/v2/index.html (latest) and docs/v2/reports/<date>.html
(archive) -- a namespace SEPARATE from V1's docs/index.html / docs/
reports/<date>.html, so V1 and V2.1 stay independently deployable and
distinguishable. V1's own generator (scripts/generate_daily_web_report.py)
is untouched by this file's existence; this script never writes to V1's
paths and never redirects them.

Writes are atomic: the full HTML string is rendered in memory first (a
render failure writes nothing at all), then each destination file is
written to a temp file in the SAME directory and swapped into place via
Path.replace() (atomic on both POSIX and Windows) -- a crash mid-write can
never leave a half-written page overwriting a previously good one.

Never sends anything, never touches delivery_history (read-only against
already-persisted tables), never commits or pushes -- this only writes
local files. Publishing (commit + push) is a separate, manual step, same
as V1.

build_dashboard_data_v2() never raises for "nothing persisted yet" -- it
returns honest DEGRADED/UNAVAILABLE/empty states instead, so this CLI
always writes a page and exits 0 as long as the DB itself is reachable.

Exit code contract:
  0 = dashboard generated and both files written
  1 = unexpected failure (e.g. DB unreachable, render error) -- nothing is
      written or replaced in this case, and any previously valid page is
      left exactly as it was
  2 = CLI invocation error (argparse's own handling)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# PERMANENT ZERO-PAYG SAFETY: forced BEFORE any other import, same pattern
# as scripts/run_daily_full_pipeline_v2.py's own cost guard. A real PAYG
# violation happened once because this script was run manually without the
# operator remembering to set this -- safety must never depend on that.
# report.translation.build_translation_provider() never imports/constructs
# report.translation_anthropic.AnthropicTranslationProvider while this is
# set, regardless of TRANSLATION_PROVIDER/ANTHROPIC_API_KEY; an existing,
# already-paid-for translation_cache entry can still be reused (zero cost),
# see build_translation_provider()'s own docstring.
os.environ["SUPER_NEWS_NO_PAID_API"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH
from db.database import connect
from report.source_metadata import SourceMetadataValidationError, validate_active_source_metadata
from report.web_data_v2 import build_dashboard_data_v2
from report.web_render_v2 import render_daily_page_html_v2, render_dashboard_html_v2, render_music_page_html_v2

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_UNEXPECTED_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_SOURCE_METADATA_ERROR = 3

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

# docs/v2/ lives at the repo root (super-news/scripts/../../docs/v2), a
# sibling of super-news/ and a namespace SEPARATE from V1's docs/ root --
# never the same files, never redirected.
_DOCS_V2_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "v2"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Generate the static SUPER NEWS V2.1 Intelligence Dashboard from already-persisted data."
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
        help="Override the docs/v2/ output directory (default: the repo root's docs/v2/). "
             "Intended for test isolation -- production runs should never override this.",
    )
    return parser.parse_args(argv)


def _atomic_write_text(path, content):
    """Writes `content` to `path` atomically: a temp file in the SAME
    directory (required so the final replace is on the same filesystem/
    volume, which is what makes it atomic) is written and fsync'd, then
    swapped into place -- a crash mid-write can never leave `path`
    half-written, and nothing else can ever observe a partial file at
    `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(path)


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH
    report_date_kst = args.report_date or datetime.now(_KST).strftime("%Y-%m-%d")
    docs_dir = args.docs_dir if args.docs_dir is not None else _DOCS_V2_DIR

    # Production FAIL gate (credential-independent architecture audit):
    # every ENABLED sources.yaml/music.registry source must have a real
    # display_name and quality_tier before this script ever reads the DB
    # or writes a page -- a missing one would mean a raw internal adapter
    # key silently reaching a real reader. Checked once, loudly, here;
    # never silently defaulted the way the dev/test-friendly SourceConfig
    # loader is for a directly-constructed fixture.
    try:
        validate_active_source_metadata()
    except SourceMetadataValidationError as exc:
        logger.error("Active source metadata validation FAILED: %s", exc)
        print(f"V2.1 web dashboard generation refused: {exc}")
        return EXIT_SOURCE_METADATA_ERROR

    try:
        conn = connect(db_path=db_path)
        try:
            dashboard_data = build_dashboard_data_v2(conn, report_date_kst)
        finally:
            conn.close()

        html_content = render_dashboard_html_v2(dashboard_data)

        index_path = docs_dir / "index.html"
        archive_path = docs_dir / "reports" / f"{report_date_kst}.html"
        _atomic_write_text(index_path, html_content)
        _atomic_write_text(archive_path, html_content)

        # REFERENCE DESIGN PRODUCT SPLIT: SUPER NEWS MUSIC and SUPER NEWS
        # DAILY are two genuinely separate product pages (see report.
        # web_render_v2's module docstring) -- written ADDITIONALLY here,
        # alongside the existing combined index.html/archive above, which
        # stay unchanged so report.release_v2's existing docs/v2/index.html
        # release-gate contract is untouched.
        music_path = docs_dir / "music.html"
        daily_path = docs_dir / "daily.html"
        _atomic_write_text(music_path, render_music_page_html_v2(dashboard_data))
        _atomic_write_text(daily_path, render_daily_page_html_v2(dashboard_data))
    except Exception:
        logger.error("V2.1 web dashboard generation failed unexpectedly.", exc_info=True)
        print("V2.1 web dashboard generation failed due to an unexpected error. See the log for details.")
        return EXIT_UNEXPECTED_ERROR

    print(f"report_date={report_date_kst}")
    print(f"wrote {index_path}")
    print(f"wrote {archive_path}")
    print(f"wrote {music_path}")
    print(f"wrote {daily_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
