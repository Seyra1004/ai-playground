from __future__ import annotations

"""Deterministic conversion only: takes the already-verified authored
carousel/caption/Threads copy from scripts/real_content_swipe_info.py (the
completed hospital-refund package) and saves it under the exact semantic
cache key run_daily.py computes for today's bundle -- no new Claude
authoring, just a Python format conversion."""

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import compute_hash  # noqa: E402
from core.config import load_account_config, load_brand_config  # noqa: E402
from pipeline import semantic_cache  # noqa: E402
from pipeline.discovery import load_research_bundle  # noqa: E402
from scripts.real_content_swipe_info import (  # noqa: E402
    build_real_instagram_caption,
    build_real_pages,
    build_real_threads_text,
)

ACCOUNT_ID = "swipe_info"
RUN_DATE = "2026-08-22"
TOP_CANDIDATE_ID = "c1-hospital-refund"


def main() -> int:
    account = load_account_config(ACCOUNT_ID)
    brand = load_brand_config(account.brand_config_path)

    bundle = load_research_bundle(ACCOUNT_ID, RUN_DATE)
    fact_sheet = bundle.fact_sheets_by_candidate[TOP_CANDIDATE_ID]
    fact_sheet.content_id = f"{ACCOUNT_ID}-{RUN_DATE}"  # mirrors run_daily.py's own overwrite, exactly

    evidence_hash = compute_hash(dataclasses.asdict(fact_sheet))
    account_config_hash = compute_hash(dataclasses.asdict(account))
    brand_hash = compute_hash(brand.raw)
    key = semantic_cache.compute_semantic_cache_key(evidence_hash, account_config_hash, brand_hash)

    payload = {
        "pages": [dataclasses.asdict(p) for p in build_real_pages()],
        "instagram_caption": build_real_instagram_caption(),
        "threads_text": build_real_threads_text(),
    }

    path = semantic_cache.save_semantic_output(os.path.join("data", "semantic_cache", ACCOUNT_ID), key, payload)
    print(f"semantic_cache_key: {key}")
    print(f"saved: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
