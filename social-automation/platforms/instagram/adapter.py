from __future__ import annotations

from core.config import BrandConfig
from core.models import CanonicalContent, InstagramContent


def build_instagram_caption(canonical: CanonicalContent) -> str:
    fs = canonical.fact_sheet
    if not fs.reader_value or not fs.action_steps:
        raise ValueError(
            "cannot build Instagram caption: fact sheet is missing reader_value/action_steps "
            "(canonical content copy generation has not occurred)"
        )
    action_line = " / ".join(fs.action_steps)
    return f"{fs.reader_value}\n\n{fs.why_it_matters}\n\n저장하고 놓치지 마세요.\n다음 단계: {action_line}"


def build_instagram_content(canonical: CanonicalContent, brand: BrandConfig) -> InstagramContent:
    """Map the shared CanonicalContent into Instagram's carousel + caption model.

    Fails clearly instead of silently degrading when the canonical content's
    pages have not actually been generated yet.
    """
    if not canonical.pages:
        raise ValueError(
            "Instagram adapter requires generated carousel pages; none found on canonical content "
            "(copy generation has not occurred)"
        )
    if len(canonical.pages) != canonical.page_count:
        raise ValueError(
            f"Instagram adapter: page count mismatch, expected {canonical.page_count} "
            f"but canonical content has {len(canonical.pages)} pages"
        )

    caption = build_instagram_caption(canonical)
    return InstagramContent(pages=canonical.pages, caption=caption)
