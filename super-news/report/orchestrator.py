"""Report V1 daily orchestrator: SELECT candidates -> synthesize (news,
combined LLM call) / diff (music, deterministic) -> validate -> persist, as
one run with its own run_id.

Reuses ingestion.orchestrator.start_run/finalize_run exactly as
music.orchestrator does -- these only touch runs/run_metadata and take no
news- or music-specific arguments, so a report-generation run gets its own
run_id/runs row without inventing a second run-lifecycle implementation.
Never touches raw_items/normalized_items collection -- that layer is closed
and this module only reads from it.
"""

import logging
from datetime import datetime, timedelta, timezone

from ingestion.orchestrator import finalize_run, start_run
from report.ai_synthesis import synthesize_news
from report.candidate_selection import select_news_candidates
from report.llm_interface import build_llm
from report.music_diff import compute_music_diff, render_music_report
from report.persistence import persist_report_run
from report.validation import CategoryValidationError, validate_all_categories

logger = logging.getLogger(__name__)

NEWS_CATEGORIES = ("AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY")

# See ingestion/orchestrator.py's _KST docstring: fixed +09:00 offset,
# stdlib-only, exact for KST (no DST).
_KST = timezone(timedelta(hours=9))

_STATUS_TO_RUN_RESULT = {
    "REPORT_GENERATED": "SUCCESS",
    "REPORT_FAILED": "FAILED",
    "NOT_READY": "SKIPPED",
}


def run_daily_report(conn, run_id, run_date=None, llm=None):
    """Runs one full daily report-generation execution. `llm` lets tests
    (and any future caller) inject a fake StructuredLLM; production code
    leaves it None and gets report.llm_interface.build_llm()'s config-driven
    choice. Never raises for an ordinary news-synthesis failure (LLM error,
    validation failure) -- those become REPORT_FAILED run_category_status
    rows, matching the ingestion orchestrator's "isolate the failure, keep
    going" convention. Only start_run's own GlobalFailureError subclasses
    propagate."""
    effective_run_date = run_date or datetime.now(_KST).strftime("%Y-%m-%d")
    runs_row_id = start_run(conn, run_id, effective_run_date, registry_hash=None)

    candidates_by_category = select_news_candidates(conn, NEWS_CATEGORIES, effective_run_date)

    news_result = None
    valid_selections = {}
    validation_errors = {}
    has_any_news_candidate = any(len(c) > 0 for c in candidates_by_category.values())
    if has_any_news_candidate:
        # Only construct an LLM client when there's actually something to
        # send it -- a zero-candidate day must never require ANTHROPIC_API_KEY
        # to be configured, and must never touch the provider at all.
        try:
            llm_instance = llm if llm is not None else build_llm()
            news_result = synthesize_news(conn, llm_instance, candidates_by_category, effective_run_date)
            if news_result is not None:
                valid_selections, validation_errors = validate_all_categories(
                    news_result["parsed"], candidates_by_category
                )
        except Exception as exc:
            # A total LLM-call failure (network error, auth error, provider
            # outage, ...) must not take MUSIC down with it, and must not
            # crash the run -- every news category that actually had
            # candidates is recorded REPORT_FAILED; a category with zero
            # candidates stays NOT_READY (see persist_report_run: no
            # candidates -> NOT_READY regardless of validation_errors,
            # since it's not in that dict).
            logger.error("run_id=%s news synthesis FAILED: %s", run_id, type(exc).__name__)
            news_result = None
            validation_errors = {
                category: CategoryValidationError(category, f"LLM call failed: {type(exc).__name__}: {exc}")
                for category in NEWS_CATEGORIES
                if len(candidates_by_category.get(category, [])) > 0
            }

    music_diff = compute_music_diff(conn, effective_run_date)
    music_content = render_music_report(music_diff)

    category_outcomes = persist_report_run(
        conn, runs_row_id, effective_run_date, news_result, valid_selections,
        validation_errors, candidates_by_category, music_diff, music_content,
    )

    results = [
        {"source_name": "report_generation", "category": category,
         "status": _STATUS_TO_RUN_RESULT[outcome["status"]]}
        for category, outcome in category_outcomes.items()
    ]
    final_status = finalize_run(conn, runs_row_id, results)
    logger.info("run_id=%s status=%s category_outcomes=%s", run_id, final_status, category_outcomes)
    return {
        "run_id": run_id,
        "runs_row_id": runs_row_id,
        "status": final_status,
        "category_outcomes": category_outcomes,
    }
