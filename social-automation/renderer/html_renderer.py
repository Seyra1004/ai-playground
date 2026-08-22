from __future__ import annotations

from core.config import BrandConfig
from core.models import CanonicalContent, CarouselPage


def render_page_html(page: CarouselPage, total_pages: int, brand: BrandConfig) -> str:
    """Render one carousel page to a self-contained HTML/CSS fragment.

    Korean typography/layout is handled here in code; no AI image model is
    involved in placing or rendering text (product rule 12).
    """
    bg = next(iter(brand.backgrounds.values()))
    accent = brand.colors.get("violet", "#7848D8")
    accent2 = brand.colors.get("magenta", "#F04890")
    text_color = brand.colors.get("text_primary", "#241B31")
    margin = brand.layout

    return (
        f'<div class="swipe-page" style="'
        f"width:{brand.canvas_width}px;height:{brand.canvas_height}px;background:{bg};"
        f"color:{text_color};font-family:'{brand.typography_family}',sans-serif;"
        f"padding:{margin.get('safe_margin_top', 80)}px {margin.get('safe_margin_right', 84)}px "
        f"{margin.get('safe_margin_bottom', 92)}px {margin.get('safe_margin_left', 84)}px;"
        f'box-sizing:border-box;position:relative;">'
        f'<div class="brand-label" style="color:{accent};font-weight:700;">{brand.name}</div>'
        f'<h1 class="headline">{page.headline}</h1>'
        f'<div class="visual-placeholder" data-visual-ref="{page.visual_ref}" '
        f'style="border:2px dashed {accent};"></div>'
        f'<p class="body">{page.body}</p>'
        f'<div class="page-number" style="background:linear-gradient(90deg, {accent}, {accent2});">'
        f"{page.page_number}/{total_pages}</div>"
        f"</div>"
    )


def build_renderer_input(canonical: CanonicalContent, brand: BrandConfig) -> list:
    if not canonical.pages:
        raise ValueError("cannot build renderer input: canonical content has no generated pages")

    total = len(canonical.pages)
    return [
        {
            "page_number": page.page_number,
            "role": page.role,
            "width": brand.canvas_width,
            "height": brand.canvas_height,
            "html": render_page_html(page, total, brand),
        }
        for page in canonical.pages
    ]
