"""SUPER NEWS single UNATTENDED daily production entrypoint -- the ONE
Windows Task Scheduler target for full-chain automation:

    fresh news ingestion (+ normalization/dedup -- ingestion.orchestrator's
    own required stage, see ingestion/orchestrator.py's normalize_batch call)
    -> Apple Music KR collection
    -> Spotify collection (chart + optional Web API enrichment)
    -> derived VELOCITY signal computation
    -> Report Intelligence (news selection/synthesis)
    -> Producer Intelligence
    -> News Intelligence
    -> Music Trend Intelligence
    -> SUPER NEWS MUSIC Kakao delivery
    -> SUPER NEWS DAILY Kakao delivery

    .venv\\Scripts\\python.exe scripts\\run_daily_full_pipeline_v2.py [--db-path PATH] [--dry-run]

COST SAFETY (read before touching this file): SUPER_NEWS_NO_PAID_API=1 is
forced into this process's own environment UNCONDITIONALLY, before any
other import, at module load time -- see the assignment immediately below
the module docstring. Every stage below runs as a subprocess that inherits
this process's environment, so this guarantees
report.translation.build_translation_provider() never returns a real
AnthropicTranslationProvider anywhere in this chain, regardless of what
TRANSLATION_PROVIDER/.env says, and regardless of whether Task Scheduler's
own launch environment ever set it. LLM_PROVIDER=claude_cli is likewise
forced into this process's own environment UNCONDITIONALLY -- every LLM
call in the four intelligence stages below goes through
report.llm_claude_cli.ClaudeCLIStructuredLLM (the already-authenticated
Claude Code subscription CLI in non-interactive print mode, ANTHROPIC_API_KEY
stripped from its own child environment), never report.llm_anthropic's
direct api.anthropic.com/PAYG path -- report.llm_interface.build_llm()
additionally refuses outright to construct the paid Anthropic client at all
while SUPER_NEWS_NO_PAID_API is set, regardless of LLM_PROVIDER, so there is
no path from this script to a real paid API call. A subscription-CLI
failure/timeout/rate-limit in any one intelligence stage never falls back to
the paid API and never aborts the chain -- see the graceful-degradation note
below, matching every other stage here.

LLM-based news selection/synthesis (scripts/run_daily_report.py,
scripts/run_daily_producer_intelligence.py,
scripts/run_daily_news_intelligence.py,
scripts/run_daily_music_trend_intelligence.py) now run as four ordinary
stages in this chain, after derived signals and before Kakao delivery, using
the same already-tested, unmodified CLI scripts as every other stage (no
second competing pipeline implementation). MUSIC and DAILY Kakao delivery
both still have a real, tested, no-LLM raw-fallback path (report.
candidate_selection's raw fallback over normalized_items/music_observations)
that reads directly from freshly-ingested data for whichever categories a
given day's intelligence stage didn't produce a curated report for (a
category with zero candidates, a validation rejection, or a genuine
subscription-CLI failure) -- so a fresh daily send is always real and
non-fabricated, with LLM-curated synthesis layered on top whenever an
intelligence stage actually succeeds for that category.

Each upstream collection stage runs via subprocess against THIS process's
own sys.executable (whatever python launched this script -- the project's
own .venv python when run the intended way) -- reuses the already-tested,
unmodified CLI scripts unchanged, no second competing pipeline
implementation. A failure in any upstream stage is logged (structured,
one SUCCESS/FAILED line per stage) but never aborts the chain -- matches
scripts/run_daily_pipeline.sh's own long-standing non-`set -e` philosophy
(one source's hiccup must not cancel today's real delivery; MUSIC/DAILY
delivery already degrades gracefully around missing/partial upstream data
via NoDashboardDataError).

--dry-run threads ONLY into the final Kakao delivery stage
(run_daily_kakao_delivery_v2.py --dry-run): every upstream stage
(fresh ingestion included) still runs for real -- ingestion/collection are
free, side-effect-safe, already-idempotent reads/writes, and a dry run
that skipped them would not actually prove "fresh data", which is the
whole point of this script's existence. Only the final Kakao
send + delivery_history/runs write is suppressed.

Exit code contract:
  0 = the final MUSIC+DAILY Kakao delivery stage exited 0 (both ended
      "sent"/"skipped_duplicate", or a clean dry run)
  1 = the final delivery stage exited non-zero (see
      run_daily_kakao_delivery_v2.py's own exit contract), regardless of
      any upstream stage's own result (upstream failures are logged, not
      fatal -- see the graceful-degradation note above)
  2 = CLI invocation error (argparse's own handling)
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Forced BEFORE any other import in this file -- see the module docstring's
# COST SAFETY note. Must win even if the parent process (Task Scheduler, a
# human shell) never set it.
os.environ["SUPER_NEWS_NO_PAID_API"] = "1"
os.environ["LLM_PROVIDER"] = "claude_cli"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import DB_PATH

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_DELIVERY_FAILURE = 1
EXIT_CONFIG_ERROR = 2

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"

# Order matters: signals must run after both collectors; both collectors
# and ingestion have no ordering dependency on each other but are run
# sequentially anyway (matches scripts/run_daily_pipeline.sh's own order).
_UPSTREAM_STAGES = (
    ("ingestion", "run_daily_ingestion.py"),
    ("apple_music", "run_daily_music.py"),
    ("spotify_music", "run_daily_music_spotify.py"),
    ("derived_signals", "run_daily_music_signals.py"),
)

# Runs after upstream collection, before Kakao delivery -- each stage calls
# report.llm_interface.build_llm() internally, which resolves to the
# subscription Claude CLI provider (LLM_PROVIDER=claude_cli, forced above)
# and refuses outright to construct a paid Anthropic client while
# SUPER_NEWS_NO_PAID_API=1 (also forced above). A failure in any one stage
# (CLI timeout/rate-limit, content validation rejection, ...) is logged and
# never aborts the chain -- the next stage still runs, and Kakao delivery's
# existing raw-fallback path (see report/web_data_v2.py's
# _raw_fallback_items) covers whatever category didn't get a curated
# report today, exactly like every other upstream stage's failure handling
# in this script.
_INTELLIGENCE_STAGES = (
    ("report_intelligence", "run_daily_report.py"),
    ("producer_intelligence", "run_daily_producer_intelligence.py"),
    ("news_intelligence", "run_daily_news_intelligence.py"),
    ("music_trend_intelligence", "run_daily_music_trend_intelligence.py"),
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description=(
            "SUPER NEWS full unattended daily chain: fresh ingestion -> music "
            "collection -> derived signals -> SUPER NEWS MUSIC + DAILY Kakao delivery."
        )
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the SQLite DB path (default: the project's configured data/super_news.db).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run every real upstream collection stage, but only render (never send) the "
             "final Kakao digests and never touch delivery_history/runs.",
    )
    return parser.parse_args(argv)


def _run_stage(label, script_name, db_path, extra_args=()):
    script_path = _SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script_path), "--db-path", str(db_path), *extra_args]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    logger.info("stage=%s STARTING cmd=%s", label, " ".join(cmd))
    started = time.monotonic()
    # Raw bytes, NOT text=True: the child (PYTHONIOENCODING=utf-8 above)
    # always encodes its own stdout/stderr as UTF-8. Relaying those bytes
    # straight through this process's own stdout/stderr buffer (rather than
    # decoding to str and letting Python re-encode via whatever codepage
    # THIS process's console/redirected-file happens to be using) is the
    # only way real Korean-language digest content survives intact through
    # a Windows console/log redirect regardless of the active codepage.
    result = subprocess.run(cmd, cwd=str(_PROJECT_ROOT), env=env, capture_output=True)
    elapsed = time.monotonic() - started

    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
        if not result.stdout.endswith(b"\n"):
            sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)
        if not result.stderr.endswith(b"\n"):
            sys.stderr.buffer.write(b"\n")
        sys.stderr.buffer.flush()

    status = "SUCCESS" if result.returncode == 0 else "FAILED"
    logger.info("stage=%s %s exit=%s elapsed=%.1fs", label, status, result.returncode, elapsed)
    print(f"STAGE_RESULT stage={label} status={status} exit={result.returncode} elapsed={elapsed:.1f}s")
    return result.returncode


def main(argv=None):
    logging_setup.setup_logging()
    args = _parse_args(argv)

    db_path = args.db_path if args.db_path is not None else DB_PATH

    print(f"=== SUPER NEWS full daily pipeline START dry_run={args.dry_run} db_path={db_path} ===")
    logger.info(
        "=== SUPER NEWS full daily pipeline START dry_run=%s db_path=%s SUPER_NEWS_NO_PAID_API=%s ===",
        args.dry_run, db_path, os.environ.get("SUPER_NEWS_NO_PAID_API"),
    )

    any_upstream_failure = False
    for label, script_name in _UPSTREAM_STAGES:
        exit_code = _run_stage(label, script_name, db_path)
        if exit_code != 0:
            any_upstream_failure = True

    for label, script_name in _INTELLIGENCE_STAGES:
        exit_code = _run_stage(label, script_name, db_path)
        if exit_code != 0:
            any_upstream_failure = True

    delivery_extra = ["--dry-run"] if args.dry_run else []
    delivery_exit = _run_stage(
        "kakao_delivery", "run_daily_kakao_delivery_v2.py", db_path, extra_args=delivery_extra,
    )

    print(
        f"=== SUPER NEWS full daily pipeline SUMMARY "
        f"any_upstream_failure={any_upstream_failure} delivery_exit={delivery_exit} ==="
    )
    logger.info(
        "=== SUPER NEWS full daily pipeline END any_upstream_failure=%s delivery_exit=%s ===",
        any_upstream_failure, delivery_exit,
    )

    return EXIT_OK if delivery_exit == 0 else EXIT_DELIVERY_FAILURE


if __name__ == "__main__":
    sys.exit(main())
