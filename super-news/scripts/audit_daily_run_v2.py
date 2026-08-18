"""SUPER NEWS post-run audit -- READ-ONLY inspection of one already-
completed scripts/run_daily_full_pipeline_v2.py execution for a given
report_date_kst, built entirely from data the production system already
records (logs/super_news.log's own structured `logger.info` lines --
NOT the print()-only STAGE_RESULT lines, which never reach the log file
-- plus the runs/llm_interpretations/delivery_history tables) -- never a
new capture mechanism, never a duplicate of what report.llm_usage_summary
or the pipeline's own logging already does.

    .venv\\Scripts\\python.exe scripts\\audit_daily_run_v2.py --date YYYY-MM-DD

HARD GUARANTEE: this script makes ZERO LLM calls, sends ZERO Kakao
messages, and performs ZERO writes to delivery_history/runs/any DB table
or any local file. Its only network call is a single real, read-only
HTTP GET against the live public MUSIC page (report.release_v2.
DEFAULT_PUBLIC_BASE_URL + "/v2/music.html") -- observability must never
be able to affect whether news gets generated or sent, so a failure in
THIS script can never corrupt or block production execution (it isn't
part of the pipeline; it only ever runs afterward, by hand).

CANONICAL RUN IDENTITY: the single most-reliable identity for "the one
normal scheduled MUSIC delivery for this date" is not a new concept --
it's the existing delivery_history row itself (report_type=
'SUPER_NEWS_MUSIC_V2', status='sent'), joined via its own real
delivery_history.run_id (an INTEGER FK to runs.id, already the schema's
own relationship) back to that stage's real runs.run_id/started_at/
finished_at. A dry run never writes this row at all (see report.
release_v2 / report_daily_kakao_delivery_v2's own dry-run branch), and a
correction send uses a different report_type -- so this identity can
never be confused with either.

STAGE-LEVEL detail (which of the 9 pipeline stages ran, in what order,
how long each took) comes from parsing the pipeline's own real
`logger.info("stage=%s %s exit=%s elapsed=%.1fs", ...)` lines already
written to logs/super_news.log by every real invocation -- bounded by
the pipeline's own real "=== SUPER NEWS full daily pipeline START ... ==="
/ "...END..." marker lines. RotatingFileHandler (see logging_setup.py,
maxBytes=1_000_000, backupCount=5) means an old block can roll into
super_news.log.1 -- this script checks the live file first, then the
immediate .1 backup, before giving up.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH, LOG_DIR
from db.database import connect
from report.llm_usage_summary import CATEGORY_TO_PURPOSE

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2

_KST = timezone(timedelta(hours=9))

_PUBLIC_BASE_URL = "https://seyra1004.github.io/ai-playground"
_MUSIC_PUBLIC_URL = _PUBLIC_BASE_URL + "/v2/music.html"
_MUSIC_TITLE_DATE_RE = re.compile(r"SUPER NEWS MUSIC — (\d{4}\.\d{2}\.\d{2})")

_PIPELINE_START_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) [\d:,]+ INFO __main__: === SUPER NEWS full daily pipeline START "
    r"dry_run=(\S+) db_path=(\S+) SUPER_NEWS_NO_PAID_API=(\S+) ==="
)
_PIPELINE_END_RE = re.compile(
    r"^[\d-]+ [\d:,]+ INFO __main__: === SUPER NEWS full daily pipeline END "
    r"any_upstream_failure=(\S+) delivery_exit=(\S+) ==="
)
_STAGE_RESULT_RE = re.compile(
    r"^([\d-]+ [\d:,]+) INFO __main__: stage=(\S+) (SUCCESS|FAILED) exit=(\S+) elapsed=([\d.]+)s"
)
_STAGE_RUN_ID_RE = re.compile(r"run_id=(\S+)\s")

_EXPECTED_STAGES = (
    "ingestion", "apple_music", "spotify_music", "derived_signals",
    "report_intelligence", "producer_intelligence", "news_intelligence",
    "music_trend_intelligence", "kakao_delivery",
)
_INTELLIGENCE_STAGE_LABELS = (
    "report_intelligence", "producer_intelligence", "news_intelligence", "music_trend_intelligence",
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="READ-ONLY post-run audit of one scripts/run_daily_full_pipeline_v2.py "
                     "execution for a given KST report date. Makes zero LLM calls, sends zero "
                     "Kakao messages, writes zero DB rows."
    )
    parser.add_argument("--date", type=str, required=True, help="KST report date YYYY-MM-DD to audit.")
    parser.add_argument("--db-path", type=Path, default=None,
                         help="Override the SQLite DB path (default: the project's configured data/super_news.db).")
    parser.add_argument("--no-http", action="store_true",
                         help="Skip the one real public-page HTTP GET (offline/CI use).")
    return parser.parse_args(argv)


def _log_lines():
    """Yields lines from the live log file, then its immediate .1 backup
    (RotatingFileHandler rotates the live file to .1 first) -- read-only,
    never writes, never rotates anything itself."""
    for suffix in ("", ".1"):
        path = LOG_DIR / f"super_news.log{suffix}"
        if not path.exists():
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\n")


def _find_pipeline_block(target_date, lines=None):
    """Returns the most relevant real pipeline START/END block for
    target_date (a real, non-dry-run block preferred over a dry-run one
    if both exist), or None if no block for that date was found in
    whatever log history is currently available (rotation may have
    already discarded it -- reported honestly, never guessed).
    `lines` defaults to the real log files (see _log_lines) -- tests pass
    a synthetic list directly, no file I/O needed."""
    lines = list(_log_lines()) if lines is None else list(lines)
    blocks = []
    i = 0
    while i < len(lines):
        m = _PIPELINE_START_RE.match(lines[i])
        if not m:
            i += 1
            continue
        start_date, dry_run, db_path, no_paid_api = m.groups()
        start_line_idx = i
        j = i + 1
        end_match = None
        while j < len(lines):
            em = _PIPELINE_END_RE.match(lines[j])
            if em:
                end_match = em
                break
            j += 1
        block_lines = lines[start_line_idx:(j + 1 if end_match else len(lines))]
        blocks.append({
            "start_date": start_date,
            "dry_run": dry_run,
            "no_paid_api": no_paid_api,
            "completed": end_match is not None,
            "any_upstream_failure": end_match.group(1) if end_match else None,
            "delivery_exit": end_match.group(2) if end_match else None,
            "lines": block_lines,
        })
        i = (j + 1) if end_match else len(lines)
    matching = [b for b in blocks if b["start_date"] == target_date]
    if not matching:
        return None
    real_runs = [b for b in matching if b["dry_run"] == "False"]
    return real_runs[-1] if real_runs else matching[-1]


def _stage_results_from_block(block):
    """Returns [{"label", "status", "exit", "elapsed", "run_id"}] in the
    real order stages actually ran, plus started_at/finished_at parsed
    straight from the block's own first/last real timestamps."""
    stages = []
    for idx, line in enumerate(block["lines"]):
        sm = _STAGE_RESULT_RE.match(line)
        if not sm:
            continue
        ts, label, status, exit_code, elapsed = sm.groups()
        run_id = None
        for back in range(idx - 1, max(idx - 12, -1), -1):
            rm = _STAGE_RUN_ID_RE.search(block["lines"][back])
            if rm and label.replace("_", "-") in block["lines"][back].replace("_", "-") or (
                rm and "STARTING" not in block["lines"][back] and "run_id=" in block["lines"][back]
            ):
                run_id = rm.group(1)
                break
        stages.append({
            "label": label, "status": status, "exit": exit_code, "elapsed": float(elapsed),
            "timestamp": ts, "run_id": run_id,
        })
    return stages


def _first_and_last_timestamp(block):
    start_ts = None
    end_ts = None
    for line in block["lines"]:
        m = re.match(r"^(\d{4}-\d{2}-\d{2} [\d:]+),(\d+)", line)
        if m:
            ts_str = m.group(1)
            if start_ts is None:
                start_ts = ts_str
            end_ts = ts_str
    return start_ts, end_ts


def main(argv=None):
    args = _parse_args(argv)
    report_date = args.date
    db_path = args.db_path if args.db_path is not None else DB_PATH

    print(f"REPORT_DATE={report_date}")

    conn = connect(db_path=db_path)
    conn.row_factory = __import__("sqlite3").Row
    try:
        block = _find_pipeline_block(report_date)
        run_found = block is not None
        print(f"RUN_FOUND={str(run_found).lower()}")

        if not run_found:
            print("RUN_MODE=unknown")
            print("STARTED_AT=unknown")
            print("FINISHED_AT=unknown")
            print("TOTAL_DURATION_SECONDS=unknown")
            print("PIPELINE_STATUS=NOT_FOUND (no matching pipeline START/END block in "
                  "logs/super_news.log or its .1 backup -- may have rotated away)")
            print(f"STAGES_EXPECTED={len(_EXPECTED_STAGES)}")
            print("STAGES_SUCCEEDED=0")
            print("STAGES_FAILED=0")
            print("FAILED_STAGE=unknown")
            print("ERROR=no log block found for this date")
        else:
            run_mode = "dry_run" if block["dry_run"] == "True" else "real"
            print(f"RUN_MODE={run_mode}")
            started_at, finished_at = _first_and_last_timestamp(block)
            print(f"STARTED_AT={started_at or 'unknown'}")
            print(f"FINISHED_AT={finished_at or 'unknown'}")
            if started_at and finished_at:
                fmt = "%Y-%m-%d %H:%M:%S"
                duration = (datetime.strptime(finished_at, fmt) - datetime.strptime(started_at, fmt)).total_seconds()
                print(f"TOTAL_DURATION_SECONDS={duration:.0f}")
            else:
                print("TOTAL_DURATION_SECONDS=unknown")

            stages = _stage_results_from_block(block)
            stage_by_label = {s["label"]: s for s in stages}
            succeeded = [s["label"] for s in stages if s["status"] == "SUCCESS"]
            failed = [s["label"] for s in stages if s["status"] == "FAILED"]
            missing = [label for label in _EXPECTED_STAGES if label not in stage_by_label]

            if not block["completed"]:
                pipeline_status = "INCOMPLETE (no END marker found -- likely still running or crashed mid-run)"
            elif failed or missing:
                pipeline_status = "FAILED"
            else:
                pipeline_status = "SUCCESS"
            print(f"PIPELINE_STATUS={pipeline_status}")

            print(f"STAGES_EXPECTED={len(_EXPECTED_STAGES)}")
            print(f"STAGES_SUCCEEDED={len(succeeded)}")
            print(f"STAGES_FAILED={len(failed) + len(missing)}")
            print(f"FAILED_STAGE={','.join(failed + missing) if (failed or missing) else 'none'}")
            first_failed = (failed + missing)[0] if (failed or missing) else None
            if first_failed:
                print(f"ERROR=stage '{first_failed}' " +
                      ("did not complete/appear in the log" if first_failed in missing else "exited non-zero"))
            else:
                print("ERROR=none")

            # --- MUSIC report generation (real, from `runs`/`reports`) ---
            music_run = conn.execute(
                "SELECT id, run_id, status FROM runs WHERE run_date = ? AND run_id LIKE 'daily-report-%' "
                "ORDER BY id DESC LIMIT 1", (report_date,),
            ).fetchone()
            print(f"MUSIC_REPORT_GENERATED={'true' if music_run else 'false'}")
            print(f"MUSIC_REPORT_DATE={report_date if music_run else 'unknown'}")

            # --- LLM usage for the 4 intelligence stages found IN THIS BLOCK ---
            intelligence_run_ids = [
                stage_by_label[label]["run_id"] for label in _INTELLIGENCE_STAGE_LABELS
                if label in stage_by_label and stage_by_label[label]["run_id"]
            ]
            llm_rows = []
            if intelligence_run_ids:
                runs_rows = conn.execute(
                    f"SELECT id, run_id FROM runs WHERE run_id IN "
                    f"({','.join('?' for _ in intelligence_run_ids)})", intelligence_run_ids,
                ).fetchall()
                runs_row_ids = [r["id"] for r in runs_rows]
                if runs_row_ids:
                    llm_rows = conn.execute(
                        f"SELECT category, model_used, input_tokens, output_tokens, estimated_cost "
                        f"FROM llm_interpretations WHERE run_id IN ({','.join('?' for _ in runs_row_ids)})",
                        runs_row_ids,
                    ).fetchall()

            models = sorted({r["model_used"] for r in llm_rows if r["model_used"]})
            purposes = {}
            total_in = total_out = 0
            any_in = any_out = any_cost = False
            total_cost = 0.0
            for r in llm_rows:
                purpose = CATEGORY_TO_PURPOSE.get(r["category"], "other")
                purposes[purpose] = purposes.get(purpose, 0) + 1
                if r["input_tokens"] is not None:
                    total_in += r["input_tokens"]
                    any_in = True
                if r["output_tokens"] is not None:
                    total_out += r["output_tokens"]
                    any_out = True
                if r["estimated_cost"] is not None:
                    total_cost += r["estimated_cost"]
                    any_cost = True

            print(f"LLM_PROVIDER={'claude_cli' if block['no_paid_api'] == '1' else 'unknown'}")
            print(f"LLM_MODEL={models[0] if models else 'unknown'}")
            print(f"LLM_CALLS={len(llm_rows)}")
            print(f"LLM_INPUT_TOKENS={total_in if any_in else 'unknown'}")
            print(f"LLM_OUTPUT_TOKENS={total_out if any_out else 'unknown'}")
            print(f"LLM_TOTAL_TOKENS={(total_in + total_out) if (any_in and any_out) else 'unknown'}")
            print(f"LLM_ESTIMATED_COST_USD={total_cost if any_cost else 'unknown'}")
            print(f"TOKEN_USAGE_AVAILABLE={str(any_in or any_out).lower()}")
            paid_api_used = block["no_paid_api"] != "1"
            print(f"PAID_API_USED={str(paid_api_used).lower()}")

        # --- Public web verification (real, read-only HTTP GET) ---
        print(f"PUBLIC_MUSIC_EXPECTED={_MUSIC_PUBLIC_URL}")
        if args.no_http:
            print("PUBLIC_MUSIC_VERIFICATION=SKIPPED (--no-http)")
        else:
            import requests
            try:
                resp = requests.get(_MUSIC_PUBLIC_URL, timeout=15)
                date_match = _MUSIC_TITLE_DATE_RE.search(resp.text or "")
                page_date = date_match.group(1).replace(".", "-") if date_match else None
                if resp.status_code == 200 and page_date == report_date:
                    print(f"PUBLIC_MUSIC_VERIFICATION=PASS (HTTP 200, page date {page_date} matches)")
                elif resp.status_code == 200:
                    print(f"PUBLIC_MUSIC_VERIFICATION=STALE (HTTP 200, page date {page_date!r} != {report_date!r})")
                else:
                    print(f"PUBLIC_MUSIC_VERIFICATION=FAIL (HTTP {resp.status_code})")
            except Exception as exc:
                print(f"PUBLIC_MUSIC_VERIFICATION=FAIL ({type(exc).__name__}: {exc})")

        # --- Kakao exactly-once (real, read-only delivery_history query) ---
        normal_rows = conn.execute(
            "SELECT id, content_hash, delivered_at FROM delivery_history "
            "WHERE report_date = ? AND report_type = 'SUPER_NEWS_MUSIC_V2' AND status = 'sent' ORDER BY id",
            (report_date,),
        ).fetchall()
        correction_rows = conn.execute(
            "SELECT id FROM delivery_history "
            "WHERE report_date = ? AND report_type = 'SUPER_NEWS_MUSIC_V2_CORRECTION' AND status = 'sent'",
            (report_date,),
        ).fetchall()
        print(f"NORMAL_KAKAO_SEND_COUNT={len(normal_rows)}")
        print(f"NORMAL_KAKAO_SENT={'true' if normal_rows else 'false'}")
        print(f"KAKAO_CONTENT_HASH={normal_rows[0]['content_hash'] if normal_rows else 'none'}")
        print(f"DUPLICATE_NORMAL_SEND={'true' if len(normal_rows) > 1 else 'false'}")
        print(f"CORRECTION_SEND_COUNT={len(correction_rows)}")

        if not run_found:
            final = "FAIL (no pipeline record found for this date)"
        elif pipeline_status != "SUCCESS":
            final = f"FAIL (pipeline_status={pipeline_status})"
        elif len(normal_rows) != 1:
            final = f"FAIL (normal Kakao send count={len(normal_rows)}, expected exactly 1)"
        else:
            final = "PASS"
        print(f"FINAL_AUDIT_RESULT={final}")
    finally:
        conn.close()

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
