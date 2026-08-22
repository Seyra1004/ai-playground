from __future__ import annotations

from core.models import Claim, ClaimType, FactSheet, Source, SourceType, VerificationStatus, TopicCandidate
from core.page_selector import PageCountInputs

# Deterministic, fully synthetic fixture used ONLY for --demo runs.
# No web access, no LLM calls. Not real production content.


def build_demo_candidates() -> list:
    return [
        TopicCandidate(
            candidate_id="demo-001",
            topic="병원비 본인부담금 환급 신청",
            category="health_insurance",
            summary="과다 납부한 병원비 본인부담금을 돌려받는 방법",
            urgent=False,
            timeliness_signal=0.8,
            practical_value_signal=0.95,
            population_reach_signal=0.7,
            verification_availability_signal=0.9,
            save_share_signal=0.8,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="demo-002",
            topic="일반 건강 상식",
            category="health_insurance",
            summary="누구나 아는 상식적인 건강 정보",
            urgent=False,
            timeliness_signal=0.2,
            practical_value_signal=0.3,
            population_reach_signal=0.4,
            verification_availability_signal=0.3,
            save_share_signal=0.2,
            duplication_penalty_signal=0.2,
            has_authoritative_source=False,
        ),
        TopicCandidate(
            candidate_id="demo-003",
            topic="통신비 절약 요금제 비교",
            category="finance_savings",
            summary="불필요한 통신비 지출을 줄이는 요금제 비교",
            urgent=False,
            timeliness_signal=0.5,
            practical_value_signal=0.6,
            population_reach_signal=0.6,
            verification_availability_signal=0.6,
            save_share_signal=0.5,
            duplication_penalty_signal=0.3,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="demo-004",
            topic="기존에 다룬 병원비 환급 topic 재탕",
            category="health_insurance",
            summary="이미 다룬 내용과 거의 동일",
            urgent=False,
            timeliness_signal=0.6,
            practical_value_signal=0.6,
            population_reach_signal=0.5,
            verification_availability_signal=0.6,
            save_share_signal=0.5,
            duplication_penalty_signal=0.9,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="demo-005",
            topic="긴급 정부 지원금 마감 임박",
            category="benefits",
            summary="공식 확인 불가한 긴급 지원금 루머",
            urgent=True,
            timeliness_signal=0.9,
            practical_value_signal=0.9,
            population_reach_signal=0.8,
            verification_availability_signal=0.2,
            save_share_signal=0.7,
            duplication_penalty_signal=0.1,
            has_authoritative_source=False,
        ),
    ]


def build_demo_fact_sheet(content_id: str = "demo-content-001") -> FactSheet:
    source_nhis = Source(
        source_id="src-nhis-001",
        url="https://www.nhis.or.kr/notice/demo",
        source_type=SourceType.PUBLIC_INSTITUTION,
        publisher="국민건강보험공단",
        published_at="2026-08-01",
        retrieved_at="2026-08-22",
    )
    source_moh = Source(
        source_id="src-moh-001",
        url="https://www.mohw.go.kr/notice/demo",
        source_type=SourceType.GOVERNMENT,
        publisher="보건복지부",
        published_at="2026-08-02",
        retrieved_at="2026-08-22",
    )

    claims = [
        Claim(
            claim_id="claim-eligibility",
            claim_type=ClaimType.ELIGIBILITY,
            text="본인부담상한액을 초과해 납부한 가입자는 신청 대상이다",
            source_ids=["src-nhis-001"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-amount",
            claim_type=ClaimType.AMOUNT,
            text="초과분 전액을 환급받을 수 있다",
            source_ids=["src-nhis-001", "src-moh-001"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-deadline",
            claim_type=ClaimType.DEADLINE,
            text="환급 신청은 지급 결정일로부터 3년 이내에 해야 한다",
            source_ids=["src-nhis-001"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-documents",
            claim_type=ClaimType.REQUIRED_DOCUMENTS,
            text="신분증과 환급금 지급 계좌 사본이 필요하다",
            source_ids=["src-nhis-001"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-method",
            claim_type=ClaimType.ACTION_METHOD,
            text="국민건강보험공단 홈페이지 또는 전화로 신청할 수 있다",
            source_ids=["src-nhis-001"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
    ]

    return FactSheet(
        content_id=content_id,
        topic="병원비 본인부담금 환급 신청",
        reader_value="본인부담상한액을 넘게 낸 병원비, 신청만 하면 돌려받습니다",
        affected_audience="작년에 병원비를 많이 낸 건강보험 가입자",
        event_or_policy="본인부담상한제 사후환급금 지급",
        why_it_matters="많은 사람이 신청하지 않아 환급금을 받지 못하고 있습니다",
        eligibility="연간 본인부담금이 소득분위별 상한액을 초과한 가입자",
        exclusions="비급여 항목 및 선별급여 본인부담금은 제외",
        amount_or_benefit="상한액 초과분 전액",
        deadline="지급 결정일로부터 3년 이내",
        action_steps=[
            "국민건강보험공단 홈페이지에서 지급 대상 여부 조회",
            "신분증, 계좌 사본 준비",
            "온라인 또는 전화로 환급 신청",
        ],
        required_documents=["신분증", "본인 명의 계좌 사본"],
        exceptions_and_warnings=["비급여 항목은 환급 대상이 아님", "대리 신청 시 위임장 필요"],
        claims=claims,
        sources=[source_nhis, source_moh],
        image_rights="브랜드 자체 제작 아이콘/일러스트 사용",
        risk_flags=[],
        verified_at="2026-08-22",
        volatile_fields=["amount_or_benefit"],
    )


def build_demo_page_inputs() -> PageCountInputs:
    return PageCountInputs(
        critical_info_blocks=3,
        eligibility_conditions=2,
        exclusions_count=1,
        procedure_steps=3,
        has_comparison=False,
        volatility_risk=True,
        estimated_text_density=0.6,
    )
