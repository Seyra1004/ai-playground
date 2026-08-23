from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cache import compute_hash  # noqa: E402
from core.config import load_account_config, load_brand_config  # noqa: E402
from core.database import get_connection, init_db  # noqa: E402
from core.factcheck import validate_fact_sheet_claims  # noqa: E402
from core.models import CarouselPage, QAStatus  # noqa: E402
from core.page_selector import derive_page_inputs_from_fact_sheet, select_page_count, select_page_plan  # noqa: E402
from core.payg_guard import payg_guard_active  # noqa: E402
from core.scoring import evaluate_candidate  # noqa: E402
from pipeline import daily_state, semantic_cache  # noqa: E402
from pipeline.discovery import build_dry_run_bundle, load_research_bundle  # noqa: E402
from pipeline.editorial_asset_planner import plan_content_assets  # noqa: E402
from pipeline.image_acquisition import assign_images_to_pages, discover_and_acquire_images  # noqa: E402
from pipeline.photo_acquisition import acquire_photo_for_page  # noqa: E402
from pipeline.repair import MAX_REPAIR_ATTEMPTS, repair_canonical_content  # noqa: E402
from pipeline.runner import run_pipeline  # noqa: E402
from qa.content_qa import check_real_images, check_visual_quality  # noqa: E402
from qa.render_qa import run_render_qa, verify_korean_font_available, verify_png_dimensions  # noqa: E402
from renderer.html_renderer import build_renderer_input  # noqa: E402
from renderer.png_renderer import build_contact_sheet, render_pages_to_png  # noqa: E402

RECENCY_WINDOW_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fact_sheet_to_dict(fs) -> dict:
    return {
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
    }


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_failure_report(out_dir: str, stage: str, reason: str, retry_count: int, recommended_action: str) -> None:
    _write_json(
        os.path.join(out_dir, "failure_report.json"),
        {
            "stage": stage,
            "reason": reason,
            "timestamp": _now_iso(),
            "retry_count": retry_count,
            "recommended_manual_action": recommended_action,
        },
    )


def run_daily(account_id: str, run_date: str, dry_run: bool, resume: bool) -> int:
    account = load_account_config(account_id)
    brand = load_brand_config(account.brand_config_path)

    db_path = os.path.join("data", f"{account_id}.db")
    conn = get_connection(db_path)
    init_db(conn)

    # dry-run gets its own runs-table namespace so it can never collide with
    # (or be mistaken for) a real production run on the same calendar date.
    run_tracking_account = f"{account_id}--dryrun" if dry_run else account_id
    now = _now_iso()

    print(f"ZERO_PAYG_GUARD={'active' if payg_guard_active() else 'DISABLED'}")

    out_dir = os.path.join("output", run_tracking_account, run_date)

    existing_run = daily_state.get_run(conn, run_tracking_account, run_date)
    if existing_run is not None and existing_run["status"] == "COMPLETE" and not resume:
        if os.path.isdir(out_dir):
            print(f"IDEMPOTENT_SKIP: run already COMPLETE for {run_tracking_account} on {run_date}")
            print("content_status: COMPLETE (cached)")
            print(f"output_dir: {out_dir}")
            conn.close()
            return 0

    daily_state.upsert_run(conn, run_tracking_account, run_date, status="RUNNING", started_at=now)

    # --- discovery ---
    if dry_run:
        bundle = build_dry_run_bundle(run_date)
    else:
        bundle = load_research_bundle(account_id, run_date)

    if bundle is None or not bundle.candidates:
        daily_state.upsert_run(conn, run_tracking_account, run_date, status="NEEDS_REVIEW", finished_at=_now_iso())
        _write_failure_report(
            out_dir,
            "discovery",
            f"no research bundle found for account={account_id} date={run_date}",
            0,
            "Run discovery/verification (WebSearch/WebFetch + claim evidence) for this date and save "
            f"candidates.json + fact_sheets/*.json under data/daily_input/{account_id}/{run_date}/, "
            "or rerun with --dry-run to verify the orchestrator itself.",
        )
        print("content_status: NEEDS_REVIEW (no research bundle)")
        conn.close()
        return 1

    # --- dedupe + score + recency penalty ---
    candidates = daily_state.dedupe_candidates(bundle.candidates)
    if len(candidates) < account.content.min_candidates:
        print(f"WARNING: only {len(candidates)} unique candidates (min_candidates={account.content.min_candidates})")

    recent_fps = daily_state.recent_topic_fingerprints(conn, run_tracking_account, run_date, RECENCY_WINDOW_DAYS)
    daily_state.apply_recency_penalty(candidates, recent_fps)

    # Permanent (all-time, not just RECENCY_WINDOW_DAYS) duplicate guard:
    # same source/candidate_id, exact/near-exact title, or a same-story
    # keyword-similarity near-duplicate -- runs every day forever, so
    # "recent" alone can't guarantee a topic is never repeated.
    all_time_fps = daily_state.recent_topic_fingerprints(
        conn, run_tracking_account, run_date, daily_state.PERMANENT_WINDOW_DAYS
    )
    daily_state.reject_previously_used_candidates(conn, run_tracking_account, candidates, all_time_fps)

    ranked = []
    for c in candidates:
        accepted, breakdown, reason = evaluate_candidate(c, account.content.min_score)
        ranked.append({"candidate": c, "score": breakdown.total, "accepted": accepted, "reason": reason})
    ranked.sort(key=lambda r: r["score"], reverse=True)

    breakdown_report = [
        {
            "candidate_id": r["candidate"].candidate_id,
            "topic": r["candidate"].topic,
            "score": round(r["score"], 1),
            "accepted": r["accepted"],
            "reason": r["reason"],
        }
        for r in ranked
    ]

    # --- ranked verification: investigate only until one candidate passes ---
    selected_candidate = None
    selected_fact_sheet = None
    verification_notes = []
    for r in ranked:
        if not r["accepted"]:
            continue
        c = r["candidate"]
        fs = bundle.fact_sheets_by_candidate.get(c.candidate_id)
        if fs is None:
            verification_notes.append(f"{c.candidate_id}: not investigated (no fact sheet in bundle)")
            continue
        fact_status, _results = validate_fact_sheet_claims(fs.claims, fs.sources)
        if fact_status == QAStatus.PASS:
            selected_candidate = c
            selected_fact_sheet = fs
            verification_notes.append(f"{c.candidate_id}: PASS -- selected")
            break
        verification_notes.append(f"{c.candidate_id}: {fact_status.value} -- rejected")

    if selected_candidate is None:
        daily_state.upsert_run(conn, run_tracking_account, run_date, status="NEEDS_REVIEW", finished_at=_now_iso())
        _write_json(os.path.join(out_dir, "candidates.json"), breakdown_report)
        _write_failure_report(
            out_dir,
            "ranked_verification",
            "no ranked candidate passed claim-evidence verification",
            0,
            "Review candidates.json and either supply fact_sheets for more candidates or lower min_score.",
        )
        print("content_status: NEEDS_REVIEW (no candidate verified)")
        print("verification_notes:", verification_notes)
        conn.close()
        return 1

    content_id = f"dryrun-{account_id}-{run_date}" if dry_run else f"{account_id}-{run_date}"
    selected_fact_sheet.content_id = content_id
    topic_fingerprint = daily_state.compute_topic_fingerprint(selected_candidate.topic)

    # Persist the final selection permanently (survives restarts/future
    # runs) so it's never picked again -- exact/near-exact title, same
    # candidate_id, or same-story keyword match all get rejected upstream
    # next time via reject_previously_used_candidates.
    selected_score = next((r["score"] for r in ranked if r["candidate"] is selected_candidate), 0.0)
    daily_state.record_selected_topic(
        conn, run_tracking_account, selected_candidate.candidate_id, selected_candidate.topic,
        selected_candidate.category, selected_score, "SELECTED", _now_iso(),
    )

    # --- deterministic page plan ---
    page_inputs = derive_page_inputs_from_fact_sheet(selected_fact_sheet)

    # --- semantic layer: cache-key lookup only, never regenerate here ---
    evidence_hash = compute_hash(dataclasses.asdict(selected_fact_sheet))
    account_config_hash = compute_hash(dataclasses.asdict(account))
    brand_hash = compute_hash(brand.raw)
    semantic_key = semantic_cache.compute_semantic_cache_key(evidence_hash, account_config_hash, brand_hash)
    semantic_dir = os.path.join("data", "semantic_cache", account_id)

    pages_override = None
    caption_override = None
    threads_override = None
    semantic_cache_hit = False

    if dry_run:
        # dry-run intentionally exercises the mechanical fallback assembler
        # already in pipeline.runner (same path the original --demo used) --
        # no semantic authoring needed to verify the orchestrator itself.
        pass
    else:
        semantic = semantic_cache.load_semantic_output(semantic_dir, semantic_key)
        if semantic is None:
            # Unattended ZERO-PAYG authoring attempt: the already-authenticated
            # subscription `claude` CLI, same pattern SUPER_NEWS runs in
            # production. Never falls back to a paid API on failure -- only
            # to the existing deterministic mechanical assembler.
            page_count_for_cli = select_page_count(page_inputs, account.content.pages_min, account.content.pages_max)
            page_plan_for_cli = select_page_plan(selected_fact_sheet, page_inputs, page_count_for_cli)
            try:
                from pipeline.semantic_claude_cli import SemanticCLIError, generate_semantic_output

                generated = generate_semantic_output(_fact_sheet_to_dict(selected_fact_sheet), page_plan_for_cli)
                semantic_cache.save_semantic_output(semantic_dir, semantic_key, generated)
                semantic = generated
                print("SEMANTIC_AUTHORED_BY=claude_cli")
            except Exception as exc:  # noqa: BLE001 -- any CLI failure falls back, never raises here
                print(f"SEMANTIC_CLI_FAILED={exc}")
                semantic = None

        if semantic is None:
            daily_state.upsert_run(
                conn, run_tracking_account, run_date, status="NEEDS_REVIEW",
                content_id=content_id, topic_fingerprint=topic_fingerprint, finished_at=_now_iso(),
            )
            _write_json(os.path.join(out_dir, "candidates.json"), breakdown_report)
            _write_json(os.path.join(out_dir, "fact_sheet.json"), _fact_sheet_to_dict(selected_fact_sheet))
            _write_failure_report(
                out_dir,
                "semantic_authoring",
                f"no cached semantic output for key={semantic_key} and claude CLI authoring failed",
                0,
                "Author carousel copy/caption/Threads text for this fact sheet and save via "
                "pipeline.semantic_cache.save_semantic_output(), then rerun with --resume.",
            )
            print(f"content_status: NEEDS_REVIEW (semantic authoring required, key={semantic_key})")
            conn.close()
            return 1

        semantic_cache_hit = True
        pages_override = [
            CarouselPage(
                page_number=p["page_number"],
                role=p["role"],
                headline=p["headline"],
                body=p["body"],
                visual_ref=p["visual_ref"],
                visual_data=p.get("visual_data", {}),
            )
            for p in semantic["pages"]
        ]
        caption_override = semantic["instagram_caption"]
        threads_override = semantic["threads_text"]

    print(f"SEMANTIC_CACHE_HIT={semantic_cache_hit}")

    # --- run the existing deterministic pipeline (stage-level caching/resume built in) ---
    package = run_pipeline(
        conn, account, brand, [selected_candidate], selected_fact_sheet, page_inputs, now,
        pages_override=pages_override,
        instagram_caption_override=caption_override,
        threads_text_override=threads_override,
    )

    repair_attempts = 0
    repairs_applied = []
    while package.status == "FAILED" and repair_attempts < MAX_REPAIR_ATTEMPTS:
        fixes = repair_canonical_content(package.canonical_content, package.qa_result)
        if not fixes:
            break
        repair_attempts += 1
        repairs_applied.extend(fixes)
        package = run_pipeline(
            conn, account, brand, [selected_candidate], selected_fact_sheet, page_inputs, now,
            pages_override=package.canonical_content.pages,
            instagram_caption_override=caption_override,
            threads_text_override=threads_override,
        )

    final_status = package.status
    if final_status == "FAILED":
        final_status = "NEEDS_REVIEW"  # never force COMPLETE; report instead

    # --- automatic real-image acquisition (source-agnostic; no hardcoded
    # topic/URL/filename/page number -- see pipeline/image_acquisition.py).
    # Runs before rendering so accepted images are baked into the pages
    # that actually get rendered below. Never fabricates a substitute: an
    # empty result just leaves the existing deterministic visuals in place.
    real_image_fallback = False
    real_image_fallback_reason = None
    accepted_images = []
    asset_plans = []
    if package.canonical_content is not None:
        for source in package.canonical_content.fact_sheet.sources:
            asset_dir = os.path.join(
                "data", "assets", account_id, f"src_{hashlib.sha256(source.url.encode()).hexdigest()[:12]}"
            )
            accepted_images = discover_and_acquire_images(source, asset_dir)
            if accepted_images:
                break
        if len(accepted_images) < 2:
            real_image_fallback = True
            real_image_fallback_reason = (
                "no legally-clear, sufficiently-real image found on any verified source"
                if not accepted_images
                else f"only {len(accepted_images)} suitable image(s) found (need >=2)"
            )
            print(f"REAL_IMAGE_FALLBACK=true REAL_IMAGE_FALLBACK_REASON={real_image_fallback_reason!r}")
        else:
            changed_pages = assign_images_to_pages(package.canonical_content.pages, accepted_images)
            print(f"REAL_IMAGE_PAGES_ASSIGNED={changed_pages}")

        # --- editorial asset planning + stock-photo acquisition, upstream
        # of the renderer. The plan is the single source of truth for what
        # each page's dominant visual material should be; photo acquisition
        # below consumes its (subject, query) ladder directly rather than
        # re-deriving its own -- and any page the official on-source-page
        # image pass above didn't cover is only searched here when the plan
        # says a photo would genuinely add value (photo_value HIGH/MEDIUM
        # with a distinctive subject), never as a blanket per-page search.
        asset_plans = plan_content_assets(package.canonical_content.pages, package.canonical_content.fact_sheet)
        photo_dir = os.path.join("data", "assets", account_id, f"{content_id}_photos")
        seen_hashes = set()
        for page, plan in zip(package.canonical_content.pages, asset_plans):
            if page.image_data:
                plan.asset_status = "OFFICIAL_IMAGE"
                continue
            if plan.photo_value not in ("HIGH", "MEDIUM") or not plan.concept_pairs:
                plan.asset_status = "INFO_OBJECT_USED" if plan.information_object_type != "TYPOGRAPHY" else "TYPOGRAPHY_ONLY"
                continue
            debug_log = []
            result = acquire_photo_for_page(
                page.role, page.headline, page.body, page.page_number, photo_dir, seen_hashes,
                concepts=plan.concept_pairs, debug_log=debug_log,
            )
            plan.fallback_chain = [{"debug": debug_log}] + plan.fallback_chain
            if result:
                page.image_data = {
                    "type": "real_image", "image_path": result["path"], "attribution": result["publisher"],
                }
                plan.asset_status = "PHOTO_ACQUIRED"
                print(f"P{page.page_number} PHOTO_ACQUIRED subject={plan.distinctive_subject!r}")
            else:
                plan.asset_status = "INFO_OBJECT_USED" if plan.information_object_type != "TYPOGRAPHY" else "TYPOGRAPHY_ONLY"
                print(f"P{page.page_number} NO_PHOTO -> {plan.asset_status} (subject={plan.distinctive_subject!r})")

    # --- render + post-render QA (only if we have canonical content to show) ---
    png_paths = []
    contact_sheet_path = None
    qa_render = None
    qa_png = None
    qa_font = None
    qa_visual = None
    qa_real_images = None
    if package.canonical_content is not None:
        # Pre-render gate: refuses to render at all if the host has no
        # Korean-capable font -- this is the actual root cause of broken/
        # tofu Hangul glyphs, which dimension/structural QA can never catch.
        qa_font = verify_korean_font_available()
        if qa_font.status.value == "FAIL":
            final_status = "NEEDS_REVIEW" if final_status == "COMPLETE" else final_status
            print(f"KOREAN_FONT_QA_FAILED={qa_font.checks_failed}")
        else:
            qa_visual = check_visual_quality(package.canonical_content)
            if qa_visual.status.value == "FAIL":
                final_status = "NEEDS_REVIEW" if final_status == "COMPLETE" else final_status
                print(f"VISUAL_QA_FAILED={qa_visual.checks_failed}")
            elif qa_visual.status.value == "NEEDS_REVIEW":
                final_status = "NEEDS_REVIEW" if final_status == "COMPLETE" else final_status
                print(f"VISUAL_QA_NEEDS_REVIEW={qa_visual.notes}")

            # A legitimate fallback (nothing suitable found) must never be
            # reported as a real-image FAIL -- only the minimums are
            # relaxed; any image that WAS accepted still needs valid
            # file/metadata, so this never silently reports a false PASS.
            qa_real_images = check_real_images(
                package.canonical_content,
                min_files=0 if real_image_fallback else 2,
                min_pages=0 if real_image_fallback else 2,
            )
            if qa_real_images.status.value == "FAIL":
                final_status = "NEEDS_REVIEW" if final_status == "COMPLETE" else final_status
                print(f"REAL_IMAGE_QA_FAILED={qa_real_images.checks_failed}")

            renderer_input = build_renderer_input(package.canonical_content, brand)
            qa_render = run_render_qa(renderer_input, brand)

            ig_dir = os.path.join(out_dir, "instagram")
            png_paths = render_pages_to_png(renderer_input, ig_dir)
            qa_png = verify_png_dimensions(png_paths, brand)

            contact_sheet_path = build_contact_sheet(png_paths, os.path.join(out_dir, "preview", "contact_sheet.png"))

            if qa_render.status.value == "FAIL" or qa_png.status.value == "FAIL":
                final_status = "NEEDS_REVIEW" if final_status == "COMPLETE" else final_status

    finished_at = _now_iso()
    daily_state.upsert_run(
        conn, run_tracking_account, run_date, status=final_status,
        content_id=content_id, topic_fingerprint=topic_fingerprint,
        retry_count=repair_attempts, finished_at=finished_at,
    )

    # --- final delivery directory ---
    if package.canonical_content is not None:
        fs = package.canonical_content.fact_sheet
        _write_json(os.path.join(out_dir, "candidates.json"), breakdown_report)
        _write_json(os.path.join(out_dir, "fact_sheet.json"), _fact_sheet_to_dict(fs))
        _write_json(os.path.join(out_dir, "sources.json"), [dataclasses.asdict(s) for s in fs.sources])
        _write_json(os.path.join(out_dir, "asset_plan.json"), [dataclasses.asdict(p) for p in asset_plans])

        sources_by_id = {s.source_id: s for s in fs.sources}
        _write_json(
            os.path.join(out_dir, "claim_source_map.json"),
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
                        }
                        for sid in c.source_ids
                        if sid in sources_by_id
                    ],
                }
                for c in fs.claims
            ],
        )

        with open(os.path.join(out_dir, "instagram_caption.txt"), "w", encoding="utf-8") as f:
            f.write(package.instagram_caption or "")
        with open(os.path.join(out_dir, "threads.txt"), "w", encoding="utf-8") as f:
            f.write(package.threads_text or "")

        _write_json(
            os.path.join(out_dir, "qa_report.json"),
            {
                "content_status": final_status,
                "content_qa_status": package.qa_result.status.value if package.qa_result else None,
                "checks_passed": package.qa_result.checks_passed if package.qa_result else [],
                "checks_failed": package.qa_result.checks_failed if package.qa_result else [],
                "korean_font_qa_status": qa_font.status.value if qa_font else None,
                "korean_font_qa_failed": qa_font.checks_failed if qa_font else [],
                "visual_qa_status": qa_visual.status.value if qa_visual else None,
                "visual_qa_failed": qa_visual.checks_failed if qa_visual else [],
                "visual_qa_notes": qa_visual.notes if qa_visual else [],
                "real_image_qa_status": qa_real_images.status.value if qa_real_images else None,
                "real_image_qa_passed": qa_real_images.checks_passed if qa_real_images else [],
                "real_image_qa_failed": qa_real_images.checks_failed if qa_real_images else [],
                "real_image_count": len(accepted_images),
                "real_image_fallback": real_image_fallback,
                "real_image_fallback_reason": real_image_fallback_reason,
                "render_qa_status": qa_render.status.value if qa_render else None,
                "render_checks_failed": qa_render.checks_failed if qa_render else [],
                "png_dimension_status": qa_png.status.value if qa_png else None,
                "png_dimension_checks_failed": qa_png.checks_failed if qa_png else [],
                "repair_attempts": repair_attempts,
                "repairs_applied": repairs_applied,
            },
        )

    _write_json(
        os.path.join(out_dir, "run_summary.json"),
        {
            "run_id": daily_state.make_run_id(run_tracking_account, run_date),
            "account_id": account_id,
            "run_date": run_date,
            "dry_run": dry_run,
            "content_id": content_id,
            "selected_topic": selected_candidate.topic,
            "selected_score": round(next(r["score"] for r in ranked if r["candidate"] is selected_candidate), 1),
            "verification_notes": verification_notes,
            "page_count": package.canonical_content.page_count if package.canonical_content else None,
            "page_plan": package.canonical_content.page_plan if package.canonical_content else None,
            "semantic_cache_hit": semantic_cache_hit,
            "payg_used": 0,
            "status": final_status,
            "started_at": now,
            "finished_at": finished_at,
        },
    )

    if final_status != "COMPLETE" and not os.path.isfile(os.path.join(out_dir, "failure_report.json")):
        _write_failure_report(
            out_dir,
            "qa",
            f"content/render QA did not reach PASS after {repair_attempts} repair attempt(s)",
            repair_attempts,
            "Review qa_report.json checks_failed and either fix the fact sheet/copy or re-author affected pages.",
        )

    print(f"content_status: {final_status}")
    print(f"content_id: {content_id}")
    if png_paths:
        print(f"png_count: {len(png_paths)}")
    if contact_sheet_path:
        print(f"contact_sheet: {contact_sheet_path}")
    print(f"output_dir: {out_dir}")

    conn.close()
    return 0 if final_status in ("COMPLETE", "NEEDS_REVIEW") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily SWIPE_INFO production entrypoint (no auto-publishing).")
    parser.add_argument("--account", required=True)
    parser.add_argument("--dry-run", action="store_true", help="use a synthetic fixture, no network, no real content")
    parser.add_argument("--resume", action="store_true", help="reprocess even if today's run already COMPLETE")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")
    args = parser.parse_args()

    run_date = args.date or date.today().isoformat()
    return run_daily(args.account, run_date, args.dry_run, args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
