from __future__ import annotations

"""ONE authoritative upstream planner for per-page visual/editorial asset
strategy.

Root-cause context: page content (role/headline/body/visual_data) is
already authored by the time this runs -- by the semantic `claude` CLI
layer (pipeline/semantic_claude_cli.py) or the mechanical fact-sheet
assembler (pipeline/runner.py) -- but until now NOTHING decided, in one
place, what a page's dominant visual material SHOULD be. Photo acquisition
(pipeline/photo_acquisition.py) re-derived its own concepts internally and
was never even called from the real daily pipeline (scripts/run_daily.py
only ran official on-source-page image discovery); the renderer inferred
its own composition strategy purely from visual_data's shape. This module
is the single source of truth those two now consume, instead of guessing
independently.

Deliberately NOT a new Claude-CLI call site: distinctive-subject/query
generation reuses the existing two-tier pipeline.photo_acquisition.
derive_concepts() (glossary first, semantic-CLI fallback only when the
glossary finds nothing) -- the same approved, already-hardened path, not a
second parallel one. Nothing here invents facts; every field is derived
from the page's own already-verified role/headline/body/visual_data.
"""

from dataclasses import dataclass, field

from pipeline.photo_acquisition import derive_concepts

# visual_data["type"] -> (primary asset family if no photo, secondary,
# semantic fallback chain). This is the same "shape signals visual
# strategy" principle renderer/html_renderer.py's V4 composer dispatch
# already applies for CSS layout -- formalized once here so acquisition
# and rendering read the same decision instead of each inferring it.
_TYPE_ASSET_MAP = {
    "stat_hero": ("TYPOGRAPHY", "DOCUMENT", ["DOCUMENT", "TYPOGRAPHY"]),
    "highlight_box": ("TYPOGRAPHY", "DOCUMENT", ["DOCUMENT", "TYPOGRAPHY"]),
    "checklist": ("CHECKLIST", "PHOTO", ["CHECKLIST", "TYPOGRAPHY"]),
    "exclusion_list": ("CHECKLIST", "PHOTO", ["CHECKLIST", "TYPOGRAPHY"]),
    "steps": ("PROCESS", "PHOTO", ["PROCESS", "CHECKLIST"]),
    "comparison": ("DATA", "PHOTO", ["DATA", "TYPOGRAPHY"]),
    "process_flow": ("PROCESS", "PHOTO", ["PROCESS", "CHECKLIST"]),
    "evidence_card": ("DOCUMENT", "PHOTO", ["DOCUMENT", "TYPOGRAPHY"]),
    "bar_chart": ("DATA", "PHOTO", ["DATA", "TYPOGRAPHY"]),
    "cta_panel": ("PROCESS", "PHOTO", ["PROCESS", "TYPOGRAPHY"]),
}
_DEFAULT_ASSET = ("TYPOGRAPHY", "", ["TYPOGRAPHY"])

# How much a photo would plausibly help THIS role, independent of whether
# one is actually found -- P1/context/eligibility pages benefit from a
# real-world visual anchor far more than a pure data-compare or CTA page
# (product rule: "DATA/COMPARE may legitimately outperform photography").
_ROLE_PHOTO_VALUE = {
    "hook": "HIGH",
    "why_now": "MEDIUM",
    "eligibility": "MEDIUM",
    "conditions": "MEDIUM",
    "examples": "MEDIUM",
    "amount": "LOW",
    "comparison": "LOW",
    "exclusions": "LOW",
    "procedure": "LOW",
    "warnings": "LOW",
    "cta": "LOW",
}

_NEGATIVE_CONCEPTS = [
    "generic office worker", "generic doctor", "generic patient", "generic hospital corridor",
    "random person", "random man's back", "generic smartphone", "generic paperwork",
    "generic hand", "generic money image", "stock business handshake",
]


@dataclass
class PageAssetPlan:
    page_number: int
    page_role: str
    primary_message: str
    supporting_facts: list = field(default_factory=list)

    primary_asset_type: str = "TYPOGRAPHY"
    secondary_asset_type: str = ""

    photo_value: str = "LOW"  # HIGH | MEDIUM | LOW
    distinctive_subject: str = ""
    search_queries: list = field(default_factory=list)
    concept_pairs: list = field(default_factory=list)  # [(subject, query), ...] -- what acquisition actually consumes
    negative_concepts: list = field(default_factory=list)

    information_object_type: str = ""
    composition_intent: str = ""
    fallback_chain: list = field(default_factory=list)
    asset_status: str = "PLANNED"  # PLANNED | PHOTO_ACQUIRED | OFFICIAL_IMAGE | INFO_OBJECT_USED | TYPOGRAPHY_ONLY


def _extract_supporting_facts(page) -> list:
    vd = page.visual_data or {}
    facts = []
    for key in ("items", "big_text", "highlight", "sub_text", "metrics", "groups", "sections"):
        val = vd.get(key)
        if isinstance(val, list):
            facts.extend(str(v)[:80] for v in val[:5])
        elif val:
            facts.append(str(val)[:80])
    return facts[:6]


def plan_page_assets(page, fact_sheet=None) -> PageAssetPlan:
    """Deterministic derivation from the page's OWN already-authored/
    verified content. The only network-adjacent step is the existing
    derive_concepts() call, and only for roles where a photo would
    plausibly add real value -- a pure DATA/CTA page never triggers one."""
    vd = page.visual_data or {}
    vd_type = vd.get("type", "")
    primary, secondary, fallback = _TYPE_ASSET_MAP.get(vd_type, _DEFAULT_ASSET)
    photo_value = _ROLE_PHOTO_VALUE.get(page.role, "LOW")

    distinctive_subject = ""
    search_queries = []
    concept_pairs = []
    if photo_value in ("HIGH", "MEDIUM"):
        concept_pairs = derive_concepts(page.headline, page.body, page.role)
        if concept_pairs:
            distinctive_subject = concept_pairs[0][0]
            search_queries = [query for _subject, query in concept_pairs]
        else:
            # No confidently-distinctive subject in this page's own verified
            # text -- NO_PHOTO is correct here, not a generic fallback search.
            photo_value = "LOW"

    return PageAssetPlan(
        page_number=page.page_number,
        page_role=page.role,
        primary_message=page.headline,
        supporting_facts=_extract_supporting_facts(page),
        primary_asset_type="PHOTO" if photo_value == "HIGH" and distinctive_subject else primary,
        secondary_asset_type=secondary,
        photo_value=photo_value,
        distinctive_subject=distinctive_subject,
        search_queries=search_queries,
        concept_pairs=concept_pairs,
        negative_concepts=list(_NEGATIVE_CONCEPTS),
        information_object_type=primary if primary in ("CHECKLIST", "DOCUMENT", "PROCESS", "DATA") else "TYPOGRAPHY",
        composition_intent=f"{page.role}:{vd_type or 'typography'}",
        fallback_chain=list(fallback),
        asset_status="PLANNED",
    )


def plan_content_assets(pages, fact_sheet=None) -> list:
    return [plan_page_assets(p, fact_sheet) for p in pages]
