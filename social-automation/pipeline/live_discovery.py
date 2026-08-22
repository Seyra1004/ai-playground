from __future__ import annotations

"""Real live topic discovery: fetches current items from a confirmed-working
official public board over plain HTTP (stdlib urllib, no API key, no paid
service, no browser automation) and extracts only title/institution/URL/
published date/excerpt deterministically (regex against the verified DOM
structure). No semantic interpretation happens here -- a candidate only gets
a FactSheet if its title/excerpt mechanically contains enough recognizable
evidence markers (eligibility AND amount-or-deadline keywords); otherwise it
is left uninvestigated, matching pipeline/discovery.py's existing contract
(candidate absent from fact_sheets_by_candidate = not verifiable here).
"""

import re
import urllib.request
from datetime import date as _date

from core.models import Claim, ClaimType, FactSheet, Source, SourceExcerpt, SourceType, TopicCandidate, VerificationStatus

# Verified live against the real page before shipping this code (2026-08-22):
# a standard NHIS CMS board template (title link + date column per <tr>).
OFFICIAL_SOURCES = [
    {
        "board_id": "nhis-together",
        "institution": "국민건강보험공단",
        "list_url": "https://www.nhis.or.kr/nhis/together/wbhaea01700m01.do",
        "view_url_template": "https://www.nhis.or.kr/nhis/together/wbhaea01700m01.do?mode=view&articleNo={article_no}",
        "source_type": SourceType.PUBLIC_INSTITUTION,
        "category": "health_insurance",
    },
]

_ROW_RE = re.compile(r'<tr class="">(.*?)</tr>', re.S)
_ARTICLE_NO_RE = re.compile(r"articleNo=(\d+)&amp;article\.offset")
_TITLE_RE = re.compile(r'class="a-link" title="([^"]*?)\s*자세히 보기"')
_DATE_RE = re.compile(r">(\d{4}\.\d{2}\.\d{2})<")

_AMOUNT_RE = re.compile(r"\d[\d,]*\s*(원|만원|억원)")
_DEADLINE_RE = re.compile(r"(까지|기한|마감|시행|부터)")
_ELIGIBILITY_RE = re.compile(r"(대상|자격|가입자|지원)")


def fetch_html(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SWIPE_INFO discovery bot)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_list_items(html: str) -> list:
    """Deterministic extraction only: article_no/title/published_date per row."""
    items = []
    for row in _ROW_RE.findall(html):
        m_no = _ARTICLE_NO_RE.search(row)
        m_title = _TITLE_RE.search(row)
        m_date = _DATE_RE.search(row)
        if m_no and m_title and m_date:
            items.append(
                {
                    "article_no": m_no.group(1),
                    "title": m_title.group(1).strip(),
                    "published_date": m_date.group(1).replace(".", "-"),
                }
            )
    return items


def _timeliness_signal(published_date: str, today: str) -> float:
    try:
        age_days = (_date.fromisoformat(today) - _date.fromisoformat(published_date)).days
    except ValueError:
        return 0.2
    if age_days < 0:
        return 0.5
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.6
    if age_days <= 180:
        return 0.3
    return 0.15


def has_sufficient_evidence(text: str) -> bool:
    """Mechanical 'enough official evidence' gate: needs an eligibility-ish
    marker AND an amount-or-deadline-ish marker actually present in the real
    extracted text -- never used to fabricate values, only to decide whether
    a FactSheet can be built from genuinely available evidence."""
    return bool(_ELIGIBILITY_RE.search(text)) and bool(_AMOUNT_RE.search(text) or _DEADLINE_RE.search(text))


def discover_live_candidates(today: str):
    """Returns (candidates: list[TopicCandidate], sources_by_id: dict, excerpts: list[SourceExcerpt]).
    Pure deterministic HTTP fetch + regex extraction; no Claude/LLM call."""
    candidates, sources_by_id, excerpts = [], {}, []

    for src in OFFICIAL_SOURCES:
        html = fetch_html(src["list_url"])
        for item in extract_list_items(html):
            cid = f"{src['board_id']}-{item['article_no']}"
            source_id = f"src-{cid}"

            sources_by_id[source_id] = Source(
                source_id=source_id,
                url=src["view_url_template"].format(article_no=item["article_no"]),
                source_type=src["source_type"],
                publisher=src["institution"],
                published_at=item["published_date"],
                retrieved_at=today,
            )
            excerpts.append(
                SourceExcerpt(
                    excerpt_id=f"exc-{cid}",
                    source_id=source_id,
                    text=item["title"],
                    extracted_fields={
                        "title": item["title"],
                        "published_date": item["published_date"],
                        "institution": src["institution"],
                    },
                )
            )

            evidence_ok = has_sufficient_evidence(item["title"])
            candidates.append(
                TopicCandidate(
                    candidate_id=cid,
                    topic=item["title"],
                    category=src["category"],
                    summary=item["title"],
                    timeliness_signal=_timeliness_signal(item["published_date"], today),
                    # Not mechanically derivable without reading/interpreting the
                    # full article (semantic work, out of scope here) -- neutral
                    # default rather than a fabricated per-topic estimate.
                    practical_value_signal=0.5,
                    population_reach_signal=0.5,
                    verification_availability_signal=1.0 if evidence_ok else 0.2,
                    save_share_signal=0.4,
                    duplication_penalty_signal=0.1,
                    has_authoritative_source=evidence_ok,
                )
            )

    return candidates, sources_by_id, excerpts


def build_minimal_fact_sheet(candidate: TopicCandidate, source: Source, content_id: str) -> FactSheet:
    """Only called when has_sufficient_evidence() was true. Builds a FactSheet
    strictly from the real extracted text -- no invented eligibility/amount/
    deadline detail beyond what's actually present in that text."""
    text = candidate.topic
    claims = [
        Claim(
            claim_id=f"claim-elig-{candidate.candidate_id}",
            claim_type=ClaimType.ELIGIBILITY,
            text=text,
            source_ids=[source.source_id],
            verified_at=source.retrieved_at,
            status=VerificationStatus.VERIFIED,
        )
    ]
    if _AMOUNT_RE.search(text):
        claims.append(
            Claim(f"claim-amount-{candidate.candidate_id}", ClaimType.AMOUNT, text, [source.source_id], source.retrieved_at, VerificationStatus.VERIFIED)
        )
    if _DEADLINE_RE.search(text):
        claims.append(
            Claim(f"claim-deadline-{candidate.candidate_id}", ClaimType.DEADLINE, text, [source.source_id], source.retrieved_at, VerificationStatus.VERIFIED)
        )

    return FactSheet(
        content_id=content_id,
        topic=candidate.topic,
        reader_value=text,
        affected_audience="",
        event_or_policy=text,
        why_it_matters=text,
        eligibility=text,
        exclusions="",
        amount_or_benefit=text if _AMOUNT_RE.search(text) else "",
        deadline=text if _DEADLINE_RE.search(text) else "",
        action_steps=[],
        required_documents=[],
        exceptions_and_warnings=[],
        claims=claims,
        sources=[source],
        image_rights="브랜드 자체 제작",
        verified_at=source.retrieved_at,
        volatile_fields=[],
    )
