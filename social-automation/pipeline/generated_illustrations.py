from __future__ import annotations

"""Deterministic, ZERO-PAYG standalone editorial illustration generator --
the Priority-4 image fallback for SWIPE_INFO's real-image acquisition chain.
Used only when no genuine external image (Priority 1-3, pipeline.
image_acquisition) is available for a page.

Draws an original flat-design scene with PIL primitives -- no AI/LLM model,
no external network call, and NOT a screenshot of the existing HTML/CSS
carousel components (a fully separate raster-drawing pipeline). No text or
numbers are ever drawn (nothing here can fabricate a fact). The scene is
selected generically by the page's ROLE (hook/why_now/eligibility/... --
the same fixed role vocabulary every topic already uses via
core/page_selector.py), never by today's specific wording, so it needs no
per-topic maintenance.
"""

import hashlib
import os

from PIL import Image, ImageDraw

_CANVAS = (960, 720)


def _hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _tint(c, amount):
    """Blend a color toward white -- amount 0=full color, 1=white."""
    return _lerp(c, (255, 255, 255), amount)


def _gradient_bg(draw, size, c1, c2):
    w, h = size
    for y in range(h):
        draw.line([(0, y), (w, y)], fill=_lerp(c1, c2, y / max(h - 1, 1)))


def _rrect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


# --- scene primitives -------------------------------------------------
# Each scene(draw, cx, cy, fg, accent, variant) draws one self-contained
# icon-like composition centered near (cx, cy). `variant` (0-4) nudges
# layout/size deterministically so repeated roles across a carousel, or
# the same role on a different topic, don't render pixel-identical.


def _scene_shield_alert(draw, cx, cy, fg, accent, variant):
    s = 170 + variant * 8
    pts = [
        (cx, cy - s), (cx + s * 0.8, cy - s * 0.55), (cx + s * 0.8, cy + s * 0.2),
        (cx, cy + s * 1.05), (cx - s * 0.8, cy + s * 0.2), (cx - s * 0.8, cy - s * 0.55),
    ]
    draw.polygon(pts, fill=_tint(fg, 0.15), outline=fg, width=6)
    bar_h = s * 0.7
    draw.rounded_rectangle(
        [cx - 14, cy - bar_h * 0.55, cx + 14, cy - bar_h * 0.55 + bar_h * 0.65], radius=12, fill=accent
    )
    draw.ellipse([cx - 14, cy + bar_h * 0.28, cx + 14, cy + bar_h * 0.28 + 28], fill=accent)


def _scene_calendar(draw, cx, cy, fg, accent, variant):
    w, h = 320, 280
    box = [cx - w / 2, cy - h / 2 + 10, cx + w / 2, cy + h / 2 + 10]
    _rrect(draw, box, 24, fill=_tint(fg, 0.85), outline=fg, width=6)
    draw.rectangle([box[0], box[1], box[2], box[1] + 60], fill=fg)
    for i in range(2):
        x = cx - 70 + i * 140
        draw.rounded_rectangle([x - 10, box[1] - 24, x + 10, box[1] + 20], radius=8, fill=accent)
    cell = 46
    gx0, gy0 = cx - cell * 1.5, box[1] + 90
    highlight = (1 + variant) % 6
    n = 0
    for r in range(2):
        for c in range(3):
            x0, y0 = gx0 + c * cell, gy0 + r * cell
            fill = accent if n == highlight else _tint(fg, 0.6)
            draw.rounded_rectangle([x0, y0, x0 + cell - 10, y0 + cell - 10], radius=8, fill=fill)
            n += 1


def _scene_people(draw, cx, cy, fg, accent, variant):
    count = 3
    spacing = 150
    colors = [fg, accent, _tint(fg, 0.35)]
    start_x = cx - spacing * (count - 1) / 2
    for i in range(count):
        x = start_x + i * spacing + (variant % 3) * 6
        color = colors[i % len(colors)]
        draw.ellipse([x - 38, cy - 140, x + 38, cy - 64], fill=color)
        draw.rounded_rectangle([x - 60, cy - 60, x + 60, cy + 120], radius=40, fill=color)


def _scene_document(draw, cx, cy, fg, accent, variant):
    w, h = 260, 320
    box = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
    _rrect(draw, box, 18, fill=_tint(fg, 0.85), outline=fg, width=6)
    for i in range(4):
        y = box[1] + 55 + i * 44
        line_w = w * (0.75 if i % 2 == 0 else 0.5)
        draw.rounded_rectangle([box[0] + 26, y, box[0] + 26 + line_w, y + 16], radius=8, fill=_tint(fg, 0.35))
    r = 46 + variant * 3
    draw.ellipse([cx + w / 2 - r * 0.6, cy + h / 2 - r * 0.6, cx + w / 2 - r * 0.6 + r * 2, cy + h / 2 - r * 0.6 + r * 2], fill=accent)


def _scene_flow(draw, cx, cy, fg, accent, variant):
    box_w, box_h, gap = 300, 76, 46
    total = box_h * 3 + gap * 2
    y0 = cy - total / 2
    colors = [fg, accent, fg]
    for i in range(3):
        y = y0 + i * (box_h + gap)
        _rrect(draw, [cx - box_w / 2, y, cx + box_w / 2, y + box_h], 20, fill=_tint(colors[i % len(colors)], 0.2), outline=colors[i % len(colors)], width=5)
        if i < 2:
            ay = y + box_h + gap / 2
            draw.line([(cx, ay - gap / 2 + 6), (cx, ay + gap / 2 - 10)], fill=fg, width=6)
            draw.polygon([(cx - 10, ay + gap / 2 - 14), (cx + 10, ay + gap / 2 - 14), (cx, ay + gap / 2 + 2)], fill=fg)


def _scene_steps(draw, cx, cy, fg, accent, variant):
    n = 3
    spacing = 220
    start_x = cx - spacing * (n - 1) / 2
    y = cy
    draw.line([(start_x, y), (start_x + spacing * (n - 1), y)], fill=_tint(fg, 0.4), width=6)
    for i in range(n):
        x = start_x + i * spacing
        r = 46
        color = accent if i == (variant % n) else fg
        draw.ellipse([x - r, y - r, x + r, y + r], fill=_tint(color, 0.15), outline=color, width=6)


def _scene_balance(draw, cx, cy, fg, accent, variant):
    draw.line([(cx, cy - 110), (cx, cy + 40)], fill=fg, width=8)
    draw.line([(cx - 160, cy - 60), (cx + 160, cy - 60)], fill=fg, width=8)
    for i, dx in enumerate((-160, 160)):
        color = fg if i == 0 else accent
        pan_y = cy - 60 + (18 if i == variant % 2 else -6)
        draw.arc([cx + dx - 70, pan_y, cx + dx + 70, pan_y + 60], 0, 180, fill=color, width=8)
    draw.rounded_rectangle([cx - 90, cy + 30, cx + 90, cy + 70], radius=16, fill=_tint(fg, 0.3))


def _scene_warning(draw, cx, cy, fg, accent, variant):
    s = 180
    draw.polygon([(cx, cy - s), (cx + s * 0.95, cy + s * 0.7), (cx - s * 0.95, cy + s * 0.7)], fill=_tint(accent, 0.15), outline=accent, width=7)
    draw.rounded_rectangle([cx - 12, cy - 60, cx + 12, cy + 40], radius=10, fill=accent)
    draw.ellipse([cx - 12, cy + 62, cx + 12, cy + 86], fill=accent)


def _scene_spotlight(draw, cx, cy, fg, accent, variant):
    r = 110
    lx, ly = cx - 30, cy - 30
    draw.ellipse([lx - r, ly - r, lx + r, ly + r], outline=fg, width=10, fill=_tint(fg, 0.85))
    draw.line([(lx + r * 0.7, ly + r * 0.7), (lx + r * 1.6, ly + r * 1.6)], fill=fg, width=20)
    for i in range(3):
        draw.rounded_rectangle([lx - 40, ly - 20 + i * 26, lx + 40, ly - 6 + i * 26], radius=6, fill=_tint(accent, 0.2 + i * 0.1))


def _scene_megaphone(draw, cx, cy, fg, accent, variant):
    draw.polygon(
        [(cx - 120, cy - 40), (cx + 40, cy - 100), (cx + 40, cy + 100), (cx - 120, cy + 40)],
        fill=_tint(accent, 0.1), outline=accent, width=6,
    )
    draw.rounded_rectangle([cx - 150, cy - 30, cx - 110, cy + 30], radius=10, fill=fg)
    for i, r in enumerate((60, 95, 130)):
        bbox = [cx + 40 - r * 0.3, cy - r * 0.6, cx + 40 + r, cy + r * 0.6]
        draw.arc(bbox, -40, 40, fill=_tint(fg, i * 0.15), width=7)


_ROLE_SCENES = {
    "hook": _scene_shield_alert,
    "why_now": _scene_calendar,
    "eligibility": _scene_people,
    "amount": _scene_document,
    "conditions": _scene_flow,
    "comparison": _scene_balance,
    "exclusions": _scene_warning,
    "procedure": _scene_steps,
    "warnings": _scene_warning,
    "examples": _scene_spotlight,
    "cta": _scene_megaphone,
}


def generate_editorial_illustration(role: str, page_number: int, out_dir: str, brand) -> dict:
    """Deterministic per (role, page_number, brand palette) -- same inputs
    always produce the same file (idempotent, cacheable), while varying
    color pairing/layout by page_number so no two pages in one carousel
    render identically even when a role repeats. Never raises for an
    unrecognized role -- falls back to a generic document scene."""
    os.makedirs(out_dir, exist_ok=True)
    scene_fn = _ROLE_SCENES.get(role, _scene_document)

    palette = [c for c in brand.colors.values() if isinstance(c, str) and c.startswith("#")]
    if len(palette) < 2:
        palette = ["#7848D8", "#F04890"]
    seed = int(hashlib.sha256(f"{role}:{page_number}".encode()).hexdigest()[:8], 16)
    fg = _hex_to_rgb(palette[seed % len(palette)])
    accent = _hex_to_rgb(palette[(seed // 7 + 1) % len(palette)])
    variant = seed % 5

    img = Image.new("RGB", _CANVAS, _tint(fg, 0.92))
    draw = ImageDraw.Draw(img)
    _gradient_bg(draw, _CANVAS, _tint(fg, 0.9), _tint(accent, 0.88))
    draw = ImageDraw.Draw(img)  # redraw handle after gradient paints over the base
    scene_fn(draw, _CANVAS[0] // 2, _CANVAS[1] // 2, fg, accent, variant)

    fname = f"generated_{role}_{page_number}.png"
    path = os.path.join(out_dir, fname)
    img.save(path, "PNG")

    return {
        "file": fname,
        "path": path,
        "source_url": None,
        "publisher": "SWIPE_INFO",
        "description": f"Original deterministically-generated editorial illustration for the '{role}' page (no AI model, no external source, no text/numbers).",
        "rights": "Internally generated original asset -- no external source, no rights restriction.",
        "acquisition_method": "generated_editorial_asset",
        "width": _CANVAS[0],
        "height": _CANVAS[1],
        "bytes": os.path.getsize(path),
    }
