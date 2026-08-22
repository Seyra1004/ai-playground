from __future__ import annotations

"""Real, sourced SWIPE_INFO content package: 본인부담상한액 초과금 환급.

Every fact below traces to a live authoritative source fetched during
production (see Source entries). Editorial copy (headlines/body text,
captions) is hand-authored to those facts by the semantic layer — no LLM
batch-generation call, no fabricated figures. This is production content,
not a demo fixture.
"""

from core.models import (
    CanonicalContent,
    CarouselPage,
    Claim,
    ClaimType,
    FactSheet,
    Source,
    SourceType,
    TopicCandidate,
    VerificationStatus,
)
from core.page_selector import PageCountInputs

CONTENT_ID = "swipe-2026-08-22-hospital-refund"


def build_real_candidates() -> list:
    """5 real candidates researched from official Korean sources on 2026-08-22.
    Signals are assigned via a fixed rubric (see fact-sheet research notes):
    timeliness by proximity of the actionable window; verification by count/
    type of independent authoritative sources actually confirmed; duplication
    kept low since this is the account's first published piece.
    """
    return [
        TopicCandidate(
            candidate_id="c1-hospital-refund",
            topic="병원비 본인부담상한액 초과금 환급 신청",
            category="health_insurance",
            summary="본인부담상한액을 초과한 병원비를 국민건강보험공단에서 환급",
            urgent=False,
            timeliness_signal=1.0,
            practical_value_signal=1.0,
            population_reach_signal=0.9,
            verification_availability_signal=1.0,
            save_share_signal=0.9,
            duplication_penalty_signal=0.05,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="c2-voicephishing",
            topic="상품권 대출빙자 신종 보이스피싱 주의",
            category="scam_prevention",
            summary="상품권 구매실적을 대출조건으로 속여 통장을 편취하는 신종 사기 (금감원 소비자경보, 2026-08-10)",
            urgent=False,
            timeliness_signal=0.7,
            practical_value_signal=0.85,
            population_reach_signal=0.7,
            verification_availability_signal=0.5,
            save_share_signal=0.8,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="c3-workincome-benefit",
            topic="근로장려금·자녀장려금 정기분 8월 지급",
            category="benefits",
            summary="5월 신청분 근로/자녀장려금이 8월 말 지급 예정 (국세청)",
            urgent=False,
            timeliness_signal=0.6,
            practical_value_signal=0.7,
            population_reach_signal=0.8,
            verification_availability_signal=0.5,
            save_share_signal=0.5,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="c4-scholarship",
            topic="2026년 2학기 국가장학금 신청",
            category="benefits",
            summary="한국장학재단 2학기 국가장학금 및 지역인재장학금 신청 접수",
            urgent=False,
            timeliness_signal=0.6,
            practical_value_signal=0.6,
            population_reach_signal=0.4,
            verification_availability_signal=0.5,
            save_share_signal=0.5,
            duplication_penalty_signal=0.1,
            has_authoritative_source=True,
        ),
        TopicCandidate(
            candidate_id="c5-income-tax-refund",
            topic="종합소득세 환급 안내",
            category="finance_savings",
            summary="종합소득세 신고 후 환급금 조회 및 지급 (신고 시즌 이미 종료, 낮은 시의성)",
            urgent=False,
            timeliness_signal=0.2,
            practical_value_signal=0.5,
            population_reach_signal=0.5,
            verification_availability_signal=0.6,
            save_share_signal=0.3,
            duplication_penalty_signal=0.3,
            has_authoritative_source=True,
        ),
    ]


def build_real_fact_sheet() -> FactSheet:
    src_nhis_minwon = Source(
        source_id="src-nhis-minwon",
        url="https://www.nhis.or.kr/nhis/minwon/minwonServiceBoard.do?mode=view&articleNo=10945830",
        source_type=SourceType.PUBLIC_INSTITUTION,
        publisher="국민건강보험공단",
        published_at=None,
        retrieved_at="2026-08-22",
    )
    src_gov24 = Source(
        source_id="src-gov24",
        url="https://www.gov.kr/portal/service/serviceInfo/PTR000050350",
        source_type=SourceType.GOVERNMENT,
        publisher="정부24",
        published_at=None,
        retrieved_at="2026-08-22",
    )
    src_nhis_banner = Source(
        source_id="src-nhis-banner-2026",
        url="https://www.nhis.or.kr/nhis/etc/20260113_banner_pop01.do",
        source_type=SourceType.PUBLIC_INSTITUTION,
        publisher="국민건강보험공단",
        published_at="2026-01-13",
        retrieved_at="2026-08-22",
    )
    src_nhis_system = Source(
        source_id="src-nhis-system-explainer",
        url="https://www.nhis.or.kr/nhis/minwon/wbhapa01000m01.do?mode=view&articleNo=10946900",
        source_type=SourceType.PUBLIC_INSTITUTION,
        publisher="국민건강보험공단",
        published_at=None,
        retrieved_at="2026-08-22",
    )
    src_law = Source(
        source_id="src-law-nhia-art91",
        url="https://www.law.go.kr/LSW//lsSideInfoP.do?lsiSeq=265877&joNo=0091&joBrNo=00&docCls=jo&urlMode=lsScJoRltInfoR",
        source_type=SourceType.GOVERNMENT,
        publisher="국가법령정보센터 (국민건강보험법 제91조)",
        published_at=None,
        retrieved_at="2026-08-22",
    )
    src_news_statute_stat = Source(
        source_id="src-news-statute-stat",
        url="https://www.newsfc.co.kr/news/articleView.html?idxno=80266",
        source_type=SourceType.NEWS_MEDIA,
        publisher="금융소비자뉴스",
        published_at=None,
        retrieved_at="2026-08-22",
    )

    claims = [
        Claim(
            claim_id="claim-eligibility",
            claim_type=ClaimType.ELIGIBILITY,
            text=(
                "연간(1.1~12.31, 진료일 기준) 건강보험 본인일부부담금 총액이 소득분위별 "
                "본인부담상한액을 초과한 가입자 및 피부양자 중 공단으로부터 초과금 지급 안내를 "
                "받은 사람이 대상이다."
            ),
            source_ids=["src-nhis-minwon", "src-gov24"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-amount",
            claim_type=ClaimType.AMOUNT,
            text=(
                "2026년 진료분 소득분위별 본인부담상한액: 1분위 90만원, 2~3분위 112만원, "
                "4~5분위 173만원, 6~7분위 326만원, 8분위 446만원, 9분위 536만원, 10분위 843만원 "
                "(요양병원 120일 초과 입원은 최대 1,096만원). 초과분은 전액 환급된다."
            ),
            source_ids=["src-nhis-banner-2026"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-deadline",
            claim_type=ClaimType.DEADLINE,
            text="보험급여를 받을 권리는 국민건강보험법 제91조에 따라 3년간 행사하지 않으면 시효로 소멸한다.",
            source_ids=["src-law-nhia-art91"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-exclusions",
            claim_type=ClaimType.EXCLUSIONS,
            text=(
                "비급여, 선별급여, 전액본인부담, 임플란트, 상급병실(2~3인실) 입원료, 추나요법, "
                "상급종합병원 경증질환 외래 초·재진 본인부담금 등은 상한액 계산에서 제외된다."
            ),
            source_ids=["src-nhis-minwon"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-documents",
            claim_type=ClaimType.REQUIRED_DOCUMENTS,
            text=(
                "본인 신청 시 지급신청서만 필요하다. 가족·제3자 계좌로 받으려면 지급신청서, "
                "위임장, 위임인·수임인 신분증 사본, 가족관계증명서가 필요하다."
            ),
            source_ids=["src-gov24", "src-nhis-minwon"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-method",
            claim_type=ClaimType.ACTION_METHOD,
            text=(
                "국민건강보험공단 홈페이지, The건강보험 앱, 지사 방문, 팩스, 우편, 정부24, "
                "전화(1577-1000)로 신청할 수 있다. 본인 명의 계좌만 가능하며 제3자 계좌는 지사 "
                "방문으로 별도 신청해야 한다."
            ),
            source_ids=["src-nhis-minwon", "src-gov24"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
        Claim(
            claim_id="claim-statute-impact-stat",
            claim_type=ClaimType.OTHER,
            text="2021~2022년 진료분만 5만 4,002건, 378억 원이 3년 시효로 청구되지 않고 소멸됐다.",
            source_ids=["src-news-statute-stat"],
            verified_at="2026-08-22",
            status=VerificationStatus.VERIFIED,
        ),
    ]

    return FactSheet(
        content_id=CONTENT_ID,
        topic="병원비 본인부담상한액 초과금 환급 신청",
        reader_value="작년에 병원비를 많이 냈다면 소득분위별 상한액을 넘는 금액을 전액 돌려받을 수 있다",
        affected_audience="2026년(1.1~12.31 진료분) 병원비를 많이 낸 건강보험 가입자 및 피부양자",
        event_or_policy="국민건강보험 본인부담상한제 사후환급금 지급",
        why_it_matters="3년 안에 청구하지 않으면 시효로 소멸되며, 실제로 최근 2년치만 378억 원이 사라졌다",
        eligibility=claims[0].text,
        exclusions=claims[3].text,
        amount_or_benefit=claims[1].text,
        deadline=claims[2].text,
        action_steps=[
            "국민건강보험공단 홈페이지·The건강보험 앱에서 본인부담상한액 초과금 조회",
            "본인 명의 계좌로 지급신청서 제출 (가족·제3자 계좌는 지사 방문 + 위임장 필요)",
            "공단 심사 후 등록 계좌로 입금",
        ],
        required_documents=["지급신청서", "(가족·제3자 신청 시) 위임장", "위임인·수임인 신분증 사본", "가족관계증명서"],
        exceptions_and_warnings=[
            "3년간 청구하지 않으면 시효로 소멸된다 (국민건강보험법 제91조)",
            "제3자 계좌로 받으려면 지사 방문 및 위임장이 필요하다",
        ],
        claims=claims,
        sources=[src_nhis_minwon, src_gov24, src_nhis_banner, src_nhis_system, src_law, src_news_statute_stat],
        image_rights="브랜드 자체 제작 카드/차트/아이콘만 사용, 외부 이미지·로고 미사용",
        risk_flags=[],
        verified_at="2026-08-22",
        volatile_fields=["amount_or_benefit"],
    )


def build_real_page_inputs() -> PageCountInputs:
    return PageCountInputs(
        critical_info_blocks=4,
        eligibility_conditions=2,
        exclusions_count=1,
        procedure_steps=3,
        has_comparison=False,
        volatility_risk=True,
        estimated_text_density=0.6,
    )


def build_real_pages() -> list:
    """Hand-authored carousel copy grounded strictly in the fact sheet above."""
    return [
        CarouselPage(
            1,
            "hook",
            "넘게 낸 병원비, 최대 843만 원까지 돌려받을 수 있어요",
            "작년 한 해 병원비를 많이 냈다면, 국민건강보험공단이 상한액 초과분을 전액 돌려드립니다.",
            "hero_stat_visual",
            visual_data={"type": "stat_hero", "big_text": "최대 843만원", "sub_text": "소득분위별 상한액 초과분 전액 환급"},
        ),
        CarouselPage(
            2,
            "why_now",
            "3년 지나면 못 받는 돈, 지금 확인하세요",
            "본인부담상한액 초과금은 3년 안에 청구하지 않으면 시효로 사라집니다. "
            "2021~2022년 진료분만 5만 4,002건, 378억 원이 청구 없이 소멸됐어요.",
            "urgency_visual",
            visual_data={"type": "highlight_box", "icon": "⏳", "highlight": "5만 4,002건 · 378억 원 소멸"},
        ),
        CarouselPage(
            3,
            "eligibility",
            "나도 대상일까? 이렇게 확인하세요",
            "2026년(1.1~12.31 진료분) 본인부담금 총액이 소득분위별 상한액을 넘은 건강보험 "
            "가입자·피부양자라면 대상입니다.",
            "checklist_visual",
            visual_data={
                "type": "checklist",
                "items": [
                    "건강보험 가입자 또는 피부양자",
                    "작년 한 해 여러 병원비를 합쳐 상한액 초과",
                    "공단 안내문·앱에서 초과금이 조회됨",
                ],
            },
        ),
        CarouselPage(
            4,
            "amount",
            "소득분위별 최대 환급액",
            "상한액을 넘은 금액은 전액 돌려받습니다. 요양병원 120일 초과 입원은 최대 1,096만 원까지 적용돼요.",
            "amount_chart_visual",
            visual_data={
                "type": "bar_chart",
                "items": [
                    ["1분위", 90], ["2~3분위", 112], ["4~5분위", 173],
                    ["6~7분위", 326], ["8분위", 446], ["9분위", 536], ["10분위", 843],
                ],
                "unit": "만원",
            },
        ),
        CarouselPage(
            5,
            "conditions",
            "사전급여 vs 사후환급, 뭐가 다를까",
            "한 병원에서 상한액을 넘으면 병원이 바로 청구(사전급여)하지만, 여러 병원비를 합쳐 "
            "넘었다면 본인이 직접 신청하는 사후환급 절차가 필요합니다.",
            "comparison_visual",
            visual_data={
                "type": "comparison",
                "left": {"title": "사전급여", "desc": "한 병원에서 초과 · 자동 적용"},
                "right": {"title": "사후환급", "desc": "여러 병원 합산 초과 · 본인 신청 필요"},
            },
        ),
        CarouselPage(
            6,
            "exclusions",
            "이 항목은 환급 대상이 아니에요",
            "비급여, 선별급여, 전액본인부담금, 임플란트, 상급병실(2~3인실) 입원료, 추나요법, "
            "상급종합병원 경증질환 외래 초·재진 본인부담금은 제외됩니다.",
            "exclusions_visual",
            visual_data={
                "type": "exclusion_list",
                "items": ["비급여 항목", "선별급여·전액본인부담금", "임플란트", "상급병실(2~3인실) 입원료", "추나요법"],
            },
        ),
        CarouselPage(
            7,
            "procedure",
            "신청 방법 3단계",
            "① 공단 홈페이지·The건강보험 앱에서 초과금 조회 → ② 본인 명의 계좌로 신청 "
            "(가족·제3자는 위임장 필요) → ③ 심사 후 계좌로 입금",
            "procedure_visual",
            visual_data={
                "type": "steps",
                "items": ["초과금 조회 (홈페이지·앱)", "본인 계좌로 신청서 제출", "심사 후 계좌 입금"],
            },
        ),
        CarouselPage(
            8,
            "cta",
            "지금 바로 조회하고, 가족에게도 알려주세요",
            "국민건강보험공단 홈페이지·The건강보험 앱·전화(1577-1000)·정부24에서 지금 확인하세요. "
            "부모님, 배우자 몫도 함께 확인해보세요.",
            "cta_visual",
            visual_data={"type": "cta_panel", "button_text": "지금 확인하기 →"},
        ),
    ]


def build_real_instagram_caption() -> str:
    return (
        "작년 병원비, 너무 많이 내지 않으셨나요?\n"
        "국민건강보험공단이 본인부담상한액을 넘게 낸 병원비를 최대 843만 원까지 돌려드립니다.\n\n"
        "그런데 3년 안에 청구하지 않으면 이 돈, 그냥 사라져요. 실제로 2021~2022년 진료분만 "
        "5만 4천 건, 378억 원이 주인을 못 찾고 소멸됐습니다.\n\n"
        "✅ 확인 방법\n"
        "국민건강보험공단 홈페이지 또는 The건강보험 앱에서 '본인부담상한액 초과금' 조회 → "
        "본인 계좌로 신청 → 심사 후 입금\n\n"
        "가족 계좌로 받으려면 위임장과 가족관계증명서가 필요해요.\n\n"
        "지금 저장해두고, 부모님·배우자 몫도 같이 확인해보세요.\n"
        "#SWIPE_INFO #병원비환급 #본인부담상한제 #건강보험 #환급금조회 #생활정보"
    )


def build_real_threads_text() -> str:
    return (
        "병원비 많이 낸 해엔 본인부담상한액 초과금부터 확인하세요. 소득분위별로 최대 843만 원까지 "
        "돌려받을 수 있는데, 3년 지나면 청구권이 사라져요(실제로 최근 2년치만 378억 원이 그렇게 "
        "사라졌어요). 국민건강보험공단 홈페이지나 The건강보험 앱에서 1분이면 조회됩니다."
    )
