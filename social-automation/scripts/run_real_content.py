from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_account_config, load_brand_config  # noqa: E402
from core.database import get_connection, init_db  # noqa: E402
from pipeline.runner import run_pipeline  # noqa: E402
from qa.render_qa import run_render_qa  # noqa: E402
from renderer.html_renderer import build_renderer_input  # noqa: E402
from renderer.png_renderer import build_contact_sheet, render_pages_to_png  # noqa: E402
from scripts.real_content_swipe_info import (  # noqa: E402
    CONTENT_ID,
    build_real_candidates,
    build_real_fact_sheet,
    build_real_instagram_caption,
    build_real_page_inputs,
    build_real_pages,
    build_real_threads_text,
)

NOW = "2026-08-22T00:00:00Z"


def write_review_package(out_dir: str, package, breakdown_report, qa_render) -> None:
    os.makedirs(out_dir, exist_ok=True)
    fs = package.canonical_content.fact_sheet

    with open(os.path.join(out_dir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump(breakdown_report, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "fact_sheet.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "content_id": fs.content_id,
                "topic": fs.topic,
                "reader_value": fs.reader_value,
                "affected_audience": fs.affected_audience,
                "event_or_policy": fs.event_or_policy,
                "why_it_matters": fs.why_it_matters,
                "eligibility": fs.eligibility,
                "exclusions": fs.exclusions,
                "amount_or_benefit": fs.amount_or_benefit,
                "deadline": fs.deadline,
                "action_steps": fs.action_steps,
                "required_documents": fs.required_documents,
                "exceptions_and_warnings": fs.exceptions_and_warnings,
                "image_rights": fs.image_rights,
                "verified_at": fs.verified_at,
                "volatile_fields": fs.volatile_fields,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(os.path.join(out_dir, "claim_source_map.json"), "w", encoding="utf-8") as f:
        sources_by_id = {s.source_id: s for s in fs.sources}
        json.dump(
            [
                {
                    "claim_id": c.claim_id,
                    "claim_type": c.claim_type.value,
                    "text": c.text,
                    "status": c.status.value,
                    "verified_at": c.verified_at,
                    "sources": [
                        {
                            "source_id": sid,
                            "url": sources_by_id[sid].url,
                            "publisher": sources_by_id[sid].publisher,
                            "source_type": sources_by_id[sid].source_type.value,
                            "published_at": sources_by_id[sid].published_at,
                            "retrieved_at": sources_by_id[sid].retrieved_at,
                        }
                        for sid in c.source_ids
                        if sid in sources_by_id
                    ],
                }
                for c in fs.claims
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(os.path.join(out_dir, "instagram_caption.txt"), "w", encoding="utf-8") as f:
        f.write(package.instagram_caption or "")

    with open(os.path.join(out_dir, "threads_copy.txt"), "w", encoding="utf-8") as f:
        f.write(package.threads_text or "")

    with open(os.path.join(out_dir, "qa_report.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "content_status": package.status,
                "content_qa_status": package.qa_result.status.value if package.qa_result else None,
                "checks_passed": package.qa_result.checks_passed if package.qa_result else [],
                "checks_failed": package.qa_result.checks_failed if package.qa_result else [],
                "notes": package.qa_result.notes if package.qa_result else [],
                "render_qa_status": qa_render.status.value,
                "render_checks_passed": qa_render.checks_passed,
                "render_checks_failed": qa_render.checks_failed,
                "topic_selection": breakdown_report,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="swipe_info")
    args = parser.parse_args()

    account = load_account_config(args.account)
    brand = load_brand_config(account.brand_config_path)

    db_path = os.path.join("data", f"{args.account}.db")
    conn = get_connection(db_path)
    init_db(conn)

    candidates = build_real_candidates()
    fact_sheet = build_real_fact_sheet()
    page_inputs = build_real_page_inputs()
    pages = build_real_pages()
    caption = build_real_instagram_caption()
    threads_text = build_real_threads_text()

    package = run_pipeline(
        conn,
        account,
        brand,
        candidates,
        fact_sheet,
        page_inputs,
        NOW,
        pages_override=pages,
        instagram_caption_override=caption,
        threads_text_override=threads_text,
    )

    print(f"content_status: {package.status}")
    if package.status == "FAILED" or package.canonical_content is None:
        print("FAILED before render stage; not rendering PNGs.")
        conn.close()
        return 1

    canonical = package.canonical_content
    print(f"page_count: {canonical.page_count}")
    print(f"page_plan: {canonical.page_plan}")
    print(f"qa_status: {package.qa_result.status.value}")
    if package.qa_result.checks_failed:
        print(f"qa_checks_failed: {package.qa_result.checks_failed}")

    content_dir = os.path.join("data", CONTENT_ID)
    ig_dir = os.path.join(content_dir, "instagram")

    renderer_input = build_renderer_input(canonical, brand)
    qa_render = run_render_qa(renderer_input, brand)
    print(f"render_qa_status: {qa_render.status.value}")
    if qa_render.checks_failed:
        print(f"render_qa_checks_failed: {qa_render.checks_failed}")

    png_paths = render_pages_to_png(renderer_input, ig_dir)
    for p in png_paths:
        print(f"rendered: {p}")

    contact_sheet_path = build_contact_sheet(png_paths, os.path.join(ig_dir, "..", "preview", "contact_sheet.png"))
    print(f"contact_sheet: {contact_sheet_path}")

    from core.scoring import evaluate_candidate

    breakdown_report = []
    for c in candidates:
        accepted, breakdown, reason = evaluate_candidate(c, account.content.min_score)
        breakdown_report.append(
            {"candidate_id": c.candidate_id, "topic": c.topic, "score": round(breakdown.total, 1), "accepted": accepted, "reason": reason}
        )
    breakdown_report.sort(key=lambda r: r["score"], reverse=True)

    write_review_package(content_dir, package, breakdown_report, qa_render)
    print(f"review_package: {content_dir}")

    conn.close()
    return 0 if package.status in ("COMPLETE", "NEEDS_REVIEW") and qa_render.status.value != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
