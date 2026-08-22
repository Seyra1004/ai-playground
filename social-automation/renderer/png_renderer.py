from __future__ import annotations

import os

from PIL import Image

_PAGE_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body {{ margin:0; padding:0; }}
  @font-face {{
    font-family: 'Pretendard';
    src: local('Pretendard'), local('Malgun Gothic'), local('맑은 고딕');
  }}
</style></head><body>{page_html}</body></html>"""


def render_pages_to_png(renderer_input: list, out_dir: str) -> list:
    """Render each renderer_input page (HTML fragment + explicit width/height)
    to an exact-size PNG using the system-installed Chrome via Playwright
    (CDP), not a bundled/downloaded browser. Returns the list of PNG paths in
    page order.
    """
    from playwright.sync_api import sync_playwright  # local import: optional/heavy dep, only needed here

    os.makedirs(out_dir, exist_ok=True)
    paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            for page_render in renderer_input:
                width, height = page_render["width"], page_render["height"]
                page = browser.new_page(viewport={"width": width, "height": height})
                page.set_content(_PAGE_HTML_TEMPLATE.format(page_html=page_render["html"]))
                out_path = os.path.join(out_dir, f"page_{page_render['page_number']:02d}.png")
                page.screenshot(path=out_path)
                page.close()
                paths.append(out_path)
        finally:
            browser.close()

    return paths


def build_contact_sheet(png_paths: list, out_path: str, columns: int = 4) -> str:
    """Assemble a grid contact sheet (thumbnail preview) from rendered PNGs."""
    if not png_paths:
        raise ValueError("cannot build contact sheet: no PNG pages provided")

    thumb_w, thumb_h = 270, 338  # 1080x1350 scaled down 4x, keeps exact 4:5 ratio
    thumbs = []
    for p in png_paths:
        with Image.open(p) as im:
            thumbs.append(im.resize((thumb_w, thumb_h)))  # .resize() returns a new, independent image

    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (thumb_w * columns, thumb_h * rows), "white")
    for i, thumb in enumerate(thumbs):
        x = (i % columns) * thumb_w
        y = (i // columns) * thumb_h
        sheet.paste(thumb, (x, y))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    sheet.save(out_path)
    return out_path
