"""Producer Intelligence orchestrator: compute today's evidence -> ONE
combined LLM synthesis call (report.producer_synthesis) -> validate ->
persist, as its own run with its own run_id -- deliberately separate from
report.orchestrator.run_daily_report's news/music run, since Producer
Intelligence depends on a news+music report already existing for today (it
reads report.web_data_v2.build_dashboard_data_v2's already-computed
intelligence/spotify_chart/industry-news facts) and must never block or be
blocked by that run's own transaction.

Validation (report.validation.validate_producer_insights) runs on
synthesize_producer_intelligence's output EVERY time, whether the result
was freshly generated or reused from an identical earlier evidence set
(see report.producer_synthesis's module docstring) -- this is the one
place that distinction is allowed to not matter, because both paths get
validated identically before anything is persisted or shown. A validation
failure is a fail-safe, not a crash: nothing is persisted, and the day is
left in the same honest UNAVAILABLE state report.web_data_v2 already
renders when no row exists at all -- never a fabricated fallback
recommendation.

Uses explicit override_status on every finalize_run call rather than
letting ingestion.orchestrator._aggregate_run_status infer it: a
legitimate "no evidence today" run has zero per-source results by
construction, which _aggregate_run_status would otherwise score as
"no_enabled_source_results" -> failed -- wrong for what is actually a
successful, honest no-op day.
"""

import json
import logging

from ingestion.orchestrator import finalize_run, start_run
from report.persistence import persist_producer_intelligence
from report.producer_synthesis import build_evidence_catalog, synthesize_producer_intelligence
from report.validation import ProducerValidationError, validate_producer_insights
from report.web_data_v2 import build_dashboard_data_v2

logger = logging.getLogger(__name__)


def run_daily_producer_intelligence(conn, run_id, report_date_kst, llm=None):
    """Runs one Producer Intelligence synthesis attempt for
    report_date_kst. `llm` lets tests inject a fake StructuredLLM;
    production code leaves it None and gets
    report.llm_interface.build_llm()'s config-driven choice -- but ONLY
    constructed if there's actually evidence to synthesize from (an
    evidence-free day must never require ANTHROPIC_API_KEY to be
    configured, matching report.orchestrator.run_daily_report's same rule
    for news synthesis).

    Returns {"run_id", "runs_row_id", "status", "reason"} where status is
    one of "completed_no_evidence" / "completed_with_insights" /
    "completed_reused" / "failed". Never raises for an ordinary synthesis
    or validation failure -- only start_run's own GlobalFailureError
    subclasses propagate."""
    runs_row_id = start_run(conn, run_id, report_date_kst, registry_hash=None)

    dashboard_data = build_dashboard_data_v2(conn, report_date_kst)
    industry_news = dashboard_data["news"]["TIKTOK"]["items"] + dashboard_data["news"]["SPOTIFY"]["items"]

    # Evidence-emptiness check BEFORE constructing an LLM client -- mirrors
    # report.orchestrator.run_daily_report's has_any_news_candidate gate.
    catalog_preview = build_evidence_catalog(
        dashboard_data["intelligence"], dashboard_data["spotify_chart"], industry_news
    )
    if not catalog_preview:
        finalize_run(conn, runs_row_id, [], override_status="completed", override_failure_stage=None)
        logger.info("run_id=%s producer intelligence: no meaningful evidence today.", run_id)
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "completed_no_evidence", "reason": None}

    try:
        if llm is not None:
            llm_instance = llm
        else:
            from report.llm_interface import build_llm
            llm_instance = build_llm()
        synthesis_result = synthesize_producer_intelligence(
            conn, llm_instance, dashboard_data["intelligence"], dashboard_data["spotify_chart"],
            industry_news, report_date_kst,
        )
    except Exception as exc:
        logger.error("run_id=%s producer intelligence synthesis FAILED: %s", run_id, type(exc).__name__)
        finalize_run(
            conn, runs_row_id, [],
            override_status="failed", override_failure_stage="producer_intelligence_synthesis_failed",
        )
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}"}

    # synthesis_result is never None here: catalog_preview was non-empty,
    # and build_evidence_catalog is a pure function of the same inputs, so
    # synthesize_producer_intelligence's own internal catalog is non-empty
    # too.
    valid_refs = {e["ref"] for e in synthesis_result["catalog"]}
    evidence_by_ref = {e["ref"]: e.get("summary", "") for e in synthesis_result["catalog"]}
    try:
        validate_producer_insights(synthesis_result["parsed"], valid_refs, evidence_by_ref)
    except ProducerValidationError as exc:
        logger.error(
            "run_id=%s producer intelligence validation FAILED (reused=%s): %s",
            run_id, synthesis_result["reused"], exc.reason,
        )
        finalize_run(
            conn, runs_row_id, [],
            override_status="failed", override_failure_stage="producer_intelligence_validation_failed",
        )
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "failed", "reason": exc.reason}

    # Persist insights + the evidence catalog TOGETHER: report.web_data_v2
    # only has this run's output_text to read back from later (no separate
    # catalog column/table -- avoids a schema migration), and the renderer
    # needs the catalog's human-readable summaries to show WHAT each
    # evidence_refs code actually refers to, not just show "E1, E3" to a
    # reader. This only enriches the PERSISTED payload -- synthesis_result
    # (and report.producer_synthesis's own contract) still returns the raw
    # LLM output_text verbatim; only this orchestration boundary decides
    # to store the richer combined shape.
    persisted_result = dict(synthesis_result)
    persisted_result["output_text"] = json.dumps({
        "insights": synthesis_result["parsed"]["insights"],
        "catalog": synthesis_result["catalog"],
    }, ensure_ascii=False)
    persist_producer_intelligence(conn, runs_row_id, persisted_result)
    conn.commit()
    finalize_run(conn, runs_row_id, [], override_status="completed", override_failure_stage=None)
    status = "completed_reused" if synthesis_result["reused"] else "completed_with_insights"
    logger.info("run_id=%s producer intelligence status=%s", run_id, status)
    return {"run_id": run_id, "runs_row_id": runs_row_id, "status": status, "reason": None}
