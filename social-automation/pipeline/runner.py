from __future__ import annotations

import dataclasses
import sqlite3

from core.cache import compute_hash
from core.config import AccountConfig, BrandConfig
from core.factcheck import validate_fact_sheet_claims
from core.models import (
    CanonicalContent,
    CarouselPage,
    ContentPackage,
    FactSheet,
    QAStatus,
    TopicCandidate,
)
from core.page_selector import PageCountInputs, select_page_count, select_page_plan
from core.scoring import evaluate_candidate
from pipeline.state import run_stage
from platforms.instagram.adapter import build_instagram_content
from platforms.threads.adapter import build_threads_content
from qa.content_qa import run_content_qa
from qa.render_qa import run_render_qa
from renderer.html_renderer import build_renderer_input

# Deterministic, non-LLM mapping from a page role to the fact-sheet fields
# that fill it. Real platform copywriting (LLM-generated prose) is explicit
# future work; this mechanically assembles page text straight from verified
# fact-sheet fields so the pipeline/adapters/QA contract can be exercised now.
_ROLE_FIELD_MAP = {
    "hook": (
        lambda fs: fs.reader_value,
        lambda fs: fs.amount_or_benefit or fs.why_it_matters,
        "hook_visual",
    ),
    "why_now": (lambda fs: fs.why_it_matters, lambda fs: fs.deadline, "deadline_visual"),
    "eligibility": (
        lambda fs: f"대상: {fs.affected_audience}",
        lambda fs: fs.eligibility,
        "eligibility_checklist_visual",
    ),
    "amount": (lambda fs: "혜택 금액", lambda fs: fs.amount_or_benefit, "amount_chart_visual"),
    "conditions": (lambda fs: "확인 조건", lambda fs: fs.eligibility, "conditions_visual"),
    "comparison": (lambda fs: "비교", lambda fs: fs.amount_or_benefit, "comparison_visual"),
    "exclusions": (lambda fs: "제외 대상", lambda fs: fs.exclusions, "exclusions_visual"),
    "procedure": (
        lambda fs: "신청 방법",
        lambda fs: " / ".join(fs.action_steps),
        "procedure_steps_visual",
    ),
    "warnings": (
        lambda fs: "주의사항",
        lambda fs: " / ".join(fs.exceptions_and_warnings),
        "warning_visual",
    ),
    "examples": (lambda fs: "이런 분들이 해당돼요", lambda fs: fs.reader_value, "example_visual"),
    "cta": (
        lambda fs: "지금 확인하세요",
        lambda fs: " / ".join(fs.action_steps) if fs.action_steps else "지금 확인하세요",
        "cta_visual",
    ),
}


def assemble_pages_from_fact_sheet(fact_sheet: FactSheet, page_plan: list) -> list:
    pages = []
    for i, role in enumerate(page_plan, start=1):
        if role not in _ROLE_FIELD_MAP:
            raise ValueError(f"no content mapping defined for page role '{role}'")
        headline_fn, body_fn, visual_ref = _ROLE_FIELD_MAP[role]
        headline = headline_fn(fact_sheet)
        body = body_fn(fact_sheet)
        if not headline or not body:
            raise ValueError(
                f"cannot assemble page {i} (role='{role}'): fact sheet lacks required content "
                "(generation has not occurred for this role)"
            )
        pages.append(CarouselPage(page_number=i, role=role, headline=headline, body=body, visual_ref=visual_ref))
    return pages


def select_best_candidate(candidates: list, min_score: int):
    evaluated = []
    reasons = {}
    for c in candidates:
        accepted, breakdown, reason = evaluate_candidate(c, min_score)
        reasons[c.candidate_id] = reason
        if accepted:
            evaluated.append((c, breakdown))
    if not evaluated:
        raise ValueError(f"no candidate passed scoring/verification gate; reasons={reasons}")
    evaluated.sort(key=lambda pair: pair[1].total, reverse=True)
    return evaluated[0]


def _candidate_summary(c: TopicCandidate) -> dict:
    return dataclasses.asdict(c)


def run_pipeline(
    conn: sqlite3.Connection,
    account: AccountConfig,
    brand: BrandConfig,
    candidates: list,
    fact_sheet: FactSheet,
    page_inputs: PageCountInputs,
    now: str,
    pages_override: list = None,
    instagram_caption_override: str = None,
    threads_text_override: str = None,
) -> ContentPackage:
    """Run the full pipeline. Optional *_override params carry pre-authored
    semantic-layer output (real editorial copy for pages/caption/Threads text)
    so the same stage/cache/QA/render machinery can be reused for real content
    instead of the mechanical fact-sheet-field assembly used by --demo runs.
    """
    account_id = account.account_id
    content_id = fact_sheet.content_id

    # --- stage: topic_scoring ---
    def _do_scoring():
        best_candidate, breakdown = select_best_candidate(candidates, account.content.min_score)
        return {
            "selected_candidate_id": best_candidate.candidate_id,
            "score_total": breakdown.total,
        }

    scoring_input = {
        "candidates": [_candidate_summary(c) for c in candidates],
        "min_score": account.content.min_score,
    }
    scoring_result, _ = run_stage(conn, account_id, content_id, "topic_scoring", scoring_input, _do_scoring, now)
    best_candidate, breakdown = select_best_candidate(candidates, account.content.min_score)

    # --- stage: fact_validation ---
    def _do_fact_validation():
        status, results = validate_fact_sheet_claims(fact_sheet.claims, fact_sheet.sources)
        return {"status": status.value, "claim_count": len(results)}

    fact_input = {
        "claims": [dataclasses.asdict(c) for c in fact_sheet.claims],
        "sources": [dataclasses.asdict(s) for s in fact_sheet.sources],
    }
    fact_result, _ = run_stage(conn, account_id, content_id, "fact_validation", fact_input, _do_fact_validation, now)
    fact_status = QAStatus(fact_result["status"])

    if fact_status == QAStatus.FAIL:
        return ContentPackage(
            account_id=account_id,
            content_id=content_id,
            canonical_content=None,
            instagram_caption=None,
            threads_text=None,
            qa_result=None,
            status="FAILED",
        )

    # --- stage: page_selection ---
    def _do_page_selection():
        page_count = select_page_count(page_inputs, account.content.pages_min, account.content.pages_max)
        page_plan = select_page_plan(fact_sheet, page_inputs, page_count)
        return {"page_count": page_count, "page_plan": page_plan}

    page_selection_input = {
        "page_inputs": dataclasses.asdict(page_inputs),
        "pages_min": account.content.pages_min,
        "pages_max": account.content.pages_max,
        "fact_sheet_fields_present": {
            "eligibility": bool(fact_sheet.eligibility),
            "amount_or_benefit": bool(fact_sheet.amount_or_benefit),
            "exclusions": bool(fact_sheet.exclusions),
            "action_steps": bool(fact_sheet.action_steps),
            "exceptions_and_warnings": bool(fact_sheet.exceptions_and_warnings),
        },
    }
    page_selection_result, _ = run_stage(
        conn, account_id, content_id, "page_selection", page_selection_input, _do_page_selection, now
    )
    page_plan = page_selection_result["page_plan"]
    # select_page_plan can legitimately return fewer roles than the target
    # page_count when the fact sheet doesn't have enough distinct content to
    # fill it (it never pads with invented roles). page_count must reflect
    # what's actually being delivered, or downstream adapters correctly
    # reject the resulting canonical content as inconsistent.
    page_count = len(page_plan)

    # --- stage: canonical_content ---
    if pages_override is not None:
        override_roles = [p.role for p in pages_override]
        if override_roles != page_plan:
            raise ValueError(
                f"pages_override roles {override_roles} do not match the deterministic page_plan {page_plan}"
            )
        pages = pages_override
    else:
        pages = assemble_pages_from_fact_sheet(fact_sheet, page_plan)

    canonical = CanonicalContent(
        content_id=content_id,
        fact_sheet=fact_sheet,
        page_count=page_count,
        page_plan=page_plan,
        pages=pages,
    )

    # Computed once and reused as part of every downstream stage's cache key.
    # Two calls can share an identical page_plan (same roles) while the page
    # *content* differs (e.g. a mechanical QA repair rewrote a page body) --
    # without this, the adapter/render/qa stages would wrongly cache-hit a
    # stale result keyed only on the unchanged role list.
    pages_content_hash = compute_hash([dataclasses.asdict(p) for p in pages])

    def _do_canonical_content():
        return {"page_count": len(pages), "roles": [p.role for p in pages]}

    run_stage(
        conn,
        account_id,
        content_id,
        "canonical_content",
        {"page_plan": page_plan, "fact_sheet_topic": fact_sheet.topic, "pages_content_hash": pages_content_hash},
        _do_canonical_content,
        now,
    )

    # --- stage: instagram_adapter ---
    instagram_content = None
    if account.platforms.instagram:
        def _do_instagram():
            ig = build_instagram_content(canonical, brand, caption=instagram_caption_override)
            canonical.instagram = ig
            return {"caption_len": len(ig.caption), "page_count": len(ig.pages)}

        run_stage(
            conn,
            account_id,
            content_id,
            "instagram_adapter",
            {"pages_content_hash": pages_content_hash, "caption_override": instagram_caption_override},
            _do_instagram,
            now,
        )
        instagram_content = canonical.instagram

    # --- stage: threads_adapter ---
    threads_content = None
    if account.platforms.threads:
        def _do_threads():
            th = build_threads_content(canonical, text=threads_text_override)
            canonical.threads = th
            return {"text_len": len(th.text)}

        run_stage(
            conn,
            account_id,
            content_id,
            "threads_adapter",
            {"pages_content_hash": pages_content_hash, "threads_text_override": threads_text_override},
            _do_threads,
            now,
        )
        threads_content = canonical.threads

    # --- stage: renderer_input ---
    renderer_input = None
    if instagram_content is not None:
        def _do_renderer_input():
            return build_renderer_input(canonical, brand)

        renderer_input, _ = run_stage(
            conn,
            account_id,
            content_id,
            "renderer_input",
            {"pages_content_hash": pages_content_hash},
            _do_renderer_input,
            now,
        )

    # --- stage: qa ---
    def _do_qa():
        qa_content = run_content_qa(
            canonical,
            instagram_content,
            account.content.pages_min,
            account.content.pages_max,
            threads_text=threads_text_override or (threads_content.text if threads_content else ""),
        )
        qa_render = (
            run_render_qa(renderer_input, brand)
            if renderer_input is not None
            else run_render_qa([], brand)
        )
        overall = QAStatus.PASS
        if qa_content.status == QAStatus.FAIL or qa_render.status == QAStatus.FAIL:
            overall = QAStatus.FAIL
        elif qa_content.status == QAStatus.NEEDS_REVIEW or qa_render.status == QAStatus.NEEDS_REVIEW:
            overall = QAStatus.NEEDS_REVIEW
        return {
            "overall": overall.value,
            "content_checks_failed": qa_content.checks_failed,
            "render_checks_failed": qa_render.checks_failed,
            "content_checks_passed": qa_content.checks_passed,
            "notes": qa_content.notes,
        }

    qa_summary, _ = run_stage(
        conn, account_id, content_id, "qa", {"pages_content_hash": pages_content_hash}, _do_qa, now
    )

    from core.models import QAResult

    qa_result = QAResult(
        status=QAStatus(qa_summary["overall"]),
        checks_passed=qa_summary["content_checks_passed"],
        checks_failed=qa_summary["content_checks_failed"] + qa_summary["render_checks_failed"],
        notes=qa_summary["notes"],
    )

    if qa_result.status == QAStatus.FAIL:
        pkg_status = "FAILED"
    elif qa_result.status == QAStatus.NEEDS_REVIEW or fact_status == QAStatus.NEEDS_REVIEW:
        pkg_status = "NEEDS_REVIEW"
    else:
        pkg_status = "COMPLETE"

    return ContentPackage(
        account_id=account_id,
        content_id=content_id,
        canonical_content=canonical,
        instagram_caption=instagram_content.caption if instagram_content else None,
        threads_text=threads_content.text if threads_content else None,
        qa_result=qa_result,
        status=pkg_status,
    )
