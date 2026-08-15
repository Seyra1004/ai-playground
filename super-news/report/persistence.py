"""Atomic, single-transaction persistence for one report-generation run:
llm_interpretations (only if there was a news LLM call/reuse) + up to 4
`reports` rows (AI/ECONOMY/SOCIETY/MUSIC) + interpretation_items provenance
+ exactly 4 run_category_status rows. Either the whole run's output lands,
or none of it does -- a partial write would leave run_category_status
claiming a report exists when it doesn't (or vice versa).

Per-category status semantics (matches run_category_status's frozen CHECK):
- NOT_READY: no report was attempted for this category this run (zero
  candidates -- either the whole news call was skipped, or music had no
  chart snapshot yet for this date).
- REPORT_FAILED: an attempt was made and failed validation or the LLM call
  itself failed (failure_stage='LLM'); failure_reason is always populated.
- REPORT_GENERATED: a `reports` row was written (even if items_selected==0
  -- "nothing newsworthy today" is a successful, empty report, distinct
  from NOT_READY, which means no attempt was made at all).
"""

import hashlib
from datetime import datetime, timezone

NEWS_CATEGORIES = ("AI", "ECONOMY", "SOCIETY", "TIKTOK", "SPOTIFY")

# report.producer_synthesis.CATEGORY -- duplicated as a literal (not
# imported) to keep persistence.py free of a dependency on the synthesis
# module, matching this file's existing style (NEWS_CATEGORIES is also a
# local literal, not imported from candidate_selection.py).
PRODUCER_INTELLIGENCE_CATEGORY = "MUSIC_PRODUCER_INTELLIGENCE"
MUSIC_TREND_INTELLIGENCE_CATEGORY = "MUSIC_TREND_INTELLIGENCE"


def _render_news_report(category, selections, candidates):
    if not selections:
        return f"{category}: 오늘 보고할 뉴스가 없습니다."
    candidates_by_id = {c["id"]: c for c in candidates}
    lines = [f"{category} 뉴스 요약"]
    for sel in selections:
        candidate = candidates_by_id.get(sel["id"])
        title = candidate["normalized_title"] if candidate else "(unknown)"
        lines.append(f"- {title}\n  {sel['reason']}")
    return "\n".join(lines)


def _write_category_status(conn, runs_row_id, category, status, failure_stage,
                            failure_reason, report_id, items_collected, items_selected):
    conn.execute(
        """INSERT INTO run_category_status
           (run_id, category, status, failure_stage, report_id, items_collected,
            items_rejected, items_selected, failure_reason, retry_count)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, 0)""",
        (runs_row_id, category, status, failure_stage, report_id,
         items_collected, items_selected, failure_reason),
    )
    return {"status": status, "report_id": report_id}


def persist_report_run(conn, runs_row_id, report_date_kst, news_result, valid_selections,
                        validation_errors, candidates_by_category, music_diff, music_content):
    now = datetime.now(timezone.utc).isoformat()
    outcome = {}

    try:
        conn.execute("BEGIN")

        interpretation_id = None
        if news_result is not None:
            conn.execute(
                """INSERT INTO llm_interpretations
                   (run_id, category, model_used, prompt_version, input_hash, input_tokens,
                    output_tokens, estimated_cost, output_text, confidence, created_at)
                   VALUES (?, 'NEWS_COMBINED', ?, ?, ?, ?, ?, ?, ?, 'MEDIUM', ?)""",
                (runs_row_id, news_result["model_used"], news_result["prompt_version"],
                 news_result["input_hash"], news_result["input_tokens"], news_result["output_tokens"],
                 news_result["estimated_cost"], news_result["output_text"], now),
            )
            interpretation_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for category in NEWS_CATEGORIES:
            candidates = candidates_by_category.get(category, [])

            if category in validation_errors:
                outcome[category] = _write_category_status(
                    conn, runs_row_id, category, "REPORT_FAILED",
                    failure_stage="LLM", failure_reason=validation_errors[category].reason,
                    report_id=None, items_collected=len(candidates), items_selected=0,
                )
                continue

            if news_result is None:
                outcome[category] = _write_category_status(
                    conn, runs_row_id, category, "NOT_READY",
                    failure_stage=None, failure_reason=None,
                    report_id=None, items_collected=len(candidates), items_selected=0,
                )
                continue

            selections = valid_selections.get(category, [])
            content = _render_news_report(category, selections, candidates)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (runs_row_id, report_date_kst, category, category, content, content_hash, now),
            )
            report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            candidates_by_id = {c["id"]: c for c in candidates}
            for sel in selections:
                candidate = candidates_by_id.get(sel["id"])
                if candidate is None:
                    continue
                for item_id in candidate["item_ids"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO interpretation_items (interpretation_id, normalized_item_id) VALUES (?, ?)",
                        (interpretation_id, item_id),
                    )

            outcome[category] = _write_category_status(
                conn, runs_row_id, category, "REPORT_GENERATED",
                failure_stage=None, failure_reason=None,
                report_id=report_id, items_collected=len(candidates), items_selected=len(selections),
            )

        # MUSIC: deterministic, computed independently of the news LLM path.
        if music_diff["observed_at"] is None:
            outcome["MUSIC"] = _write_category_status(
                conn, runs_row_id, "MUSIC", "NOT_READY",
                failure_stage=None, failure_reason=None,
                report_id=None, items_collected=0, items_selected=0,
            )
        else:
            content_hash = hashlib.sha256(music_content.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT INTO reports (run_id, report_date, report_type, category, content, content_hash, generated_at)
                   VALUES (?, ?, 'MUSIC', 'MUSIC', ?, ?, ?)""",
                (runs_row_id, report_date_kst, music_content, content_hash, now),
            )
            report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            outcome["MUSIC"] = _write_category_status(
                conn, runs_row_id, "MUSIC", "REPORT_GENERATED",
                failure_stage=None, failure_reason=None, report_id=report_id,
                items_collected=len(music_diff["entries"]), items_selected=len(music_diff["entries"]),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return outcome


def persist_producer_intelligence(conn, runs_row_id, synthesis_result):
    """Writes ONE llm_interpretations row for
    report.producer_synthesis.CATEGORY (PRODUCER_INTELLIGENCE_CATEGORY).

    Deliberately independent of persist_report_run's atomic news/music
    transaction: Producer Intelligence is a separate, best-effort daily
    addition on top of an already-successful run, not a required part of
    it -- a failure persisting it (or a validation failure upstream that
    means this is never even called) must never roll back or block the
    news/music report that already succeeded this run. Caller is
    responsible for having already validated synthesis_result["parsed"]
    (report.validation.validate_producer_insights) -- this function only
    persists what it's given, exactly like persist_report_run trusts
    valid_selections has already been validated by its caller. Caller
    commits (matches persist_report_run's own transaction ownership: this
    function issues the INSERT but does not commit/rollback itself)."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, input_tokens,
            output_tokens, estimated_cost, output_text, confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'MEDIUM', ?)""",
        (runs_row_id, PRODUCER_INTELLIGENCE_CATEGORY, synthesis_result["model_used"],
         synthesis_result["prompt_version"], synthesis_result["input_hash"],
         synthesis_result["input_tokens"], synthesis_result["output_tokens"],
         synthesis_result["estimated_cost"], synthesis_result["output_text"], now),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def persist_music_trend_intelligence(conn, runs_row_id, synthesis_result):
    """Writes ONE llm_interpretations row for report.music_trend_
    synthesis.CATEGORY (MUSIC_TREND_INTELLIGENCE_CATEGORY) -- same
    contract as persist_producer_intelligence directly above: independent
    of persist_report_run's atomic transaction, caller must have already
    validated synthesis_result["parsed"] (report.validation.
    validate_music_trend_signals), caller commits."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, input_tokens,
            output_tokens, estimated_cost, output_text, confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'MEDIUM', ?)""",
        (runs_row_id, MUSIC_TREND_INTELLIGENCE_CATEGORY, synthesis_result["model_used"],
         synthesis_result["prompt_version"], synthesis_result["input_hash"],
         synthesis_result["input_tokens"], synthesis_result["output_tokens"],
         synthesis_result["estimated_cost"], synthesis_result["output_text"], now),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
