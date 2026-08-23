from __future__ import annotations

import re

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
    "generated_editorial_asset": "content_card",
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


# --- editorial design-system components (masthead/footer/pill/highlight/
# numbered rows/photo panels/summary banner) -- reused across the layout
# families below instead of one repeated boxed-card template per page. ---


def _masthead(brand: BrandConfig, accent: str, text_color: str, page_number: int, total_pages: int) -> str:
    return (
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'padding-bottom:18px;border-bottom:2px solid {text_color}14;">'
        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'<div style="width:14px;height:14px;border-radius:50%;background:{accent};"></div>'
        f'<div style="color:{text_color};font-weight:800;font-size:26px;letter-spacing:0.5px;">{brand.name}</div>'
        f"</div>"
        f'<div style="font-weight:700;font-size:22px;opacity:0.55;">{page_number:02d} / {total_pages:02d}</div>'
        f"</div>"
    )


def _footer(brand: BrandConfig, accent: str, accent2: str, text_color: str) -> str:
    short_name = brand.name.split("_")[0]
    return (
        f'<div style="position:absolute;left:84px;right:84px;bottom:34px;display:flex;align-items:center;gap:12px;">'
        f'<div style="width:10px;height:10px;border-radius:50%;background:{accent};flex-shrink:0;"></div>'
        f'<div style="font-weight:800;font-size:20px;color:{text_color};opacity:0.7;flex-shrink:0;">{short_name}</div>'
        f'<div style="flex:1;height:3px;background:linear-gradient(90deg,{accent},{accent2});opacity:0.5;border-radius:2px;"></div>'
        f"</div>"
    )


def _pill(text: str, accent: str) -> str:
    if not text:
        return ""
    return (
        f'<div style="display:inline-block;background:{accent}1c;color:{accent};font-weight:700;'
        f'font-size:22px;padding:8px 20px;border-radius:999px;margin-bottom:18px;">{text}</div>'
    )


def _highlighted_headline(text: str, accent: str) -> str:
    """Bold headline with the final short phrase drawn over a soft
    highlighter-style background band -- a deterministic stand-in for
    hand-picking "the" keyword: takes the last space-delimited word(s) up
    to ~8 chars so the emphasis always lands on a whole word, never a
    truncated fragment."""
    text = text or ""
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and 1 <= len(parts[1]) <= 10:
        head, tail = parts[0] + " ", parts[1]
    else:
        head, tail = "", text
    return (
        f'<h1 style="font-size:46px;font-weight:800;line-height:1.32;margin:0 0 20px 0;">'
        f"{head}<span style=\"background:linear-gradient(180deg,transparent 58%,{accent}3d 58%);\">{tail}</span></h1>"
    )


def _body_text(text: str, text_color: str) -> str:
    if not text:
        return ""
    return f'<p style="font-size:27px;font-weight:500;line-height:1.55;margin:0 0 26px 0;color:{text_color};opacity:0.72;">{text}</p>'


def _summary_banner(text: str, bg_color: str) -> str:
    if not text:
        return ""
    return (
        f'<div style="background:{bg_color};color:#fff;border-radius:20px;padding:26px 32px;'
        f'font-size:26px;font-weight:700;line-height:1.5;margin-top:22px;">{text}</div>'
    )


def _fill_justify(flex: str) -> str:
    """A short list forced to fill a tall flex:1 container with
    justify-content:center clumps all the extra space into one dead gap
    above+below the whole block -- generic across any item count/body
    length combo, not just the single-item case already handled by
    sizing to content. Distributing that space BETWEEN/AROUND the rows
    instead makes any row count read as intentionally spaced, not
    accidentally empty. Content-sized calls (flex starting "0") have no
    extra space to distribute, so they keep a plain center."""
    return "space-evenly" if flex.strip().startswith("1") else "center"


def _numbered_rows(items: list, accent: str, flex: str = "1 1 auto") -> str:
    rows = "".join(
        f'<div style="background:#fff;border-radius:18px;padding:22px 26px;margin-bottom:16px;'
        f'box-shadow:0 1px 3px {accent}1a;display:flex;align-items:center;gap:20px;">'
        f'<div style="width:44px;height:44px;border-radius:12px;background:{accent};color:#fff;'
        f'font-weight:800;font-size:22px;flex-shrink:0;display:flex;align-items:center;justify-content:center;">{i + 1}</div>'
        f'<div style="font-size:28px;font-weight:700;line-height:1.35;">{item}</div></div>'
        for i, item in enumerate(items)
    )
    return f'<div style="flex:{flex};display:flex;flex-direction:column;justify-content:{_fill_justify(flex)};overflow:hidden;">{rows}</div>'


def _check_rows(items: list, accent: str, flex: str = "1 1 auto") -> str:
    rows = "".join(
        f'<div style="display:flex;align-items:flex-start;gap:16px;margin:16px 0;">'
        f'<div style="width:32px;height:32px;border-radius:50%;background:{accent};color:#fff;flex-shrink:0;'
        f'display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:800;">✓</div>'
        f'<div style="font-size:28px;font-weight:700;line-height:1.4;">{item}</div></div>'
        for item in items
    )
    return (
        f'<div style="flex:{flex};background:#fff;border-radius:20px;padding:30px 30px 6px 30px;'
        f'display:flex;flex-direction:column;justify-content:{_fill_justify(flex)};overflow:hidden;">{rows}</div>'
    )


def _infographic_numbered_rows(items: list, accent: str, accent2: str, flex: str = "1 1 auto") -> str:
    """A "structured program overview" treatment -- large outlined numerals
    and a left accent rule -- deliberately distinct from _check_rows'
    checkmark-list look so two checklist-typed pages in one carousel don't
    read as the same composition."""
    n = max(len(items), 1)
    colors = [accent, accent2]
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:22px;padding:20px 0;'
        f'{"border-bottom:2px solid " + accent + "14;" if i < n - 1 else ""}">'
        f'<div style="font-size:44px;font-weight:900;color:{colors[i % 2]};opacity:0.35;width:64px;flex-shrink:0;">{i + 1:02d}</div>'
        f'<div style="flex:1;height:100%;width:4px;background:{colors[i % 2]};border-radius:2px;align-self:stretch;flex-shrink:0;max-width:4px;"></div>'
        f'<div style="font-size:29px;font-weight:700;line-height:1.4;">{item}</div></div>'
        for i, item in enumerate(items)
    )
    return (
        f'<div style="flex:{flex};background:#fff;border-radius:22px;padding:8px 34px;'
        f'display:flex;flex-direction:column;justify-content:{_fill_justify(flex)};overflow:hidden;">{rows}</div>'
    )


def _cta_full(headline: str, button_text: str, region: str, accent: str, accent2: str, flex: str = "1 1 auto") -> str:
    """A dark, bold, full-scale closing composition -- the CTA should be
    the highest-visual-weight page in the carousel, not a small banner
    tucked under other content."""
    region_html = (
        f'<div style="color:#fff;opacity:0.65;font-weight:700;font-size:22px;margin-bottom:16px;">📍 {region}</div>'
        if region else ""
    )
    # A tall dark block with only a headline+button left a large empty gap
    # below them (content naturally sized well under the full available
    # height). A big faded decorative arrow behind the text fills that
    # space intentionally -- a deliberate editorial texture, not accidental
    # whitespace -- while the actual copy/button stay bottom-anchored so
    # the CTA is the last thing the eye lands on before swiping.
    return (
        f'<div style="flex:{flex};background:#181022;border-radius:28px;padding:52px 40px;'
        f'display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-end;'
        f'position:relative;overflow:hidden;">'
        f'<div style="position:absolute;top:-40px;right:-60px;font-size:420px;font-weight:900;'
        f'color:{accent};opacity:0.08;line-height:1;">→</div>'
        f"{region_html}"
        f'<div style="color:#fff;font-weight:800;font-size:42px;line-height:1.3;margin-bottom:34px;position:relative;">{headline}</div>'
        f'<div style="background:linear-gradient(90deg,{accent},{accent2});color:#fff;font-weight:800;'
        f'font-size:30px;padding:24px 44px;border-radius:16px;position:relative;">{button_text} →</div>'
        f"</div>"
    )


def _image_src(image_data: dict) -> str:
    import base64
    import os

    image_path = (image_data or {}).get("image_path", "")
    if not image_path or not os.path.isfile(image_path):
        return ""
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}" if ext else "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _photo_panel(image_data: dict, tag: str, caption: str, accent: str, flex: str = "1 1 auto", min_h: str = "0") -> str:
    """A bleeding photo/illustration panel with a dark gradient scrim and an
    overlaid pill tag + bold caption -- the image and the copy share the
    same surface instead of a picture floating in its own separate boxed
    card next to text."""
    src = _image_src(image_data)
    # Generated illustrations are centered-focal-point scenes (icon +
    # decorative background fill) now, not the tall stacked compositions
    # that once needed "contain" to avoid cropping content -- "cover" fills
    # the panel edge-to-edge like a real bleeding photo instead of leaving
    # black letterbox bars around a small centered image.
    img_tag = f'<img src="{src}" style="width:100%;height:100%;object-fit:cover;display:block;">' if src else f'<div style="width:100%;height:100%;background:{accent}12;"></div>'
    overlay = ""
    if tag or caption:
        overlay = (
            '<div style="position:absolute;left:0;right:0;bottom:0;padding:28px 26px 22px 26px;'
            'background:linear-gradient(180deg,transparent,rgba(0,0,0,0.62) 55%,rgba(0,0,0,0.78));">'
            + (f'<div style="display:inline-block;background:{accent};color:#fff;font-weight:800;'
               f'font-size:18px;letter-spacing:0.5px;padding:5px 14px;border-radius:8px;margin-bottom:10px;'
               f'text-transform:uppercase;">{tag}</div><br/>' if tag else "")
            + (f'<div style="color:#fff;font-weight:800;font-size:27px;line-height:1.35;">{caption}</div>' if caption else "")
            + "</div>"
        )
    return (
        f'<div style="flex:{flex};min-height:{min_h};position:relative;border-radius:22px;overflow:hidden;'
        f'background:#000;">{img_tag}{overlay}</div>'
    )


def _focal_number_block(big_text: str, sub_text: str, accent: str) -> str:
    """VISUAL ENGINE V2 primitive: a strong standalone number/claim as an
    editorial pull-stat -- left-aligned, sized to its own content (no
    flex:1 stretch, no decorative ring/circle). Pairs with
    _pull_quote_panel below it so the page carries TWO genuine content
    blocks instead of one shape trying to fill empty space."""
    sub_html = f'<div style="font-size:28px;font-weight:700;margin-top:12px;color:{accent};opacity:0.85;">{sub_text}</div>' if sub_text else ""
    return (
        f'<div style="flex:0 1 auto;padding:4px 0 0 0;">'
        f'<div style="font-size:112px;font-weight:900;line-height:0.98;letter-spacing:-3px;color:{accent};">{big_text}</div>'
        f"{sub_html}</div>"
    )


def _pull_quote_panel(text: str, accent: str, accent2: str) -> str:
    """VISUAL ENGINE V2 primitive: a second, distinct editorial block (not
    a stretched copy of the first) -- the page's own already-approved
    closing sentence styled as a tinted pull-quote. Content-sized."""
    if not text:
        return ""
    return (
        f'<div style="flex:0 1 auto;margin-top:34px;background:linear-gradient(120deg,{accent}14,{accent2}0d);'
        f'border-left:6px solid {accent};border-radius:6px 20px 20px 6px;padding:26px 28px;">'
        f'<div style="font-size:27px;font-weight:800;line-height:1.5;color:{accent};">{text}</div></div>'
    )


_KOREAN_DATE_RE = re.compile(r"\d{1,2}월\s?\d{1,2}일")


def _extract_accent_stat(body: str, items: list) -> tuple:
    """Deterministic secondary-panel fact -- a real date already present in
    the page's own body text if there is one, else the true item count.
    Never invented; both are mechanically derived from already-approved
    content, reusable for any future topic's list-shaped page."""
    m = _KOREAN_DATE_RE.search(body or "")
    if m:
        return "시행일", m.group(0)
    if items:
        return "확인할 항목", f"{len(items)}가지"
    return "", ""


def _content_plus_accent(primary_html: str, label: str, value: str, accent: str, accent2: str) -> str:
    """VISUAL ENGINE V2 primitive: a two-zone asymmetric composition --
    the primary content (sized to its own real content, never stretched)
    beside a compact colored panel carrying one genuine derived fact. This
    is what replaces "one giant white box" for any list/checklist-shaped
    page: real secondary visual weight instead of an empty stretched card."""
    if not value:
        return f'<div style="flex:0 1 auto;">{primary_html}</div>'
    panel = (
        f'<div style="flex:0 0 30%;background:linear-gradient(160deg,{accent},{accent2});border-radius:20px;'
        f'padding:26px 18px;display:flex;flex-direction:column;justify-content:center;color:#fff;">'
        f'<div style="font-size:17px;font-weight:700;opacity:0.85;margin-bottom:8px;">{label}</div>'
        f'<div style="font-size:32px;font-weight:900;line-height:1.2;">{value}</div></div>'
    )
    return f'<div style="flex:0 1 auto;display:flex;gap:18px;align-items:stretch;">' f'<div style="flex:1;">{primary_html}</div>{panel}</div>'


def _comparison_cards_v2(left: dict, right: dict, accent: str, accent2: str) -> str:
    """VISUAL ENGINE V2 primitive: two content-sized (not flex:1-stretched)
    comparison cards with a floating transition badge overlapping between
    them -- visualizes the CHANGE itself, not just two static boxes."""
    def card(d, color):
        return (
            f'<div style="flex:1;background:#fff;border:3px solid {color}55;border-top:8px solid {color};'
            f'border-radius:18px;padding:34px 24px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{color};opacity:0.85;margin-bottom:10px;">{d.get("title", "")}</div>'
            f'<div style="font-size:27px;font-weight:800;line-height:1.4;">{d.get("desc", "")}</div></div>'
        )
    arrow = (
        f'<div style="width:0;display:flex;align-items:center;justify-content:center;z-index:2;">'
        f'<div style="width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,{accent},{accent2});'
        f'color:#fff;font-size:24px;font-weight:900;display:flex;align-items:center;justify-content:center;'
        f'margin-left:-26px;box-shadow:0 4px 10px {accent}44;flex-shrink:0;">→</div></div>'
    )
    return f'<div style="flex:0 1 auto;display:flex;align-items:stretch;">{card(left, accent)}{arrow}{card(right, accent2)}</div>'


_ARROW_STEP_RE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨]\s*")


def _extract_step_flow(body: str) -> tuple:
    """Deterministic parse of a body ALREADY written as a numbered
    arrow sequence (①...→②...→...) into discrete steps + any trailing
    sentence after the chain (e.g. a secondary share prompt). Only fires
    when the text already uses this pattern -- returns ([], body)
    otherwise, never fabricating steps for a page that doesn't have them."""
    if "→" not in (body or ""):
        return [], body
    segments = [s.strip() for s in body.split("→")]
    steps, trailing = [], ""
    for i, seg in enumerate(segments):
        seg = _ARROW_STEP_RE.sub("", seg)
        if i == len(segments) - 1:
            first, _, rest = seg.partition(".")
            steps.append(first.strip())
            trailing = rest.strip()
        else:
            steps.append(seg)
    return steps, trailing


def _cta_designed(steps: list, trailing: str, button_text: str, accent: str, accent2: str) -> str:
    """VISUAL ENGINE V2 primitive: the action page as a visual step-flow +
    button, not a paragraph of text inside a dark rectangle."""
    colors = [accent, accent2]
    chips = "".join(
        (
            f'<div style="display:flex;align-items:center;gap:16px;">'
            f'<div style="width:38px;height:38px;border-radius:50%;background:{colors[i % 2]};color:#fff;'
            f'display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px;flex-shrink:0;">{i + 1}</div>'
            f'<div style="color:#fff;font-weight:700;font-size:24px;line-height:1.3;">{step}</div></div>'
        )
        + (f'<div style="margin-left:18px;color:{colors[i % 2]};font-size:20px;opacity:0.55;">↓</div>' if i < len(steps) - 1 else "")
        for i, step in enumerate(steps)
    )
    trailing_html = f'<div style="color:#fff;opacity:0.7;font-weight:600;font-size:20px;margin-top:4px;">{trailing}</div>' if trailing else ""
    return (
        f'<div style="flex:0 1 auto;background:#181022;border-radius:28px;padding:40px 34px;'
        f'display:flex;flex-direction:column;gap:22px;position:relative;overflow:hidden;">'
        f'<div style="position:absolute;top:-30px;right:-50px;font-size:280px;font-weight:900;color:{accent};opacity:0.07;line-height:1;">→</div>'
        f'<div style="display:flex;flex-direction:column;gap:8px;position:relative;">{chips}</div>'
        f"{trailing_html}"
        f'<div style="background:linear-gradient(90deg,{accent},{accent2});color:#fff;font-weight:800;'
        f'font-size:27px;padding:22px 40px;border-radius:16px;text-align:center;position:relative;">{button_text} →</div>'
        f"</div>"
    )


def _card_grid_rows(items: list, accent: str, accent2: str, flex: str = "1 1 auto") -> str:
    """A denser alternative to _infographic_numbered_rows for several
    benefits/programs -- two-column cards instead of stacked rows, used
    when a stacked list would repeat the immediately preceding page's
    composition. flex-wrap + align-content:center left large accidental
    empty bands above/below the card block in a tall flex:1 container
    (same empty-space defect class); a CSS grid with grid-auto-rows:1fr
    makes the cards themselves stretch to occupy the full available
    height instead, for any item count -- not sized to one fixture."""
    colors = [accent, accent2]
    cards = "".join(
        f'<div style="background:#fff;border-radius:16px;padding:26px 22px;'
        f'box-shadow:0 1px 3px {colors[i % 2]}22;border-top:6px solid {colors[i % 2]};'
        f'display:flex;flex-direction:column;justify-content:center;">'
        f'<div style="font-size:24px;font-weight:900;color:{colors[i % 2]};opacity:0.55;margin-bottom:8px;">{i + 1:02d}</div>'
        f'<div style="font-size:26px;font-weight:700;line-height:1.35;">{item}</div></div>'
        for i, item in enumerate(items)
    )
    return (
        f'<div style="flex:{flex};display:grid;grid-template-columns:1fr 1fr;'
        f'grid-auto-rows:1fr;gap:18px;overflow:hidden;">{cards}</div>'
    )


def _photo_info_overlay(image_data: dict, tag: str, vd: dict, accent: str, flex: str = "1 1 auto") -> str:
    """A relevant photo with no strong list/comparison structure of its
    own -- the photo carries the page, with whatever short claim the
    content actually has (never the full headline repeated) as caption."""
    items = vd.get("items") or []
    caption = vd.get("highlight") or vd.get("big_text") or (items[0] if items else "")
    return _photo_panel(image_data, tag, caption, accent, flex=flex)


def _photo_side_panel(image_data: dict, items: list, accent: str, flex: str = "1 1 auto") -> str:
    """A narrow SIDE photo strip (not a full-width top/bottom band) paired
    with a short list beside it -- a second, genuinely different photo
    shape/position from _photo_panel's full-bleed top/bottom treatment, so
    a carousel with more than one photo+list page doesn't repeat the same
    silhouette. Reuses _photo_panel (just narrower) and _check_rows -- no
    new photo-rendering logic, just a different composition of existing
    primitives."""
    photo = _photo_panel(image_data, "", "", accent, flex="0 0 38%")
    rows = _check_rows(items, accent, flex="1 1 auto")
    return f'<div style="flex:{flex};display:flex;gap:20px;align-items:stretch;">{photo}{rows}</div>'


def _closing_strip(text: str, accent: str, text_color: str) -> str:
    """A full-width, high-contrast one-line distillation bar placed just
    above the footer -- the repeating "so what" capstone every page in the
    reference carousel ends on. Purely mechanical: the caller passes the
    page's OWN already-approved final sentence, nothing invented here."""
    if not text:
        return ""
    return (
        f'<div style="background:#181022;border-radius:16px;padding:22px 26px;margin-top:18px;'
        f'flex-shrink:0;"><div style="color:#fff;font-weight:700;font-size:24px;line-height:1.5;">{text}</div></div>'
    )


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?다요])\s+")


def _split_closing_sentence(body: str):
    """Deterministic, mechanical split of an already-approved body string
    into (lead_text, closing_sentence) -- the closing strip reuses the
    page's own last sentence verbatim; nothing is reworded or invented.
    Only splits when there are 2+ sentences, so a single-sentence page
    (no genuine second thought to distill) gets no strip."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split((body or "").strip()) if p.strip()]
    if len(parts) < 2:
        return body, ""
    return " ".join(parts[:-1]), parts[-1]


def _comparison_cards(left: dict, right: dict, accent: str, accent2: str, flex: str = "1 1 auto") -> str:
    def card(d, color):
        return (
            f'<div style="flex:1;background:#fff;border:3px solid {color}55;border-top:8px solid {color};'
            f'border-radius:18px;padding:28px 22px;text-align:center;display:flex;flex-direction:column;'
            f'justify-content:center;">'
            f'<div style="display:inline-block;background:{color}18;color:{color};font-weight:800;font-size:18px;'
            f'padding:5px 14px;border-radius:8px;margin:0 auto 12px auto;">{d.get("icon", "")}</div>'
            f'<div style="font-size:30px;font-weight:800;color:{color};margin-bottom:10px;">{d.get("title", "")}</div>'
            f'<div style="font-size:23px;font-weight:600;line-height:1.4;">{d.get("desc", "")}</div></div>'
        )
    return f'<div style="flex:{flex};display:flex;gap:20px;">{card(left, accent)}{card(right, accent2)}</div>'


def _cta_banner(button_text: str, region: str, accent: str, accent2: str) -> str:
    region_html = f'<div style="color:#fff;opacity:0.7;font-weight:700;font-size:20px;margin-bottom:8px;">📍 {region}</div>' if region else ""
    return (
        f'<div style="background:{"#20182c"};border-radius:22px;padding:30px 32px;display:flex;'
        f'align-items:center;justify-content:space-between;gap:20px;margin-top:22px;">'
        f'<div>{region_html}<div style="color:#fff;font-weight:800;font-size:28px;line-height:1.35;">지금 바로 확인하고<br/>도움을 받아보세요</div></div>'
        f'<div style="flex-shrink:0;background:linear-gradient(90deg,{accent},{accent2});color:#fff;'
        f'font-weight:800;font-size:26px;padding:20px 30px;border-radius:14px;white-space:nowrap;">{button_text} →</div>'
        f"</div>"
    )


# A hard fixed basis (previously flex:0 1 480px) left large dead white space
# below short-content cards, since nothing absorbed the canvas's remaining
# height. flex:1 1 auto lets the card grow to fill actual leftover space
# (light pages get a fuller, roomier card instead of a small box floating
# over blank canvas); min/max bound it so it never shrinks below a usable
# size or balloons into one huge mostly-empty gradient (the original flex:1
# bug this replaced).
_CARD_BASIS = "flex:1 1 auto;min-height:420px;max-height:700px;"


def _render_visual(vd: dict, accent: str, accent2: str, text_color: str, card_style: str = None) -> str:
    card_style = card_style or _CARD_BASIS
    vtype = vd.get("type")

    if vtype == "stat_hero":
        return (
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;align-items:center;'
            f'text-align:center;background:linear-gradient(135deg, {accent}22, {accent2}22);border-radius:24px;">'
            f'<div style="font-size:96px;font-weight:900;color:{accent};line-height:1.1;">{vd.get("big_text", "")}</div>'
            f'<div style="font-size:32px;font-weight:600;margin-top:16px;">{vd.get("sub_text", "")}</div>'
            f"</div>"
        )

    if vtype == "highlight_box":
        return (
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;align-items:center;'
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
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">{items}</div>'
        )

    if vtype == "exclusion_list":
        items = "".join(
            f'<div style="display:flex;align-items:center;gap:16px;font-size:32px;font-weight:600;margin:12px 0;">'
            f'<span style="color:{accent2};font-size:36px;">❌</span><span>{item}</span></div>'
            for item in vd.get("items", [])
        )
        return (
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;'
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
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">{items}</div>'
        )

    if vtype == "comparison":
        left, right = vd.get("left", {}), vd.get("right", {})
        # align-items:stretch (not :center) so both colored columns fill the
        # full card height -- centering left blank gaps above/below the
        # columns whenever the card grows taller than their own content.
        col = lambda d, color: (
            f'<div style="flex:1;display:flex;flex-direction:column;justify-content:center;'
            f'background:{color}18;border:3px solid {color};border-radius:20px;padding:32px;'
            f'text-align:center;">'
            f'<div style="font-size:44px;">{d.get("icon", "")}</div>'
            f'<div style="font-size:36px;font-weight:800;color:{color};margin-top:8px;">{d.get("title", "")}</div>'
            f'<div style="font-size:26px;font-weight:600;margin-top:16px;">{d.get("desc", "")}</div></div>'
        )
        return (
            f'<div style="{card_style}display:flex;gap:24px;align-items:stretch;">'
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
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;'
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
        # Fixed-size boxes overflowed their allocated space once composed
        # alongside an image panel shrank this card below its natural
        # content height (4+ steps spilled into the headline above and
        # behind the image below). Scale each step's footprint down as the
        # list grows -- generic for any step count, not just 4 -- and clip
        # as a safety net instead of leaking into neighboring elements.
        n = max(len(steps), 1)
        box_font = max(20, 30 - (n - 2) * 2)
        box_pad_v = max(8, 18 - (n - 2) * 3)
        arrow_font = max(20, 34 - (n - 2) * 3)
        arrow_margin = max(1, 4 - (n - 2))
        arrow = f'<div style="font-size:{arrow_font}px;color:{accent};text-align:center;margin:{arrow_margin}px 0;">↓</div>'
        blocks = arrow.join(
            f'<div style="background:{accent}14;border:2px solid {accent};border-radius:16px;'
            f'padding:{box_pad_v}px 24px;text-align:center;font-size:{box_font}px;font-weight:700;">{step}</div>'
            for step in steps
        )
        return (
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;'
            f'gap:0;padding:24px;overflow:hidden;">{blocks}</div>'
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
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;padding:32px;">{bars}</div>'
        )

    if vtype == "cta_panel":
        region = vd.get("region", "")
        region_html = (
            f'<div style="font-size:28px;font-weight:700;margin-bottom:24px;">\U0001F4CD {region}</div>' if region else ""
        )
        # A tinted bordered card (matching highlight_box/checklist/etc.)
        # instead of a transparent box -- without it, growing the card taller
        # just adds more blank white space around the button rather than
        # reading as an intentional roomy card.
        return (
            f'<div style="{card_style}display:flex;flex-direction:column;justify-content:center;align-items:center;'
            f'background:{accent}0d;border:3px solid {accent};border-radius:24px;">'
            f"{region_html}"
            f'<div style="background:linear-gradient(90deg, {accent}, {accent2});color:white;font-size:38px;'
            f'font-weight:800;padding:28px 56px;border-radius:60px;">{vd.get("button_text", "")}</div>'
            f"</div>"
        )

    return f'<div style="{card_style}border:2px dashed {accent};border-radius:16px;"></div>'


# PAGE MEANING -> EDITORIAL LAYOUT decision layer.
#
# A page's `role` (hook/why_now/eligibility/.../cta, from core/
# page_selector.py) fixes its PURPOSE in the carousel's content flow --
# P1 hook, P2 why-now, P3-5 core value, P6 action/share -- never its
# composition. Two roles that carry the same kind of information (e.g.
# eligibility's 2-item checklist vs. conditions' 5-item checklist) must be
# free to render as different families, and a role that happens to get a
# genuinely strong photo one day should be free to use it -- so layout is
# chosen here from the page's own verified visual_data structure, whether
# a real (non-generic) photo was actually acquired, and its position in
# the fixed content flow -- never from a role->layout lookup table.
_LIST_TIE_BREAK = {
    "checklist": "numbered_infographic",
    "numbered_infographic": "checklist",
    "process": "numbered_infographic",
    "card_grid": "numbered_infographic",
    # A short claim that legitimately earns stat_hero on two adjacent pages
    # would repeat P1's full visual weight right on P2 -- checklist renders
    # the exact same text (still fully preserved, just at body scale
    # instead of a giant numeral), so nothing about the actual content is
    # lost or restructured; it's a safe universal fallback, not a forced
    # redesign.
    "stat_hero": "checklist",
}

_STAT_HERO_MAX_CHARS = 16


def _is_stat_hero_worthy(text: str) -> bool:
    """A giant centered numeral/claim treatment only works for a genuinely
    compact quantitative or punchy claim (a short number, percentage,
    amount, date, short metric, or very short textual claim) -- not a full
    sentence, multi-clause warning, or long explanation, which would need
    3+ giant-font lines and reads as oversized/broken rather than
    editorial. A short length ceiling plus a check for a comma-joined
    multi-clause statement is enough to tell the two apart without any
    topic-specific wording."""
    text = (text or "").strip()
    if not text or len(text) > _STAT_HERO_MAX_CHARS:
        return False
    return "," not in text and "，" not in text


def _select_layout_family(page: CarouselPage, total_pages: int, prev_family: str = None) -> str:
    """Deterministically picks ONE editorial layout family for this page.

    Order of decision:
    1. P6 (last page) / an explicit cta_panel -> the highest-visual-weight
       action/share close, regardless of any other structure present.
    2. P1 (hook) -> maximum stopping power: a genuinely relevant photo
       first, else a strong standalone number/claim, else a short list.
    3. Otherwise the page's own visual_data SHAPE decides: comparison
       structure -> comparison; 3-6 sequential steps -> process (denser ->
       numbered_infographic); an independent-facts list -> checklist when
       short (<=3, scans as checkmarks) or numbered_infographic/card_grid
       when longer (several benefits/programs carry more information);
       a standalone number/claim -> stat_hero (or photo_info_overlay if a
       real photo also exists); a real photo with no stronger structure of
       its own -> photo_info_overlay; otherwise a safe text-first default.
    4. Only when two family choices are genuinely equally valid readings
       of the same list-shaped data (never for a real structural
       difference like comparison/photo/cta) does the tie break toward
       whichever avoids repeating the immediately preceding page.
    """
    vd = page.visual_data or {}
    vtype = vd.get("type")
    has_photo = bool((page.image_data or {}).get("image_path"))
    items = vd.get("items") or []
    steps = vd.get("steps") or []

    if page.page_number == total_pages or vtype == "cta_panel":
        return "cta"

    if page.page_number == 1:
        if has_photo:
            return "editorial_photo"
        short_claim = vd.get("big_text") or vd.get("highlight")
        if short_claim and _is_stat_hero_worthy(short_claim):
            return "stat_hero"
        if items:
            return "checklist"
        return "checklist" if vd.get("highlight") else "stat_hero"

    if vtype == "comparison" and vd.get("left") and vd.get("right"):
        family = "comparison"
    elif vtype == "process_flow" and steps:
        family = "process" if 3 <= len(steps) <= 6 else "numbered_infographic"
    elif vtype == "bar_chart" and items:
        # A two-entity number comparison (e.g. two regions' rainfall) reads
        # as an A-vs-B comparison; more than two values reads as a ranked
        # list instead -- either way this must never fall through to the
        # generic checklist default, which would print each [label, value]
        # pair's raw Python repr as literal page text.
        family = "comparison" if len(items) == 2 else "numbered_infographic"
    elif vtype == "checklist" and items:
        if len(items) <= 3 and has_photo:
            # A short list is compact enough to genuinely share the page
            # with a photo -- picking plain "checklist" here would silently
            # discard an already-vetted, semantically-specific photo just
            # because the role happens to carry a short structured list.
            family = "hybrid_photo_checklist"
        elif len(items) <= 3:
            family = "checklist"
        elif has_photo:
            family = "card_grid"
        else:
            family = "numbered_infographic"
    elif (vd.get("big_text") and _is_stat_hero_worthy(vd.get("big_text"))) or (
        vtype == "highlight_box" and vd.get("highlight") and _is_stat_hero_worthy(vd.get("highlight"))
    ):
        family = "photo_info_overlay" if has_photo else "stat_hero"
    elif vtype == "highlight_box" and vd.get("highlight"):
        # A highlight_box whose text is too long for a giant-numeral
        # treatment still needs to render its content somewhere -- a
        # single-item checklist is the existing family that already
        # handles arbitrary-length text at a legible body scale.
        family = "photo_info_overlay" if has_photo else "checklist"
    elif has_photo:
        family = "photo_info_overlay"
    else:
        family = "checklist" if items else "stat_hero"

    if family == prev_family:
        family = _LIST_TIE_BREAK.get(family, family)

    return family


def _pill_label_for_role(role: str) -> str:
    return {
        "hook": "지금 확인하세요", "why_now": "왜 지금 확인해야 할까요", "eligibility": "대상 확인",
        "amount": "핵심 조치", "conditions": "핵심 조치", "procedure": "이렇게 진행돼요",
        "comparison": "무엇이 다를까", "examples": "무엇이 다를까", "exclusions": "주의하세요",
        "warnings": "주의하세요", "cta": "가장 안전한 다음 단계",
    }.get(role, "핵심 정보")


def render_page_html(page: CarouselPage, total_pages: int, brand: BrandConfig, family: str) -> str:
    """Render one carousel page in the given editorial layout `family`
    (chosen upstream by _select_layout_family from the page's own meaning,
    not its role). Uses only page.headline/body/visual_data/image_data
    already produced upstream; no new content is authored here.
    """
    accent = brand.colors.get("violet", "#7848D8")
    accent2 = brand.colors.get("magenta", "#F04890")
    text_color = brand.colors.get("text_primary", "#241B31")
    bg_cycle = list(brand.backgrounds.values()) or ["#FFFFFF"]
    bg = bg_cycle[(page.page_number - 1) % len(bg_cycle)]

    vd = page.visual_data or {}
    tag = _pill_label_for_role(page.role)
    is_cta = family == "cta"

    if vd.get("type") == "bar_chart":
        # _select_layout_family routes this into "comparison" (2 values) or
        # "numbered_infographic" (3+); both renderers expect their own
        # shape (left/right dicts, or plain display strings) -- not raw
        # [label, value] pairs -- so normalize once here rather than
        # teaching every row-renderer about the bar_chart data shape.
        unit = vd.get("unit", "")
        pairs = [(p[0], p[1]) if isinstance(p, (list, tuple)) and len(p) >= 2 else (p, "") for p in (vd.get("items") or [])]
        if family == "comparison" and len(pairs) >= 2:
            (l_label, l_val), (r_label, r_val) = pairs[0], pairs[1]
            vd = {**vd, "left": {"title": str(l_label), "desc": f"{l_val}{unit}"}, "right": {"title": str(r_label), "desc": f"{r_val}{unit}"}}
        else:
            vd = {**vd, "items": [f"{label} {value}{unit}" for label, value in pairs]}

    # A distinct closing-strip sentence, mechanically split from the page's
    # OWN already-approved body text (never reworded/invented) -- the
    # repeating "so what" capstone every non-hook, non-CTA page ends on,
    # matching the reference carousel's rhythm. The hook (page 1) and CTA
    # already have their own strong closing treatment, so neither gets one.
    lead_body, closing_sentence = (_split_closing_sentence(page.body) if not is_cta else (page.body, ""))
    # stat_hero already uses closing_sentence as its own pull-quote panel
    # above -- showing it again as a bottom strip would duplicate the text.
    show_closing_strip = bool(closing_sentence) and page.page_number != 1 and family != "stat_hero"

    html = [_chrome_open(brand, bg, text_color)]
    html.append(_masthead(brand, accent, text_color, page.page_number, total_pages))
    html.append('<div style="flex:1;display:flex;flex-direction:column;margin-top:26px;min-height:0;">')
    html.append(_pill(tag, accent))
    html.append(_highlighted_headline(page.headline, accent))
    if not is_cta:
        html.append(_body_text(lead_body, text_color))

    if family == "editorial_photo":
        html.append(_photo_panel(page.image_data, tag, vd.get("highlight") or vd.get("big_text", ""), accent, flex="1 1 auto"))

    elif family == "photo_info_overlay":
        html.append(_photo_info_overlay(page.image_data, tag, vd, accent, flex="1 1 auto"))

    elif family == "hybrid_photo_checklist":
        # A narrow SIDE photo strip beside the short (<=3) list -- a
        # different photo silhouette from editorial_photo's full-bleed
        # top/bottom treatment, so a carousel with more than one photo
        # page doesn't repeat the same shape.
        items = vd.get("items") or []
        html.append(_photo_side_panel(page.image_data, items, accent, flex="1 1 auto"))

    elif family == "stat_hero":
        # Two genuine content blocks -- a focal number (content-sized, no
        # decorative ring) plus the page's own closing sentence styled as
        # a pull-quote -- instead of one shape trying to fill empty space.
        html.append(_focal_number_block(vd.get("big_text") or vd.get("highlight", ""), vd.get("sub_text", ""), accent))
        if closing_sentence:
            html.append(_pull_quote_panel(closing_sentence, accent, accent2))

    elif family == "process":
        items = vd.get("steps") or vd.get("items") or []
        rows_html = _numbered_rows(items, accent, flex="0 1 auto")
        label, value = _extract_accent_stat(page.body, items)
        html.append(_content_plus_accent(rows_html, label, value, accent, accent2))

    elif family == "checklist":
        # A highlight_box/stat-hero-eligible claim rejected/tie-broken into
        # checklist has no "items" list of its own -- fall back to its own
        # single short claim.
        items = vd.get("items") or ([vd["highlight"]] if vd.get("highlight") else ([vd["big_text"]] if vd.get("big_text") else []))
        rows_html = _check_rows(items, accent, flex="0 1 auto")
        label, value = _extract_accent_stat(page.body, items)
        html.append(_content_plus_accent(rows_html, label, value, accent, accent2))

    elif family == "numbered_infographic":
        items = vd.get("items") or vd.get("steps") or []
        rows_html = _infographic_numbered_rows(items, accent, accent2, flex="0 1 auto")
        label, value = _extract_accent_stat(page.body, items)
        html.append(_content_plus_accent(rows_html, label, value, accent, accent2))

    elif family == "card_grid":
        items = vd.get("items") or vd.get("steps") or []
        html.append(_card_grid_rows(items, accent, accent2, flex="0 1 auto"))

    elif family == "comparison":
        left, right = vd.get("left", {}), vd.get("right", {})
        html.append(_comparison_cards_v2(left, right, accent, accent2))

    elif family == "cta":
        steps, trailing = _extract_step_flow(page.body) if page.body else ([], "")
        if steps:
            html.append(_cta_designed(steps, trailing, vd.get("button_text", "확인하기"), accent, accent2))
        else:
            html.append(_cta_full(page.body or page.headline, vd.get("button_text", "확인하기"), vd.get("region", ""), accent, accent2, flex="0 1 auto"))

    if show_closing_strip:
        html.append(_closing_strip(closing_sentence, accent, text_color))

    html.append("</div>")
    html.append(_footer(brand, accent, accent2, text_color))
    html.append("</div>")
    return "".join(html)


def build_renderer_input(canonical: CanonicalContent, brand: BrandConfig) -> list:
    if not canonical.pages:
        raise ValueError("cannot build renderer input: canonical content has no generated pages")

    total = len(canonical.pages)
    result = []
    prev_family = None
    for page in canonical.pages:
        family = _select_layout_family(page, total, prev_family)
        prev_family = family
        result.append(
            {
                "page_number": page.page_number,
                "role": page.role,
                "layout_variant": family,
                "width": brand.canvas_width,
                "height": brand.canvas_height,
                "html": render_page_html(page, total, brand, family),
            }
        )
    return result
