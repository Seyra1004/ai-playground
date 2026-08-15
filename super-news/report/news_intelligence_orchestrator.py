"""News Intelligence orchestrator: WHAT HAPPENED / WHY IT MATTERS / WHAT TO
WATCH per real, already-displayed V2 news item -- its own run, own run_id,
deliberately separate from report.orchestrator.run_daily_report (V1's news/
music run: never touches NEWS_COMBINED, persist_report_run, or any V1
output) and from report.producer_orchestrator (Producer Intelligence: own
llm_interpretations category, NEWS_INTELLIGENCE_V2, never MUSIC_PRODUCER_
INTELLIGENCE). Reads report.web_data_v2.build_dashboard_data_v2's already-
computed, already-displayed items -- same "read, never generate at render
time" contract report.producer_orchestrator already establishes for
Producer Intelligence.

Only LEAD-tier items across AI/ECONOMY/SOCIETY are eligible (Phase 3C
production-pilot policy -- narrowed from LEAD+STANDARD; see
MAX_SYNTHESIS_ITEMS_PER_RUN below for why). `_tier_for` in
report/web_data_v2.py grants LEAD to at most ONE item per category (index
0 of that category's ranked candidates, and only when it's fresh enough --
see that function's own docstring), so this is naturally at most 3 items
(AI + ECONOMY + SOCIETY) per run, never STANDARD's much larger pool.
STANDARD items keep rendering their own existing `reason` line (report/
web_render_v2.py's _render_item) exactly as before -- this narrowing only
changes which items get the additive WHAT_HAPPENED/WHY_IT_MATTERS/
WHAT_TO_WATCH layer, never which items are displayed at all. TIKTOK/
SPOTIFY news items are the Music Industry section's own evidence (already
cited, unmodified, by Producer Intelligence's own evidence catalog) and
are intentionally excluded here to avoid the two syntheses re-explaining
the same facts.
"""

import logging

from ingestion.orchestrator import finalize_run, start_run
from report.news_intelligence_synthesis import (
    persist_news_intelligence,
    synthesize_news_intelligence,
    validate_news_intelligence,
)
from report.web_data_v2 import build_dashboard_data_v2

logger = logging.getLogger(__name__)

_ELIGIBLE_TIERS = ("LEAD",)
_ELIGIBLE_CATEGORIES = ("AI", "ECONOMY", "SOCIETY")

# Explicit per-run cap (Phase 3C production-pilot policy): even though LEAD
# is already structurally at most 1/category * 3 categories = 3 today (see
# module docstring), this is a real, independent safety net -- never padded
# up to reach it (a category with no real LEAD that day just contributes
# nothing, per _collect_eligible_items below), only ever a ceiling that
# truncates deterministically (category order, LEAD-then-next never
# applies since only LEAD is eligible) if the tier system's own invariant
# ever changes elsewhere. Exceeding it is logged, not silently ignored.
MAX_SYNTHESIS_ITEMS_PER_RUN = 6


def _collect_eligible_items(dashboard_data):
    items = []
    for category in _ELIGIBLE_CATEGORIES:
        for item in dashboard_data["news"][category]["items"]:
            if item.get("tier") in _ELIGIBLE_TIERS and item.get("id") is not None:
                items.append(item)
    if len(items) > MAX_SYNTHESIS_ITEMS_PER_RUN:
        logger.warning(
            "news intelligence: %d eligible LEAD items exceeds MAX_SYNTHESIS_ITEMS_PER_RUN=%d "
            "-- truncating to the first %d (category order); this should not normally happen "
            "since LEAD is capped at 1/category.",
            len(items), MAX_SYNTHESIS_ITEMS_PER_RUN, MAX_SYNTHESIS_ITEMS_PER_RUN,
        )
        items = items[:MAX_SYNTHESIS_ITEMS_PER_RUN]
    return items


def run_daily_news_intelligence(conn, run_id, report_date_kst, llm=None):
    """Runs one News Intelligence synthesis attempt for report_date_kst.
    `llm` lets tests inject a fake StructuredLLM; production code leaves it
    None and gets report.llm_interface.build_llm()'s config-driven choice --
    but ONLY constructed if there's actually an eligible item (an item-free
    day must never require ANTHROPIC_API_KEY to be configured, mirroring
    report.orchestrator.run_daily_report's and report.producer_orchestrator's
    same rule).

    Returns {"run_id", "runs_row_id", "status", "reason"} where status is
    one of "completed_no_evidence" / "completed_with_insights" (every
    current item validated -- COMPLETE, reusable) / "completed_partial"
    (Phase 3C.3: a real, non-empty but INCOMPLETE subset validated -- still
    persisted/displayed for today, per item, but never reusable; the next
    run for unchanged evidence gets a real fresh retry) / "completed_reused"
    (always COMPLETE by construction -- see report.news_intelligence_
    synthesis._find_valid_reusable_interpretation) / "failed" (nothing
    validated at all). Never raises for an ordinary synthesis or validation
    failure -- only start_run's own GlobalFailureError subclasses propagate.
    A synthesis/validation failure or partial result here NEVER hides the
    underlying news items: report.web_data_v2 keeps showing title/source/
    snippet exactly as before regardless of this run's outcome, since this
    run only ever adds an optional, additive intelligence layer read back
    separately at render time (report.web_data_v2._attach_news_intelligence)."""
    runs_row_id = start_run(conn, run_id, report_date_kst, registry_hash=None)

    dashboard_data = build_dashboard_data_v2(conn, report_date_kst)
    items = _collect_eligible_items(dashboard_data)

    if not items:
        finalize_run(conn, runs_row_id, [], override_status="completed", override_failure_stage=None)
        logger.info("run_id=%s news intelligence: no eligible items today.", run_id)
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "completed_no_evidence", "reason": None}

    try:
        if llm is not None:
            llm_instance = llm
        else:
            from report.llm_interface import build_llm
            llm_instance = build_llm()
        synthesis_result = synthesize_news_intelligence(conn, llm_instance, items, report_date_kst)
    except Exception as exc:
        logger.error("run_id=%s news intelligence synthesis FAILED: %s", run_id, type(exc).__name__)
        finalize_run(
            conn, runs_row_id, [],
            override_status="failed", override_failure_stage="news_intelligence_synthesis_failed",
        )
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}"}

    items_by_id = {item["id"]: item for item in items}
    validated = validate_news_intelligence(synthesis_result["parsed"], items_by_id)
    if not validated:
        logger.error(
            "run_id=%s news intelligence: zero items passed validation (reused=%s)",
            run_id, synthesis_result["reused"],
        )
        finalize_run(
            conn, runs_row_id, [],
            override_status="failed", override_failure_stage="news_intelligence_validation_failed",
        )
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "failed",
                "reason": "no items passed structured-output validation"}

    # Phase 3C.1: REUSE = NO LLM + NO DUPLICATE PERSISTENCE. A reused
    # synthesis_result is byte-identical content already sitting in
    # llm_interpretations under an EARLIER run_id for this same
    # (report_date_kst, category, model, prompt_version,
    # output_schema_version, input_hash) tuple -- persisting it again here
    # would only ever create a content-duplicate row tied to THIS run_id,
    # never anything _attach_news_intelligence couldn't already read (that
    # read resolves by report_date_kst via a runs.run_date JOIN, not by
    # "latest run_id", so the original row is found correctly regardless).
    # Execution history (this run's own completed/completed_reused status
    # in `runs`) is unaffected -- finalize_run below still runs
    # unconditionally; only the llm_interpretations WRITE is skipped.
    if not synthesis_result["reused"]:
        persist_news_intelligence(conn, runs_row_id, synthesis_result)
        conn.commit()
    finalize_run(conn, runs_row_id, [], override_status="completed", override_failure_stage=None)

    # Phase 3C.3: COMPLETE (every current item id validated) vs. PARTIAL
    # (a real, non-empty subset -- still displayed today, per-item, by
    # report.web_data_v2._attach_news_intelligence's own existing degraded
    # handling; real news is never hidden either way) are surfaced as
    # DIFFERENT statuses so this is observable, even though a reused result
    # is always COMPLETE by construction (report.news_intelligence_
    # synthesis._find_valid_reusable_interpretation only ever returns a
    # complete row -- see that function's own docstring). A PARTIAL result
    # is intentionally still "completed" at the coarse `runs.status` level
    # (finalize_run above, unchanged) -- it produced real, useful,
    # persisted content -- but is never treated as reusable (the next run
    # for unchanged evidence gets a real fresh retry, not silence forever).
    is_complete = set(validated.keys()) == set(items_by_id.keys())
    if synthesis_result["reused"]:
        status = "completed_reused"
    elif is_complete:
        status = "completed_with_insights"
    else:
        status = "completed_partial"
    logger.info(
        "run_id=%s news intelligence status=%s (%d/%d items validated)",
        run_id, status, len(validated), len(items),
    )
    return {"run_id": run_id, "runs_row_id": runs_row_id, "status": status, "reason": None}
