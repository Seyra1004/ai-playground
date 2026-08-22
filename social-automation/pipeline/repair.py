from __future__ import annotations

from qa.content_qa import MAX_BODY_CHARS

MAX_REPAIR_ATTEMPTS = 2


def repair_canonical_content(canonical, qa_result) -> list:
    """Apply small deterministic fixes for mechanically-fixable P0/P1
    failures only: over-dense page body, missing visual_ref. Mutates
    canonical.pages in place (also reflected in canonical.instagram.pages,
    same objects) and returns the list of fixes applied.

    Anything requiring editorial judgment (a genuinely weak headline, a
    duplicate role, a missing CTA, an exaggerated claim) is intentionally
    NOT guessed at here -- those need the semantic layer, so they are left
    for a human/NEEDS_REVIEW instead of a fabricated automatic fix.
    """
    fixes = []
    failed = set(qa_result.checks_failed) if qa_result else set()

    for page in canonical.pages:
        if any(f.startswith(f"body_too_dense:page_{page.page_number}:") for f in failed):
            if page.body and len(page.body) > MAX_BODY_CHARS:
                page.body = page.body[: MAX_BODY_CHARS - 1].rstrip() + "…"
                fixes.append(f"trimmed_body_density:page_{page.page_number}")

        if f"missing_visual_ref:page_{page.page_number}" in failed:
            if not page.visual_ref or not page.visual_ref.strip():
                page.visual_ref = f"{page.role}_visual"
                fixes.append(f"filled_missing_visual_ref:page_{page.page_number}")

    return fixes
