from __future__ import annotations

from core.models import ScoreBreakdown, TopicCandidate

WEIGHTS = {
    "timeliness": 25,
    "practical_value": 25,
    "population_reach": 15,
    "verification_availability": 15,
    "save_share": 10,
    "duplication_balance": 10,
}

# Candidates at/above this duplication signal are considered near-duplicates of
# existing content and are rejected outright regardless of raw score.
DUPLICATION_REJECT_THRESHOLD = 0.85


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_candidate(candidate: TopicCandidate) -> ScoreBreakdown:
    return ScoreBreakdown(
        timeliness=_clamp(candidate.timeliness_signal) * WEIGHTS["timeliness"],
        practical_value=_clamp(candidate.practical_value_signal) * WEIGHTS["practical_value"],
        population_reach=_clamp(candidate.population_reach_signal) * WEIGHTS["population_reach"],
        verification_availability=_clamp(candidate.verification_availability_signal)
        * WEIGHTS["verification_availability"],
        save_share=_clamp(candidate.save_share_signal) * WEIGHTS["save_share"],
        duplication_balance=(1.0 - _clamp(candidate.duplication_penalty_signal)) * WEIGHTS["duplication_balance"],
    )


def evaluate_candidate(candidate: TopicCandidate, min_score: int):
    """Return (accepted: bool, breakdown: ScoreBreakdown, reason: str)."""
    breakdown = score_candidate(candidate)
    total = breakdown.total

    if candidate.duplication_penalty_signal >= DUPLICATION_REJECT_THRESHOLD:
        return False, breakdown, "rejected: strong duplication with existing content"

    if candidate.urgent:
        if not candidate.has_authoritative_source:
            return False, breakdown, "rejected: urgent override requires an authoritative source"
        return True, breakdown, "accepted via urgent override"

    if not candidate.has_authoritative_source:
        return False, breakdown, "rejected: no authoritative source available for verification"

    if total < min_score:
        return False, breakdown, f"rejected: score {total:.1f} below min_score {min_score}"

    return True, breakdown, "accepted"
