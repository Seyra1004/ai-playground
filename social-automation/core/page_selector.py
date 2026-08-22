from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PageCountInputs:
    critical_info_blocks: int
    eligibility_conditions: int
    exclusions_count: int
    procedure_steps: int
    has_comparison: bool
    volatility_risk: bool
    estimated_text_density: float  # 0-1, higher = denser


_CONDITION_SPLIT_RE = None  # set lazily below to avoid an import at module load if unused


def derive_page_inputs_from_fact_sheet(fact_sheet) -> "PageCountInputs":
    """Deterministically derive PageCountInputs straight from a verified
    FactSheet's own fields -- no per-topic hand-tuning, no Claude judgment.
    Used by the daily orchestrator so page-count complexity scales with what
    the fact sheet actually contains."""
    import re

    global _CONDITION_SPLIT_RE
    if _CONDITION_SPLIT_RE is None:
        _CONDITION_SPLIT_RE = re.compile(r"[,·;]|그리고|또는")

    critical_info_blocks = sum(
        1
        for v in (fact_sheet.eligibility, fact_sheet.amount_or_benefit, fact_sheet.exclusions, fact_sheet.action_steps)
        if v
    )
    eligibility_conditions = max(1, len(_CONDITION_SPLIT_RE.split(fact_sheet.eligibility or "")))
    exclusions_count = len(_CONDITION_SPLIT_RE.split(fact_sheet.exclusions)) if fact_sheet.exclusions else 0
    procedure_steps = len(fact_sheet.action_steps or [])
    has_comparison = any(kw in (fact_sheet.event_or_policy or "") for kw in ("비교", " vs ", "대비"))
    volatility_risk = bool(fact_sheet.volatile_fields)
    density_chars = sum(
        len(v or "") for v in (fact_sheet.eligibility, fact_sheet.amount_or_benefit, fact_sheet.exclusions)
    )
    estimated_text_density = min(1.0, density_chars / 600)

    return PageCountInputs(
        critical_info_blocks=critical_info_blocks,
        eligibility_conditions=eligibility_conditions,
        exclusions_count=exclusions_count,
        procedure_steps=procedure_steps,
        has_comparison=has_comparison,
        volatility_risk=volatility_risk,
        estimated_text_density=estimated_text_density,
    )


def select_page_count(inputs: PageCountInputs, pages_min: int, pages_max: int) -> int:
    complexity = 0
    complexity += inputs.critical_info_blocks
    complexity += max(0, inputs.eligibility_conditions - 1)
    complexity += inputs.exclusions_count
    complexity += max(0, inputs.procedure_steps - 1)
    complexity += 1 if inputs.has_comparison else 0
    complexity += 1 if inputs.volatility_risk else 0
    complexity += round(inputs.estimated_text_density * 2)

    # Base of 4 pages (hook + why-now + one content page + cta); one extra
    # page per two complexity units, clamped to the account's allowed range.
    page_count = 4 + (complexity // 2)
    return max(pages_min, min(pages_max, page_count))


def select_page_plan(fact_sheet, page_inputs: PageCountInputs, page_count: int) -> list:
    """Choose which content-page roles fill the pages between hook/why_now and cta.

    Only roles the fact sheet actually has content for are used, so weak
    topics are never padded and dense topics never get facts silently cut.
    """
    candidate_roles = []
    if fact_sheet.eligibility:
        candidate_roles.append("eligibility")
    if fact_sheet.amount_or_benefit:
        candidate_roles.append("amount")
    if page_inputs.eligibility_conditions > 1:
        candidate_roles.append("conditions")
    if page_inputs.has_comparison:
        candidate_roles.append("comparison")
    if fact_sheet.exclusions:
        candidate_roles.append("exclusions")
    if fact_sheet.action_steps:
        candidate_roles.append("procedure")
    if fact_sheet.exceptions_and_warnings:
        candidate_roles.append("warnings")

    middle_slots = max(0, page_count - 3)  # hook, why_now, cta are reserved
    middle_roles = []
    seen = set()
    for role in candidate_roles:
        if len(middle_roles) >= middle_slots:
            break
        if role not in seen:
            middle_roles.append(role)
            seen.add(role)

    if len(middle_roles) < middle_slots and "examples" not in seen:
        middle_roles.append("examples")

    return ["hook", "why_now"] + middle_roles + ["cta"]
