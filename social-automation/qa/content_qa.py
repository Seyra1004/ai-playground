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
