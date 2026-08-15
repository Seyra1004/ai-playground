"""Music Trend Intelligence orchestrator: compute today's real evidence ->
ONE combined LLM synthesis call (report.music_trend_synthesis) ->
validate -> persist, as its own run with its own run_id -- deliberately
separate from report.orchestrator.run_daily_report's news/music run and
from report.producer_orchestrator's own run, exactly the same isolation
rationale report.producer_orchestrator already documents (this also
depends on a news+music report already existing for today, and must
never block or be blocked by either of the other two runs' transactions).

Validation (report.validation.validate_music_trend_signals) runs on
synthesize_music_trend_intelligence's output EVERY time, whether the
result was freshly generated or reused from an identical earlier
evidence set -- same "validate on every read, reused or not" rule
report.producer_orchestrator already establishes. A validation failure is
a fail-safe, not a crash: nothing is persisted, and the day is left in
the same honest UNAVAILABLE state report.web_data_v2 already renders when
no row exists at all -- never a fabricated fallback.
"""

import json
import logging

from ingestion.orchestrator import finalize_run, start_run
from report.music_trend_synthesis import build_evidence_catalog, synthesize_music_trend_intelligence
from report.persistence import persist_music_trend_intelligence
from report.validation import MusicTrendValidationError, validate_music_trend_signals
from report.web_data_v2 import build_dashboard_data_v2

logger = logging.getLogger(__name__)


def run_daily_music_trend_intelligence(conn, run_id, report_date_kst, llm=None):
    """Runs one Music Trend Intelligence synthesis attempt for
    report_date_kst. `llm` lets tests inject a fake StructuredLLM;
    production code leaves it None and gets report.llm_interface.
    build_llm()'s config-driven choice -- but ONLY constructed if there's
    actually evidence to synthesize from (an evidence-free day must never
    require ANTHROPIC_API_KEY to be configured).

    Returns {"run_id", "runs_row_id", "status", "reason"} where status is
    one of "completed_no_evidence" / "completed_with_signals" /
    "completed_reused" / "failed". Never raises for an ordinary synthesis
    or validation failure -- only start_run's own GlobalFailureError
    subclasses propagate."""
    runs_row_id = start_run(conn, run_id, report_date_kst, registry_hash=None)

    dashboard_data = build_dashboard_data_v2(conn, report_date_kst)
    industry_news = dashboard_data["news"]["TIKTOK"]["items"] + dashboard_data["news"]["SPOTIFY"]["items"]

    catalog_preview = build_evidence_catalog(
        dashboard_data["spotify_chart"], dashboard_data["tiktok_chart"], industry_news
    )
    if not catalog_preview:
        finalize_run(conn, runs_row_id, [], override_status="completed", override_failure_stage=None)
        logger.info("run_id=%s music trend intelligence: no meaningful evidence today.", run_id)
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "completed_no_evidence", "reason": None}

    try:
        if llm is not None:
            llm_instance = llm
        else:
            from report.llm_interface import build_llm
            llm_instance = build_llm()
        synthesis_result = synthesize_music_trend_intelligence(
            conn, llm_instance, dashboard_data["spotify_chart"], dashboard_data["tiktok_chart"],
            industry_news, report_date_kst,
        )
    except Exception as exc:
        logger.error("run_id=%s music trend intelligence synthesis FAILED: %s", run_id, type(exc).__name__)
        finalize_run(
            conn, runs_row_id, [],
            override_status="failed", override_failure_stage="music_trend_intelligence_synthesis_failed",
        )
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}"}

    # synthesis_result is never None here: catalog_preview was non-empty,
    # and build_evidence_catalog is a pure function of the same inputs.
    valid_refs = {e["ref"] for e in synthesis_result["catalog"]}
    try:
        validate_music_trend_signals(synthesis_result["parsed"], valid_refs)
    except MusicTrendValidationError as exc:
        logger.error(
            "run_id=%s music trend intelligence validation FAILED (reused=%s): %s",
            run_id, synthesis_result["reused"], exc.reason,
        )
        finalize_run(
            conn, runs_row_id, [],
            override_status="failed", override_failure_stage="music_trend_intelligence_validation_failed",
        )
        return {"run_id": run_id, "runs_row_id": runs_row_id, "status": "failed", "reason": exc.reason}

    persisted_result = dict(synthesis_result)
    persisted_result["output_text"] = json.dumps({
        "genre_signals": synthesis_result["parsed"]["genre_signals"],
        "production_notes": synthesis_result["parsed"]["production_notes"],
        "producer_references": synthesis_result["parsed"]["producer_references"],
        "kpop_ar_notes": synthesis_result["parsed"]["kpop_ar_notes"],
        "catalog": synthesis_result["catalog"],
    }, ensure_ascii=False)
    persist_music_trend_intelligence(conn, runs_row_id, persisted_result)
    conn.commit()
    finalize_run(conn, runs_row_id, [], override_status="completed", override_failure_stage=None)
    status = "completed_reused" if synthesis_result["reused"] else "completed_with_signals"
    logger.info("run_id=%s music trend intelligence status=%s", run_id, status)
    return {"run_id": run_id, "runs_row_id": runs_row_id, "status": status, "reason": None}
