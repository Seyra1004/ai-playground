from __future__ import annotations

from core.factcheck import validate_fact_sheet_claims
from core.models import CanonicalContent, InstagramContent, QAResult, QAStatus

# Configurable per-page body density guardrail for readable mobile carousel text.
MAX_BODY_CHARS = 220


def run_content_qa(
    canonical: CanonicalContent,
    instagram_content: InstagramContent,
    pages_min: int,
    pages_max: int,
) -> QAResult:
    passed = []
    failed = []
    notes = []

    page_count = len(canonical.pages)
    if pages_min <= page_count <= pages_max:
        passed.append("page_count_in_range")
    else:
        failed.append(f"page_count_out_of_range:{page_count}")

    roles_seen = []
    for page in canonical.pages:
        if not page.headline or not page.headline.strip():
            failed.append(f"missing_headline:page_{page.page_number}")
        if not page.visual_ref or not page.visual_ref.strip():
            failed.append(f"missing_visual_ref:page_{page.page_number}")
        if page.body and len(page.body) > MAX_BODY_CHARS:
            failed.append(f"body_too_dense:page_{page.page_number}:{len(page.body)}chars")
        if page.role in roles_seen:
            failed.append(f"duplicate_page_role:{page.role}")
        roles_seen.append(page.role)

    if canonical.pages:
        passed.append("headline_and_visual_checks_ran")

    if canonical.pages and canonical.pages[-1].role == "cta" and canonical.pages[-1].body.strip():
        passed.append("final_page_has_cta")
    else:
        failed.append("final_page_missing_cta")

    fact_status, _claim_results = validate_fact_sheet_claims(
        canonical.fact_sheet.claims, canonical.fact_sheet.sources
    )
    if fact_status == QAStatus.PASS:
        passed.append("source_linkage_ok")
    elif fact_status == QAStatus.NEEDS_REVIEW:
        notes.append("source_linkage_needs_review")
    else:
        failed.append("source_linkage_failed")

    if instagram_content is not None:
        if len(instagram_content.pages) == page_count:
            passed.append("instagram_page_count_matches_canonical")
        else:
            failed.append("instagram_page_count_mismatch")

    if failed:
        status = QAStatus.FAIL
    elif fact_status == QAStatus.NEEDS_REVIEW:
        status = QAStatus.NEEDS_REVIEW
    else:
        status = QAStatus.PASS

    return QAResult(status=status, checks_passed=passed, checks_failed=failed, notes=notes)


def check_visual_quality(canonical: CanonicalContent) -> QAResult:
    """Deterministic visual-relevance QA: a page with no visual_data at all
    -> FAIL, the exact same visual_data reused verbatim on 2+ pages -> FAIL
    (a real evidence card/chart/diagram should never be identical across
    pages), and a visual whose own text shares no token with its page's
    headline/body -> NEEDS_REVIEW (a deterministic proxy for "this doesn't
    look related to the page" -- not a semantic judgment call)."""
    import json as _json

    passed, failed, notes = [], [], []
    seen_signatures = {}

    for page in canonical.pages:
        vd = page.visual_data or {}
        if not vd or not vd.get("type"):
            failed.append(f"missing_visual:page_{page.page_number}")
            continue

        signature = _json.dumps(vd, sort_keys=True, ensure_ascii=False)
        if signature in seen_signatures:
            failed.append(f"duplicate_visual:page_{page.page_number}_matches_page_{seen_signatures[signature]}")
        else:
            seen_signatures[signature] = page.page_number

        visual_strings = []
        for v in vd.values():
            if isinstance(v, (str, int, float)):
                visual_strings.append(str(v))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        visual_strings.extend(str(x) for x in item.values())
                    else:
                        visual_strings.append(str(item))
        visual_tokens = {t for s in visual_strings for t in s.replace(",", " ").split() if len(t) >= 2}
        page_tokens = {t for t in f"{page.headline} {page.body}".replace(",", " ").split() if len(t) >= 2}
        if page_tokens and visual_tokens and not (page_tokens & visual_tokens):
            notes.append(f"visual_relevance_uncertain:page_{page.page_number}")

    if canonical.pages:
        passed.append("visual_presence_checked")

    if failed:
        status = QAStatus.FAIL
    elif notes:
        status = QAStatus.NEEDS_REVIEW
    else:
        status = QAStatus.PASS

    return QAResult(status=status, checks_passed=passed, checks_failed=failed, notes=notes)
