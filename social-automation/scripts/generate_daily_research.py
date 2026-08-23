from __future__ import annotations

"""Real daily topic-research upstream for SWIPE_INFO.

Fetches CURRENT candidates live from a confirmed-working official public
source (pipeline/live_discovery.py, plain HTTP + regex, no API key, ZERO
PAYG), TRANSFORMS a bounded top-ranked pool of raw sources into independent,
user-benefit-framed SWIPE_INFO topics (pipeline/topic_transform.py -- SOURCE
!= TOPIC; a raw press-release/news title is a discovery opportunity, never
a carousel topic by itself), scores/dedupes the resulting topics with the
existing deterministic code (core/scoring.py, pipeline/daily_state.py),
verifies ranked candidates in order and stops at the first with
mechanically-sufficient official evidence, and writes the SAME
data/daily_input/<account>/<date>/ bundle contract pipeline/discovery.py and
scripts/run_daily.py already expect. No dependency on
scripts/real_content_swipe_info.py's hardcoded literals.
"""

import argparse
import dataclasses
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import compute_hash, get_cached, set_cached  # noqa: E402
from core.database import get_connection, init_db  # noqa: E402
from core.scoring import evaluate_candidate  # noqa: E402
from pipeline import daily_state  # noqa: E402
from pipeline.discovery import research_input_dir  # noqa: E402
from pipeline.live_discovery import (  # noqa: E402
    build_minimal_fact_sheet,
    discover_live_candidates,
    fetch_article_body,
    has_sufficient_evidence,
)
from pipeline.topic_transform import TopicTransformError, transform_candidates  # noqa: E402

ACCOUNT_ID = "swipe_info"
MIN_SCORE = 70
# Bounded pool sent to the ONE topic-transformation LLM call per run -- cost
# control (one call for a top slice, never once per raw source), not a
# hard cap on how many genuinely qualify.
TRANSFORM_POOL_SIZE = 20


def cache_excerpts(conn, excerpts: list, today: str) -> int:
    """Reuse an already-cached excerpt if its hash is unchanged; otherwise
    store it. Returns how many were freshly cached (vs reused)."""
    fresh = 0
    for exc in excerpts:
        h = compute_hash({"text": exc.text, "extracted_fields": exc.extracted_fields})
        exc.excerpt_hash = h
        cache_key = f"{ACCOUNT_ID}:excerpt:{exc.excerpt_id}:{h}"
        if get_cached(conn, cache_key) is None:
            set_cached(conn, cache_key, dataclasses.asdict(exc), today)
            fresh += 1
    return fresh


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    today = args.date

    conn = get_connection(os.path.join("data", f"{ACCOUNT_ID}.db"))
    init_db(conn)

    candidates, sources_by_id, excerpts = discover_live_candidates(today)
    fresh_count = cache_excerpts(conn, excerpts, today)

    # Same-batch raw-title dedupe first -- cheap hygiene that shrinks the
    # pool BEFORE the one bounded transformation call (two sources
    # reporting the identical press release shouldn't cost two transform
    # slots).
    candidates = daily_state.dedupe_candidates(candidates)

    # --- SOURCE -> TOPIC transformation -------------------------------
    # A raw government/press title is a discovery source, never a carousel
    # topic by itself. Rank the raw pool by the existing deterministic
    # signals purely to bound which candidates get sent to the ONE
    # transformation call (never once per source); the resulting derived
    # topics are what actually get scored/deduped/selected below.
    pool_ranked = sorted(candidates, key=lambda c: evaluate_candidate(c, MIN_SCORE)[1].total, reverse=True)
    pool = pool_ranked[:TRANSFORM_POOL_SIZE]

    body_by_id = {}
    transform_pool = []
    for c in pool:
        source = sources_by_id[f"src-{c.candidate_id}"]
        body = fetch_article_body(source)  # "" if unavailable -- safe fallback, gate handles it
        body_by_id[c.candidate_id] = body
        transform_pool.append({"candidate_id": c.candidate_id, "category": c.category, "title": c.topic, "body_excerpt": body or c.topic})

    try:
        transform_results = transform_candidates(transform_pool)
    except TopicTransformError as exc:
        print(f"TOPIC_TRANSFORM_FAILED={exc}")
        transform_results = {}

    def _clamp01(x) -> float:
        try:
            return max(0.0, min(1.0, float(x)))
        except (TypeError, ValueError):
            return 0.0

    candidates_by_id = {c.candidate_id: c for c in pool}
    qualified_candidates = []
    rejected_examples = []
    for cid, result in transform_results.items():
        original = candidates_by_id.get(cid)
        if original is None:
            continue
        if not result.get("qualified"):
            rejected_examples.append(
                {
                    "candidate_id": cid,
                    "raw_title": original.topic,
                    "rejection_reason": result.get("rejection_reason", "not a derivable practical topic"),
                }
            )
            continue
        derived_topic = (result.get("derived_topic") or "").strip()
        if not derived_topic:
            rejected_examples.append({"candidate_id": cid, "raw_title": original.topic, "rejection_reason": "qualified but no derived_topic returned"})
            continue
        transformed = dataclasses.replace(
            original,
            topic=derived_topic,
            summary=original.topic,  # SOURCE != TOPIC: raw title kept here for traceability only
            practical_value_signal=_clamp01(result.get("practical_value_signal", original.practical_value_signal)),
            save_share_signal=_clamp01(result.get("save_share_signal", original.save_share_signal)),
            population_reach_signal=_clamp01(result.get("population_reach_signal", original.population_reach_signal)),
        )
        qualified_candidates.append(transformed)
        transformed._user_benefit = result.get("user_benefit", "")  # noqa: SLF001 -- report-only, not persisted
        transformed._why_now = result.get("why_now", "")  # noqa: SLF001
        transformed._actionability_note = result.get("actionability_note", "")  # noqa: SLF001
        transformed._source_type = sources_by_id[f"src-{cid}"].source_type.value  # noqa: SLF001

    # Re-dedupe on the DERIVED topics (two different raw sources can
    # independently transform into the same underlying practical question)
    # and re-run the permanent history guard against derived-topic history
    # -- record_selected_topic stores the derived topic going forward, so
    # the guard must compare derived-to-derived, not raw-title-to-derived.
    qualified_candidates = daily_state.dedupe_candidates(qualified_candidates)
    all_time_fps = daily_state.recent_topic_fingerprints(conn, ACCOUNT_ID, today, daily_state.PERMANENT_WINDOW_DAYS)
    daily_state.reject_previously_used_candidates(conn, ACCOUNT_ID, qualified_candidates, all_time_fps)
    conn.close()

    ranked = sorted(
        qualified_candidates,
        key=lambda c: evaluate_candidate(c, MIN_SCORE)[1].total,
        reverse=True,
    )

    print("=== TOP FINAL TOPICS (transformed, practical-value ranked) ===")
    for c in ranked[:10]:
        accepted, breakdown, _reason = evaluate_candidate(c, MIN_SCORE)
        print(f"- TOPIC: {c.topic}")
        print(f"  USER_BENEFIT: {getattr(c, '_user_benefit', '')}")
        print(f"  WHY_NOW: {getattr(c, '_why_now', '')}")
        print(f"  SAVE_VALUE: {c.save_share_signal:.2f}  ACTIONABILITY: {getattr(c, '_actionability_note', '')}")
        print(f"  SOURCE_TYPE: {getattr(c, '_source_type', '')}  SCORE: {breakdown.total:.1f}  ACCEPTED: {accepted}")
    if not ranked:
        print("(none -- NO_QUALIFIED_TOPIC)")

    print("=== REJECTED (news/PR/status) EXAMPLES ===")
    for r in rejected_examples[:10]:
        print(f"- RAW: {r['raw_title']}")
        print(f"  REJECTION_REASON: {r['rejection_reason']}")

    out_dir = research_input_dir(ACCOUNT_ID, today)
    # Deterministically reset ONLY this run-date's bundle dir before writing
    # -- prevents a stale fact_sheets/*.json (e.g. from a prior verified
    # candidate that no longer verifies today) from lingering alongside
    # fresh output. Scoped to data/daily_input/<account>/<date>/ only: never
    # touches other dates, the SQLite excerpt cache, or output/ packages.
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "fact_sheets"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "excerpts"), exist_ok=True)

    with open(os.path.join(out_dir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump([dataclasses.asdict(c) for c in ranked], f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "excerpts", "evidence_excerpts.json"), "w", encoding="utf-8") as f:
        json.dump([dataclasses.asdict(e) for e in excerpts], f, ensure_ascii=False, indent=2)

    # Title alone is often too short to carry both an eligibility and an
    # amount/deadline marker. Widen to title + the SITE-SPECIFIC article-body
    # extraction (content-area only, no nav/footer/sidebar -- unlike the
    # earlier generic full-page attempt, which produced false-positive
    # keyword matches from site navigation). The evidence gate itself is
    # unchanged/unweakened; it just sees more real text.
    verified_id = None
    for c in ranked:
        accepted, _breakdown, _reason = evaluate_candidate(c, MIN_SCORE)
        if not accepted:
            continue
        source = sources_by_id[f"src-{c.candidate_id}"]
        # Reuse the body already fetched for transformation -- never refetch
        # a candidate that was already in the transform pool.
        body = body_by_id.get(c.candidate_id)
        if body is None:
            body = fetch_article_body(source)  # "" if unavailable/failed -- safe fallback to title-only
        # The gate must see only REAL extracted evidence, never the derived
        # topic's own phrasing (which could contain eligibility/deadline-
        # looking words the source text doesn't actually support).
        if not has_sufficient_evidence(body):
            continue
        evidence_text = f"{c.topic} {body}".strip()
        fs = build_minimal_fact_sheet(c, source, content_id=f"{ACCOUNT_ID}-{today}", evidence_text=evidence_text)
        with open(os.path.join(out_dir, "fact_sheets", f"{c.candidate_id}.json"), "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(fs), f, ensure_ascii=False, indent=2)
        verified_id = c.candidate_id
        break  # stop at first candidate with enough official evidence

    print(f"candidates: {len(ranked)}")
    print(f"excerpts_cached_fresh: {fresh_count}/{len(excerpts)}")
    print(f"verified_candidate: {verified_id}")
    print(f"bundle_dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
