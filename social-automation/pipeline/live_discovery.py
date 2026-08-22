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

# Each entry's list_url/extractor were verified live against the real page
# before being added (dates below reflect that verification day).
# extractor(html) -> list of {"id", "title", "published_date" (YYYY-MM-DD), "url"}.


def _extract_nhis(html: str) -> list:
    """국민건강보험공단 (건강보험) -- verified 2026-08-22."""
    row_re = re.compile(r'<tr class="">(.*?)</tr>', re.S)
    id_re = re.compile(r"articleNo=(\d+)&amp;article\.offset")
    title_re = re.compile(r'class="a-link" title="([^"]*?)\s*자세히 보기"')
    date_re = re.compile(r">(\d{4}\.\d{2}\.\d{2})<")

    items = []
    for row in row_re.findall(html):
        m_id, m_title, m_date = id_re.search(row), title_re.search(row), date_re.search(row)
        if m_id and m_title and m_date:
            items.append(
                {
                    "id": m_id.group(1),
                    "title": m_title.group(1).strip(),
                    "published_date": m_date.group(1).replace(".", "-"),
                    "url": f"https://www.nhis.or.kr/nhis/together/wbhaea01700m01.do?mode=view&articleNo={m_id.group(1)}",
                }
            )
    return items


def _extract_fss(html: str) -> list:
    """금융감독원 보도자료 (금융/소비자, 사기/피해예방) -- verified 2026-08-22."""
    row_re = re.compile(r'<tr>\s*<td class="num">(.*?)</tr>', re.S)
    title_re = re.compile(r'<td class="title"><a href="([^"]+)">([^<]+)</a></td>')
    date_re = re.compile(r"<td>\s*(\d{4}-\d{2}-\d{2})\s*</td>")
    id_re = re.compile(r"nttId=(\d+)")

    items = []
    for row in row_re.findall(html):
        m_title, m_date = title_re.search(row), date_re.search(row)
        if m_title and m_date:
            href, title = m_title.group(1), m_title.group(2).strip()
            m_id = id_re.search(href)
            items.append(
                {
                    "id": m_id.group(1) if m_id else href,
                    "title": title,
                    "published_date": m_date.group(1),
                    "url": "https://www.fss.or.kr" + href if href.startswith("/") else href,
                }
            )
    return items


def _extract_nts(html: str) -> list:
    """국세청 보도자료 (세금) -- verified 2026-08-22."""
    row_re = re.compile(r"<tr>(.*?)</tr>", re.S)
    title_re = re.compile(r'data-id="(\d+)"[^>]*title="([^"]+)"\s*class="nttInfoBtn"')
    date_re = re.compile(r'data-table="date">(\d{4})\.(\d{2})\.(\d{2})\.')

    items = []
    for row in row_re.findall(html):
        if 'data-table="subject"' not in row:
            continue
        m_title, m_date = title_re.search(row), date_re.search(row)
        if m_title and m_date:
            article_id = m_title.group(1)
            items.append(
                {
                    "id": article_id,
                    "title": m_title.group(2).strip(),
                    "published_date": "-".join(m_date.groups()),
                    "url": f"https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?nttSn={article_id}&mi=2201&bbsId=1028",
                }
            )
    return items


OFFICIAL_SOURCES = [
    {
        "board_id": "nhis-together",
        "institution": "국민건강보험공단",
        "list_url": "https://www.nhis.or.kr/nhis/together/wbhaea01700m01.do",
        "source_type": SourceType.PUBLIC_INSTITUTION,
        "category": "health_insurance",
        "extractor": _extract_nhis,
    },
    {
        "board_id": "fss-press",
        "institution": "금융감독원",
        "list_url": "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218",
        "source_type": SourceType.PUBLIC_INSTITUTION,
        "category": "finance_savings",
        "extractor": _extract_fss,
    },
    {
        "board_id": "nts-press",
        "institution": "국세청",
        "list_url": "https://www.nts.go.kr/nts/na/ntt/selectNttList.do?mi=2201&bbsId=1028",
        "source_type": SourceType.GOVERNMENT,
        "category": "finance_savings",
        "extractor": _extract_nts,
    },
]

_AMOUNT_RE = re.compile(r"\d[\d,]*\s*(원|만원|억원)")
_DEADLINE_RE = re.compile(r"(까지|기한|마감|시행|부터)")
_ELIGIBILITY_RE = re.compile(r"(대상|자격|가입자|지원|납세자|기업|근로자)")


def fetch_html(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SWIPE_INFO discovery bot)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")


def fetch_body_excerpt(url: str, max_chars: int = 2000) -> str:
    """Best-effort generic plain-text pull from an article detail page --
    strips tags/scripts/styles, collapses whitespace. NOT currently used by
    has_sufficient_evidence: real NTS/FSS detail pages front-load thousands
    of chars of site navigation before the actual article body, which
    produces false-positive keyword matches unrelated to the article. Kept
    as a utility for a future site-specific content-area extractor; never
    parsed into structured fields, never fabricated if the fetch fails."""
    try:
        html = fetch_html(url)
    except Exception:
        return ""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    text = _ENTITY_RE.sub(" ", _TAG_RE.sub(" ", html))
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


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
        try:
            html = fetch_html(src["list_url"])
        except Exception:
            continue  # don't let one broken source block the others

        for item in src["extractor"](html):
            cid = f"{src['board_id']}-{item['id']}"
            source_id = f"src-{cid}"

            sources_by_id[source_id] = Source(
                source_id=source_id,
                url=item["url"],
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
                    # Directly fetchable primary source -- verification is
                    # available regardless of whether *this* item's title
                    # alone turns out to carry enough content (checked
                    # separately, in rank order, against the article body).
                    verification_availability_signal=1.0,
                    save_share_signal=0.4,
                    duplication_penalty_signal=0.1,
                    # All OFFICIAL_SOURCES entries are real government/public
                    # institutions -- that IS the authoritative-source signal;
                    # content-richness is a separate, later verification step.
                    has_authoritative_source=True,
                )
            )

    return candidates, sources_by_id, excerpts


def build_minimal_fact_sheet(candidate: TopicCandidate, source: Source, content_id: str, evidence_text: str = None) -> FactSheet:
    """Only called when has_sufficient_evidence() was true for evidence_text
    (title, or title+body-excerpt when the caller widened it). Builds a
    FactSheet strictly from that real extracted text -- no invented
    eligibility/amount/deadline detail beyond what's actually present in it."""
    text = (evidence_text or candidate.topic)[:400]
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
