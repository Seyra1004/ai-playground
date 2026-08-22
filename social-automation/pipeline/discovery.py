from __future__ import annotations

import json
import os
from dataclasses import dataclass

from core.models import Claim, ClaimType, FactSheet, Source, SourceType, TopicCandidate, VerificationStatus


@dataclass
class ResearchBundle:
    """A day's discovery+verification research. Produced upstream by the
    semantic/discovery layer (a Claude Code session doing WebSearch/WebFetch
    and claim-evidence verification, saved as plain JSON) -- this module and
    the daily orchestrator never call a search or LLM API themselves.

    fact_sheets_by_candidate only contains entries for candidates that were
    actually investigated upstream (in score-rank order, stopping at the
    first one that verifies) -- a candidate absent here means investigation
    was not attempted or was inconclusive, and the orchestrator skips it.
    """

    candidates: list  # list[TopicCandidate]
    fact_sheets_by_candidate: dict  # candidate_id -> FactSheet


def research_input_dir(account_id: str, run_date: str) -> str:
    return os.path.join("data", "daily_input", account_id, run_date)


def _candidate_from_dict(d: dict) -> TopicCandidate:
    return TopicCandidate(**d)


def _source_from_dict(d: dict) -> Source:
    d = dict(d)
    d["source_type"] = SourceType(d["source_type"])
    return Source(**d)


def _claim_from_dict(d: dict) -> Claim:
    d = dict(d)
    d["claim_type"] = ClaimType(d["claim_type"])
    d["status"] = VerificationStatus(d.get("status", "unverified"))
    return Claim(**d)


def _fact_sheet_from_dict(d: dict) -> FactSheet:
    d = dict(d)
    d["claims"] = [_claim_from_dict(c) for c in d["claims"]]
    d["sources"] = [_source_from_dict(s) for s in d["sources"]]
    return FactSheet(**d)


def load_research_bundle(account_id: str, run_date: str):
    """Returns a ResearchBundle for data/daily_input/<account>/<date>/, or
    None if no bundle has been produced for this account/date yet."""
    base = research_input_dir(account_id, run_date)
    candidates_path = os.path.join(base, "candidates.json")
    if not os.path.isfile(candidates_path):
        return None

    with open(candidates_path, "r", encoding="utf-8") as f:
        candidates = [_candidate_from_dict(c) for c in json.load(f)]

    fact_sheets = {}
    fs_dir = os.path.join(base, "fact_sheets")
    if os.path.isdir(fs_dir):
        for fname in sorted(os.listdir(fs_dir)):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(fs_dir, fname), "r", encoding="utf-8") as f:
                fact_sheets[fname[:-5]] = _fact_sheet_from_dict(json.load(f))

    return ResearchBundle(candidates=candidates, fact_sheets_by_candidate=fact_sheets)


DRY_RUN_CANDIDATE_ID = "dryrun-c1-sample-benefit"


def build_dry_run_bundle(run_date: str) -> ResearchBundle:
    """Small synthetic bundle for --dry-run orchestrator verification only.
    Not real content, not the hospital-refund package, no network access.
    Exercises scoring -> ranked verification -> fact-sheet -> page-plan the
    same way a real bundle would."""
    candidates = [
        TopicCandidate(
            candidate_id=DRY_RUN_CANDIDATE_ID,
            topic="[DRY-RUN] 샘플 지원금 안내",
            category="benefits",
            summary="오케스트레이터 검증용 합성 후보 (실 데이터 아님)",
            timeliness_signal=0.9,
            practical_value_signal=0.9,
            population_reach_signal=0.8,
            verification_availability_signal=0.9,
            save_share_signal=0.8,
            duplication_penalty_signal=0.05,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="dryrun-c2-weak",
            topic="[DRY-RUN] 근거 부족 후보",
            category="benefits",
            summary="검증 실패 경로 테스트용 (has_authoritative_source=False)",
            timeliness_signal=0.3,
            practical_value_signal=0.3,
            population_reach_signal=0.3,
            verification_availability_signal=0.2,
            save_share_signal=0.2,
            duplication_penalty_signal=0.1,
            has_authoritative_source=False,
        ),
        TopicCandidate(
            candidate_id="dryrun-c3",
            topic="[DRY-RUN] 후보 3",
            category="finance_savings",
            summary="스코어링 다양성 확보용",
            timeliness_signal=0.5,
            practical_value_signal=0.5,
            population_reach_signal=0.5,
            verification_availability_signal=0.5,
            save_share_signal=0.5,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="dryrun-c4",
            topic="[DRY-RUN] 후보 4",
            category="scam_prevention",
            summary="스코어링 다양성 확보용",
            timeliness_signal=0.4,
            practical_value_signal=0.4,
            population_reach_signal=0.4,
            verification_availability_signal=0.4,
            save_share_signal=0.4,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="dryrun-c5",
            topic="[DRY-RUN] 후보 5",
            category="health_insurance",
            summary="스코어링 다양성 확보용",
            timeliness_signal=0.2,
            practical_value_signal=0.2,
            population_reach_signal=0.2,
            verification_availability_signal=0.3,
            save_share_signal=0.2,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
    ]

    src = Source(
        source_id="dryrun-src-1",
        url="https://example.gov.kr/dryrun-notice",
        source_type=SourceType.GOVERNMENT,
        publisher="[DRY-RUN] 샘플 기관",
        published_at=run_date,
        retrieved_at=run_date,
    )
    claims = [
        Claim("c-elig", ClaimType.ELIGIBILITY, "[DRY-RUN] 자격 요건 샘플 문장", ["dryrun-src-1"], run_date, VerificationStatus.VERIFIED),
        Claim("c-amount", ClaimType.AMOUNT, "[DRY-RUN] 지원 금액 샘플 문장", ["dryrun-src-1"], run_date, VerificationStatus.VERIFIED),
        Claim("c-deadline", ClaimType.DEADLINE, "[DRY-RUN] 마감 샘플 문장", ["dryrun-src-1"], run_date, VerificationStatus.VERIFIED),
        Claim("c-method", ClaimType.ACTION_METHOD, "[DRY-RUN] 신청 방법 샘플 문장", ["dryrun-src-1"], run_date, VerificationStatus.VERIFIED),
    ]
    fact_sheet = FactSheet(
        content_id=f"dryrun-{run_date}",
        topic="[DRY-RUN] 샘플 지원금 안내",
        reader_value="[DRY-RUN] 샘플 리더 가치 문장",
        affected_audience="[DRY-RUN] 샘플 대상",
        event_or_policy="[DRY-RUN] 샘플 정책",
        why_it_matters="[DRY-RUN] 샘플 시급성 문장",
        eligibility=claims[0].text,
        exclusions="[DRY-RUN] 샘플 제외 항목",
        amount_or_benefit=claims[1].text,
        deadline=claims[2].text,
        action_steps=["[DRY-RUN] 1단계", "[DRY-RUN] 2단계"],
        required_documents=["[DRY-RUN] 서류"],
        exceptions_and_warnings=["[DRY-RUN] 주의사항"],
        claims=claims,
        sources=[src],
        image_rights="[DRY-RUN] 자체 제작",
        verified_at=run_date,
        volatile_fields=[],
    )
    return ResearchBundle(candidates=candidates, fact_sheets_by_candidate={DRY_RUN_CANDIDATE_ID: fact_sheet})
