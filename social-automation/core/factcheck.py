from __future__ import annotations

from dataclasses import dataclass

from core.models import Claim, QAStatus, Source, VerificationStatus


@dataclass
class ClaimValidationResult:
    claim_id: str
    status: QAStatus
    reason: str


def validate_claim(claim: Claim, sources_by_id: dict) -> ClaimValidationResult:
    linked_sources = [sources_by_id[sid] for sid in claim.source_ids if sid in sources_by_id]

    if claim.is_critical and not linked_sources:
        return ClaimValidationResult(claim.claim_id, QAStatus.FAIL, "critical claim has no linked source")

    if claim.status == VerificationStatus.CONFLICTING:
        return ClaimValidationResult(claim.claim_id, QAStatus.NEEDS_REVIEW, "authoritative sources conflict")

    if claim.is_critical:
        authoritative = [s for s in linked_sources if s.is_authoritative]
        if not authoritative:
            return ClaimValidationResult(
                claim.claim_id, QAStatus.FAIL, "critical claim lacks an authoritative source"
            )

    return ClaimValidationResult(claim.claim_id, QAStatus.PASS, "validated")


def validate_fact_sheet_claims(claims: list, sources: list):
    """Return (overall_status: QAStatus, results: list[ClaimValidationResult])."""
    sources_by_id = {s.source_id: s for s in sources}
    results = [validate_claim(c, sources_by_id) for c in claims]

    if any(r.status == QAStatus.FAIL for r in results):
        return QAStatus.FAIL, results
    if any(r.status == QAStatus.NEEDS_REVIEW for r in results):
        return QAStatus.NEEDS_REVIEW, results
    return QAStatus.PASS, results
