from __future__ import annotations

from core.config import BrandConfig
from core.models import CanonicalContent, CarouselPage

# Every visual_data["type"] maps to one of a small set of reusable layout
# variants so the carousel has page-to-page variety without a different
# one-off template per page (product rule 5: 2-4 reusable layout variants).
_LAYOUT_VARIANT_BY_VISUAL_TYPE = {
    "stat_hero": "hero",
    "highlight_box": "content_card",
    "checklist": "content_card",
    "comparison": "content_card",
    "exclusion_list": "content_card",
    "steps": "content_card",
    "bar_chart": "chart",
    "cta_panel": "cta",
    "evidence_card": "content_card",
    "process_flow": "content_card",
    "real_image": "content_card",
}


def _chrome_open(brand: BrandConfig, bg: str, text_color: str) -> str:
    margin = brand.layout
    return (
        f'<div class="swipe-page" style="'
        f"width:{brand.canvas_width}px;height:{brand.canvas_height}px;background:{bg};"
        f"color:{text_color};font-family:'{brand.typography_family}','Noto Sans KR','Noto Sans CJK KR',sans-serif;"
        f"padding:{margin.get('safe_margin_top', 80)}px {margin.get('safe_margin_right', 84)}px "
        f"{margin.get('safe_margin_bottom', 92)}px {margin.get('safe_margin_left', 84)}px;"
        f'box-sizing:border-box;position:relative;display:flex;flex-direction:column;">'
    )


def _brand_label(brand: BrandConfig, accent: str) -> str:
    return f'<div style="color:{accent};font-weight:800;font-size:28px;letter-spacing:0.5px;">{brand.name}</div>'


def _page_number_bar(page: CarouselPage, total_pages: int, accent: str, accent2: str) -> str:
    return (
        f'<div style="position:absolute;left:0;bottom:0;width:100%;height:10px;'
        f"background:linear-gradient(90deg, {accent}, {accent2});\"></div>"
        f'<div style="position:absolute;right:84px;bottom:28px;font-size:22px;font-weight:700;opacity:0.6;">'
        f"{page.page_number}/{total_pages}</div>"
    )


# A hard fixed basis (previously flex:0 1 480px) left large dead white space
# below short-content cards, since nothing absorbed the canvas's remaining
# height. flex:1 1 auto lets the card grow to fill actual leftover space
# (light pages get a fuller, roomier card instead of a small box floating
# over blank canvas); min/max bound it so it never shrinks below a usable
# size or balloons into one huge mostly-empty gradient (the original flex:1
# bug this replaced).
_CARD_BASIS = "flex:1 1 auto;min-height:420px;max-height:700px;"


def _render_visual(vd: dict, accent: str, accent2: str, text_color: str) -> str:
    vtype = vd.get("type")

    if vtype == "stat_hero":
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;align-items:center;'
            f'text-align:center;background:linear-gradient(135deg, {accent}22, {accent2}22);border-radius:24px;">'
            f'<div style="font-size:96px;font-weight:900;color:{accent};line-height:1.1;">{vd.get("big_text", "")}</div>'
            f'<div style="font-size:32px;font-weight:600;margin-top:16px;">{vd.get("sub_text", "")}</div>'
            f"</div>"
        )

    if vtype == "highlight_box":
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;align-items:center;'
            f'text-align:center;background:{accent}18;border:3px solid {accent};border-radius:24px;">'
            f'<div style="font-size:72px;">{vd.get("icon", "")}</div>'
            f'<div style="font-size:40px;font-weight:800;color:{accent};margin-top:12px;">{vd.get("highlight", "")}</div>'
            f"</div>"
        )

    if vtype == "checklist":
        items = "".join(
            f'<div style="display:flex;align-items:center;gap:16px;font-size:34px;font-weight:600;margin:14px 0;">'
            f'<span style="color:{accent};font-size:40px;">✅</span><span>{item}</span></div>'
            for item in vd.get("items", [])
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">{items}</div>'
        )

    if vtype == "exclusion_list":
        items = "".join(
            f'<div style="display:flex;align-items:center;gap:16px;font-size:32px;font-weight:600;margin:12px 0;">'
            f'<span style="color:{accent2};font-size:36px;">❌</span><span>{item}</span></div>'
            for item in vd.get("items", [])
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent2}0d;border:3px solid {accent2};border-radius:24px;padding:32px;">{items}</div>'
        )

    if vtype == "steps":
        items = "".join(
            f'<div style="display:flex;align-items:center;gap:20px;margin:18px 0;">'
            f'<div style="width:56px;height:56px;border-radius:50%;background:{accent};color:white;'
            f'display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:800;flex-shrink:0;">'
            f"{i + 1}</div>"
            f'<div style="font-size:32px;font-weight:600;">{item}</div></div>'
            for i, item in enumerate(vd.get("items", []))
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">{items}</div>'
        )

    if vtype == "comparison":
        left, right = vd.get("left", {}), vd.get("right", {})
        col = lambda d, color: (
            f'<div style="flex:1;background:{color}18;border:3px solid {color};border-radius:20px;padding:32px;'
            f'text-align:center;">'
            f'<div style="font-size:44px;">{d.get("icon", "")}</div>'
            f'<div style="font-size:36px;font-weight:800;color:{color};margin-top:8px;">{d.get("title", "")}</div>'
            f'<div style="font-size:26px;font-weight:600;margin-top:16px;">{d.get("desc", "")}</div></div>'
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;gap:24px;align-items:center;">'
            f"{col(left, accent)}{col(right, accent2)}</div>"
        )

    if vtype == "evidence_card":
        # Real official-source citation (publisher/date/url domain pulled
        # straight from the verified FactSheet's own Source record) --
        # grounds the "why this is credible/timely" page in the actual
        # evidence instead of a decorative icon.
        import re as _re

        url = vd.get("url", "")
        domain = _re.sub(r"^https?://(www\.)?", "", url).split("/")[0] if url else ""
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">'
            f'<div style="font-size:52px;">\U0001F4C4</div>'
            f'<div style="font-size:30px;font-weight:800;color:{accent};margin-top:12px;">{vd.get("publisher", "")}</div>'
            f'<div style="font-size:24px;font-weight:600;margin-top:8px;opacity:0.8;">{vd.get("source_label", "")}</div>'
            f'<div style="font-size:22px;font-weight:600;margin-top:12px;opacity:0.6;">'
            f'{vd.get("published_at", "")}{" · " + domain if domain else ""}</div>'
            f"</div>"
        )

    if vtype == "process_flow":
        steps = vd.get("steps", [])
        arrow = f'<div style="font-size:34px;color:{accent};text-align:center;margin:4px 0;">↓</div>'
        blocks = arrow.join(
            f'<div style="background:{accent}14;border:2px solid {accent};border-radius:16px;'
            f'padding:18px 24px;text-align:center;font-size:28px;font-weight:700;">{step}</div>'
            for step in steps
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;'
            f'gap:0;padding:24px;">{blocks}</div>'
        )

    if vtype == "real_image":
        # An actual downloaded image file (not CSS/emoji/a generated
        # diagram) -- source/rights are recorded separately in
        # data/assets/<account>/asset_sources.json. Embedded as a base64
        # data: URI so it renders correctly under Playwright's
        # page.set_content() (no file:// base URL involved).
        import base64
        import os

        image_path = vd.get("image_path", "")
        caption = vd.get("caption", "")
        img_tag = ""
        if image_path and os.path.isfile(image_path):
            ext = os.path.splitext(image_path)[1].lower().lstrip(".")
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}" if ext else "image/jpeg"
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            img_tag = f'<img src="data:{mime};base64,{b64}" style="width:100%;height:100%;object-fit:cover;display:block;">'
        caption_html = (
            f'<div style="padding:14px 20px;font-size:22px;font-weight:700;background:{accent}0d;">{caption}</div>'
            if caption else ""
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;overflow:hidden;'
            f'border:3px solid {accent};border-radius:24px;background:#fff;">'
            f'<div style="flex:1;overflow:hidden;min-height:0;">{img_tag}</div>{caption_html}</div>'
        )

    if vtype == "bar_chart":
        items = vd.get("items", [])
        unit = vd.get("unit", "")
        max_val = max((v for _, v in items), default=1)
        bars = "".join(
            f'<div style="display:flex;align-items:center;gap:16px;margin:10px 0;">'
            f'<div style="width:110px;font-size:24px;font-weight:700;flex-shrink:0;">{label}</div>'
            f'<div style="flex:1;background:{accent}18;border-radius:8px;overflow:hidden;height:36px;">'
            f'<div style="width:{int(val / max_val * 100)}%;height:100%;'
            f"background:linear-gradient(90deg, {accent}, {accent2});\"></div></div>"
            f'<div style="width:90px;text-align:right;font-size:24px;font-weight:800;flex-shrink:0;">{val}{unit}</div>'
            f"</div>"
            for label, val in items
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">{bars}</div>'
        )

    if vtype == "cta_panel":
        region = vd.get("region", "")
        region_html = (
            f'<div style="font-size:28px;font-weight:700;margin-bottom:24px;">\U0001F4CD {region}</div>' if region else ""
        )
        return (
            f'<div style="{_CARD_BASIS}display:flex;flex-direction:column;justify-content:center;align-items:center;">'
            f"{region_html}"
            f'<div style="background:linear-gradient(90deg, {accent}, {accent2});color:white;font-size:38px;'
            f'font-weight:800;padding:28px 56px;border-radius:60px;">{vd.get("button_text", "")}</div>'
            f"</div>"
        )

    return f'<div style="{_CARD_BASIS}border:2px dashed {accent};border-radius:16px;"></div>'


def render_page_html(page: CarouselPage, total_pages: int, brand: BrandConfig) -> str:
    """Render one carousel page to a self-contained HTML/CSS fragment.

    Korean typography/layout is handled here in code; no AI image model is
    involved in placing or rendering text (product rule 12). The visual for
    each page is derived from page.visual_data, keeping 2-4 reusable layout
    variants instead of one repeated template (product rule 5).
    """
    bg = next(iter(brand.backgrounds.values()))
    accent = brand.colors.get("violet", "#7848D8")
    accent2 = brand.colors.get("magenta", "#F04890")
    text_color = brand.colors.get("text_primary", "#241B31")

    html = [_chrome_open(brand, bg, text_color)]
    html.append(_brand_label(brand, accent))
    html.append(f'<h1 style="font-size:44px;font-weight:800;line-height:1.3;margin:20px 0 24px 0;">{page.headline}</h1>')
    html.append(_render_visual(page.visual_data or {"type": None}, accent, accent2, text_color))
    if page.body:
        html.append(
            f'<p style="font-size:26px;font-weight:500;line-height:1.5;margin-top:20px;opacity:0.85;">{page.body}</p>'
        )
    html.append(_page_number_bar(page, total_pages, accent, accent2))
    html.append("</div>")
    return "".join(html)


def build_renderer_input(canonical: CanonicalContent, brand: BrandConfig) -> list:
    if not canonical.pages:
        raise ValueError("cannot build renderer input: canonical content has no generated pages")

    total = len(canonical.pages)
    return [
        {
            "page_number": page.page_number,
            "role": page.role,
            "layout_variant": _LAYOUT_VARIANT_BY_VISUAL_TYPE.get((page.visual_data or {}).get("type"), "content_card"),
            "width": brand.canvas_width,
            "height": brand.canvas_height,
            "html": render_page_html(page, total, brand),
        }
        for page in canonical.pages
    ]
