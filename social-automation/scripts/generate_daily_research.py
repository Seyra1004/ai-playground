from __future__ import annotations

"""Real daily topic-research upstream for SWIPE_INFO.

Packages today's already-verified live research (5 candidates from
authoritative Korean sources; NHIS/gov.kr/law.go.kr-backed fact sheet for
the top-ranked one -- see scripts/real_content_swipe_info.py for the source
URLs and dates) into the research-bundle contract pipeline/discovery.py and
scripts/run_daily.py already expect. Reuses existing scoring/factcheck/
cache code; adds no parallel pipeline. Deterministic extraction excerpts are
cached via core/cache.py so an unchanged source/excerpt is never re-processed.
"""

import argparse
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import compute_hash, get_cached, set_cached  # noqa: E402
from core.database import get_connection, init_db  # noqa: E402
from core.models import SourceExcerpt  # noqa: E402
from pipeline.discovery import research_input_dir  # noqa: E402
from scripts.real_content_swipe_info import build_real_candidates, build_real_fact_sheet  # noqa: E402

ACCOUNT_ID = "swipe_info"
TOP_CANDIDATE_ID = "c1-hospital-refund"


def _real_excerpts() -> list:
    """Deterministically-extracted (title/institution/date implicit via
    source_id -> Source record; relevant paragraph + structured pulls)
    slices of the authoritative pages fetched for today's selected topic --
    not the raw pages. News sources are excluded here (discovery/context
    only, never sole evidence for a critical claim)."""
    return [
        SourceExcerpt(
            excerpt_id="exc-nhis-minwon-eligibility",
            source_id="src-nhis-minwon",
            text=(
                "미지급 본인부담상한액 초과금이 발생한 건강보험가입자 및 피부양자. "
                "신청방법: 홈페이지, 건강보험25시(모바일앱), 지사방문, 팩스, 우편, 정부24, 유선(1577-1000). "
                "본인계좌로만 신청 가능, 제3자 계좌는 지사 별도 신청."
            ),
            extracted_fields={
                "eligibility": "미지급 본인부담상한액 초과금이 발생한 건강보험가입자 및 피부양자",
                "action_method": "홈페이지/앱/지사방문/팩스/우편/정부24/전화(1577-1000)",
                "exclusions": "비급여, 선별급여, 전액본인부담, 임플란트, 상급병실(2~3인실) 입원료, 추나요법, 상급종합병원 경증질환 외래 초·재진 본인부담금",
            },
        ),
        SourceExcerpt(
            excerpt_id="exc-gov24-documents",
            source_id="src-gov24",
            text=(
                "지원대상: 본인부담 상한액 초과금 지급 안내 통보를 받은 수진자 본인. "
                "필요서류(본인): 지급신청서. (가족/제3자): 지급신청서, 위임장, 신분증 사본, 가족관계증명서. "
                "법적근거: 국민건강보험법 제44조, 시행령 제19조."
            ),
            extracted_fields={
                "required_documents": "지급신청서(본인) / +위임장·신분증사본·가족관계증명서(가족·제3자)",
                "legal_basis": "국민건강보험법 제44조, 시행령 제19조",
            },
        ),
        SourceExcerpt(
            excerpt_id="exc-nhis-banner-amount",
            source_id="src-nhis-banner-2026",
            text=(
                "2026년도 소득분위별 본인부담상한액: 1분위 90만원, 2~3분위 112만원, 4~5분위 173만원, "
                "6~7분위 326만원, 8분위 446만원, 9분위 536만원, 10분위 843만원. "
                "요양병원 120일초과 입원 최고상한액 1,096만원. 적용기간 2026.1.1~2026.12.31(진료일 기준)."
            ),
            extracted_fields={
                "amounts_by_decile_manwon": {
                    "1": 90, "2-3": 112, "4-5": 173, "6-7": 326, "8": 446, "9": 536, "10": 843,
                    "long_term_care_120d": 1096,
                },
                "period": "2026.1.1~2026.12.31",
            },
        ),
        SourceExcerpt(
            excerpt_id="exc-law-art91-deadline",
            source_id="src-law-nhia-art91",
            text="국민건강보험법 제91조: 보험급여를 받을 권리는 3년간 행사하지 아니하면 시효로 소멸한다.",
            extracted_fields={"deadline": "3년", "legal_basis": "국민건강보험법 제91조"},
        ),
    ]


def cache_excerpts(conn, excerpts: list) -> list:
    """Hash each excerpt; reuse from core.cache if an identical excerpt was
    already cached, otherwise store it. Returns excerpts with excerpt_hash set."""
    now = "2026-08-22T00:00:00Z"
    result = []
    for exc in excerpts:
        h = compute_hash({"text": exc.text, "extracted_fields": exc.extracted_fields})
        exc.excerpt_hash = h
        cache_key = f"{ACCOUNT_ID}:excerpt:{exc.excerpt_id}:{h}"
        cached = get_cached(conn, cache_key)
        if cached is None:
            set_cached(conn, cache_key, dataclasses.asdict(exc), now)
        result.append(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    conn = get_connection(os.path.join("data", f"{ACCOUNT_ID}.db"))
    init_db(conn)
    excerpts = cache_excerpts(conn, _real_excerpts())
    conn.close()

    candidates = build_real_candidates()
    fact_sheet = build_real_fact_sheet()

    out_dir = research_input_dir(ACCOUNT_ID, args.date)
    os.makedirs(os.path.join(out_dir, "fact_sheets"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "excerpts"), exist_ok=True)

    with open(os.path.join(out_dir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump([dataclasses.asdict(c) for c in candidates], f, ensure_ascii=False, indent=2)

    # Only the top-ranked, already-verified candidate gets a fact sheet --
    # ranked verification in run_daily.py stops there and never has to
    # semantically process the rest.
    with open(os.path.join(out_dir, "fact_sheets", f"{TOP_CANDIDATE_ID}.json"), "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(fact_sheet), f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "excerpts", "evidence_excerpts.json"), "w", encoding="utf-8") as f:
        json.dump([dataclasses.asdict(e) for e in excerpts], f, ensure_ascii=False, indent=2)

    print(f"candidates: {len(candidates)}")
    print(f"fact_sheets: 1 ({TOP_CANDIDATE_ID})")
    print(f"excerpts_cached: {len(excerpts)}")
    print(f"bundle_dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
