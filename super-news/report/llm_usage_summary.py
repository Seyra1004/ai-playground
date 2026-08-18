"""LLM usage accounting: a small, READ-ONLY aggregation layer over data
report.persistence's own INSERT calls already write to llm_interpretations
for every real synthesis call -- never a duplicate capture mechanism, and
never a new counter scattered into unrelated code. A Claude CLI /
subscription call (report.llm_claude_cli) still populates real
input_tokens/output_tokens from the CLI's own JSON usage payload (see
report.llm_claude_cli._run_once/_run_once's `usage` dict) -- estimated_cost
is always None for a subscription call (there is no per-token price for a
flat subscription; report.producer_synthesis/report.music_trend_synthesis/
report.ai_synthesis all already set it to None for a fresh result, never a
guess) -- this module preserves that same None-means-unknown contract, it
never fabricates a number. Translation (report.translation.
translate_and_cache) has no token/cost tracking at all (translation_cache
carries no such columns); its call count here is approximated by counting
NEW cache rows created that day -- a cache HIT makes no real LLM call at
all, so it is never counted."""

CATEGORY_TO_PURPOSE = {
    "MUSIC_TREND_INTELLIGENCE": "music_trend_intelligence",
    "MUSIC_PRODUCER_INTELLIGENCE": "producer_ar_intelligence",
}


def summarize_llm_usage(conn, run_date_kst):
    """Returns a structured usage summary for every real llm_interpretations
    row belonging to a run on `run_date_kst`, plus a real translation
    call-count for the same day. `conn` must have row_factory=sqlite3.Row
    (the same requirement report.web_data_v2.build_dashboard_data_v2
    already has). Never invents a token/cost figure: input_tokens/
    output_tokens/estimated_cost/total_tokens are None whenever nothing
    real is known -- callers must render None as "unknown", NEVER as 0
    (unknown != zero)."""
    rows = conn.execute(
        """SELECT li.category, li.model_used, li.input_tokens, li.output_tokens, li.estimated_cost
           FROM llm_interpretations li JOIN runs r ON r.id = li.run_id
           WHERE r.run_date = ?""",
        (run_date_kst,),
    ).fetchall()

    purposes = {}
    models = set()
    total_input = total_output = 0
    total_cost = 0.0
    any_input_known = any_output_known = any_cost_known = False
    call_count = 0

    for row in rows:
        purpose = CATEGORY_TO_PURPOSE.get(row["category"], "other")
        purposes[purpose] = purposes.get(purpose, 0) + 1
        call_count += 1
        if row["model_used"]:
            models.add(row["model_used"])
        if row["input_tokens"] is not None:
            total_input += row["input_tokens"]
            any_input_known = True
        if row["output_tokens"] is not None:
            total_output += row["output_tokens"]
            any_output_known = True
        if row["estimated_cost"] is not None:
            total_cost += row["estimated_cost"]
            any_cost_known = True

    translation_row = conn.execute(
        "SELECT COUNT(*) AS c FROM translation_cache WHERE substr(created_at, 1, 10) = ?",
        (run_date_kst,),
    ).fetchone()
    translation_count = translation_row["c"] if translation_row else 0
    if translation_count:
        purposes["translation"] = translation_count
        call_count += translation_count

    return {
        "run_date": run_date_kst,
        "models": sorted(models),
        "calls": call_count,
        "input_tokens": total_input if any_input_known else None,
        "output_tokens": total_output if any_output_known else None,
        "total_tokens": (total_input + total_output) if (any_input_known and any_output_known) else None,
        "estimated_cost_usd": total_cost if any_cost_known else None,
        "token_usage_available": any_input_known or any_output_known,
        "purposes": purposes,
    }
