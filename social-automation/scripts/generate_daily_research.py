from __future__ import annotations

"""Real daily topic-research upstream for SWIPE_INFO.

Fetches CURRENT candidates live from a confirmed-working official public
source (pipeline/live_discovery.py, plain HTTP + regex, no API key, ZERO
PAYG), scores/dedupes them with the existing deterministic code
(core/scoring.py, pipeline/daily_state.py), verifies ranked candidates in
order and stops at the first with mechanically-sufficient official evidence,
and writes the SAME data/daily_input/<account>/<date>/ bundle contract
pipeline/discovery.py and scripts/run_daily.py already expect. No dependency
on scripts/real_content_swipe_info.py's hardcoded literals.
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

ACCOUNT_ID = "swipe_info"
MIN_SCORE = 70


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
    conn.close()

    candidates = daily_state.dedupe_candidates(candidates)

    ranked = sorted(
        candidates,
        key=lambda c: evaluate_candidate(c, MIN_SCORE)[1].total,
        reverse=True,
    )

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
        body = fetch_article_body(source)  # "" if unavailable/failed -- safe fallback to title-only
        evidence_text = f"{c.topic} {body}".strip()
        if not has_sufficient_evidence(evidence_text):
            continue
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
