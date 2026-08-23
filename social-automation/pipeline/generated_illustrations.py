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
import math
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


def _bg_accents(draw, cx, cy, fg, accent, variant):
    """Soft off-center background shapes drawn behind every scene so it
    reads as a small composed illustration -- not one icon floating alone
    in empty space (the "generic icon-pack" look this replaces)."""
    offsets = [(-260, -190, 42), (255, 195, 34), (-225, 210, 24), (235, -185, 28)]
    for i, (dx, dy, r) in enumerate(offsets):
        color = accent if i % 2 else fg
        draw.ellipse([cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r], fill=_tint(color, 0.8 - (variant % 3) * 0.05))


def _scene_shield_alert(draw, cx, cy, fg, accent, variant):
    # Scene, not one icon: a phone under threat (danger burst) being
    # actively blocked by a shield, off to the side -- "the call, and the
    # protection stopping it" rather than a lone warning glyph.
    px, py = cx - 130, cy + 10
    draw.rounded_rectangle([px - 60, py - 110, px + 60, py + 110], radius=26, fill=_tint(fg, 0.15), outline=fg, width=6)
    draw.rounded_rectangle([px - 34, py + 70, px + 34, py + 92], radius=10, fill=fg)
    for i, ang in enumerate((200, 235, 270)):
        r0, r1 = 74, 74 + 26 + i * 8
        rad = math.radians(ang)
        draw.line(
            [(px + r0 * math.cos(rad), py + r0 * math.sin(rad)), (px + r1 * math.cos(rad), py + r1 * math.sin(rad))],
            fill=accent, width=8,
        )
    draw.line([(px - 76, py - 76), (px + 76, py + 76)], fill=accent, width=14)

    sx, sy = cx + 140, cy - 10
    s = 130 + variant * 6
    pts = [
        (sx, sy - s), (sx + s * 0.78, sy - s * 0.5), (sx + s * 0.78, sy + s * 0.2),
        (sx, sy + s * 1.0), (sx - s * 0.78, sy + s * 0.2), (sx - s * 0.78, sy - s * 0.5),
    ]
    draw.polygon(pts, fill=_tint(accent, 0.12), outline=accent, width=6)
    draw.line([(sx - 28, sy), (sx - 8, sy + 24)], fill=accent, width=12)
    draw.line([(sx - 8, sy + 24), (sx + 34, sy - 26)], fill=accent, width=12)


def _scene_calendar(draw, cx, cy, fg, accent, variant):
    # Calendar plus a "new announcement" bell badge and a small clock --
    # date + urgency + notice, not a bare calendar icon.
    w, h = 300, 260
    box = [cx - w / 2 - 40, cy - h / 2 + 10, cx + w / 2 - 40, cy + h / 2 + 10]
    _rrect(draw, box, 24, fill=_tint(fg, 0.85), outline=fg, width=6)
    draw.rectangle([box[0], box[1], box[2], box[1] + 56], fill=fg)
    for i in range(2):
        x = box[0] + w * 0.28 + i * w * 0.44
        draw.rounded_rectangle([x - 9, box[1] - 22, x + 9, box[1] + 18], radius=8, fill=accent)
    cell = 42
    gx0, gy0 = box[0] + 28, box[1] + 84
    highlight = (1 + variant) % 6
    n = 0
    for r in range(2):
        for c in range(3):
            x0, y0 = gx0 + c * cell, gy0 + r * cell
            fill = accent if n == highlight else _tint(fg, 0.55)
            draw.rounded_rectangle([x0, y0, x0 + cell - 10, y0 + cell - 10], radius=8, fill=fill)
            n += 1

    bx, by = box[2] - 6, box[1] + 4
    draw.ellipse([bx - 34, by - 34, bx + 34, by + 34], fill=accent, outline="white", width=5)
    draw.polygon([(bx - 12, by + 6), (bx + 12, by + 6), (bx, by + 20)], fill="white")
    draw.rounded_rectangle([bx - 10, by - 16, bx + 10, by + 8], radius=6, fill="white")

    clx, cly = box[0] - 6, box[3] - 4
    draw.ellipse([clx - 30, cly - 30, clx + 30, cly + 30], outline=fg, width=6, fill=_tint(fg, 0.9))
    draw.line([(clx, cly), (clx, cly - 16)], fill=fg, width=5)
    draw.line([(clx, cly), (clx + 12, cly + 4)], fill=fg, width=5)


def _scene_people(draw, cx, cy, fg, accent, variant):
    # People plus a covering arc ("included/protected together") and a
    # checkmark badge on one figure -- "this group is covered", not a bare
    # row of identical pictograms.
    count = 3
    spacing = 145
    heights = (0, -18, 10)  # slightly uneven heights so figures aren't identical
    colors = [fg, accent, _tint(fg, 0.35)]
    start_x = cx - spacing * (count - 1) / 2
    arc_top = cy - 210
    draw.arc([cx - spacing * 1.35, arc_top, cx + spacing * 1.35, arc_top + 220], 200, 340, fill=_tint(accent, 0.3), width=8)
    for i in range(count):
        x = start_x + i * spacing + (variant % 3) * 6
        y = cy + heights[i]
        color = colors[i % len(colors)]
        draw.ellipse([x - 38, y - 140, x + 38, y - 64], fill=color)
        draw.rounded_rectangle([x - 58, y - 60, x + 58, y + 118], radius=38, fill=color)
    bx, by = start_x + spacing, cy - 150
    draw.ellipse([bx - 24, by - 24, bx + 24, by + 24], fill=accent, outline="white", width=4)
    draw.line([(bx - 10, by), (bx - 2, by + 9)], fill="white", width=5)
    draw.line([(bx - 2, by + 9), (bx + 12, by - 10)], fill="white", width=5)


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
        color = colors[i % len(colors)]
        _rrect(draw, [cx - box_w / 2, y, cx + box_w / 2, y + box_h], 20, fill=_tint(color, 0.2), outline=color, width=5)
        # a small marker inside each box so it reads as a filled step, not
        # an empty placeholder rectangle
        mx, my = cx - box_w / 2 + 44, y + box_h / 2
        draw.ellipse([mx - 16, my - 16, mx + 16, my + 16], fill=color)
        for lx0, ly0, lx1, ly1 in [(mx - 7, my, mx - 2, my + 7), (mx - 2, my + 7, mx + 9, my - 7)]:
            draw.line([(lx0, ly0), (lx1, ly1)], fill="white", width=4)
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
    # A clear before/after "X vs check" pair -- not an abstract balance
    # scale -- so a comparison-role page reads unambiguously at a glance.
    r = 96
    for i, dx in enumerate((-150, 150)):
        color = fg if i == 0 else accent
        lx = cx + dx
        draw.ellipse([lx - r, cy - r, lx + r, cy + r], outline=color, width=8, fill=_tint(color, 0.85))
        if i == 0:
            draw.line([(lx - 34, cy - 34), (lx + 34, cy + 34)], fill=color, width=14)
            draw.line([(lx - 34, cy + 34), (lx + 34, cy - 34)], fill=color, width=14)
        else:
            draw.line([(lx - 34, cy + 4), (lx - 8, cy + 32)], fill=color, width=14)
            draw.line([(lx - 8, cy + 32), (lx + 40, cy - 30)], fill=color, width=14)
    # A visible transformation arrow between the two states, not just a
    # small triangle -- the "before -> after" connector should read clearly.
    draw.line([(cx - 42, cy), (cx + 26, cy)], fill=_tint(accent, 0.15), width=10)
    draw.polygon([(cx + 18, cy - 22), (cx + 18, cy + 22), (cx + 52, cy)], fill=_tint(accent, 0.15))


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
    # Megaphone broadcasting toward two small figures receiving it --
    # "sharing protects people", not a lone speaker icon.
    mx = cx - 150
    draw.polygon(
        [(mx - 120, cy - 40), (mx + 40, cy - 100), (mx + 40, cy + 100), (mx - 120, cy + 40)],
        fill=_tint(accent, 0.1), outline=accent, width=6,
    )
    draw.rounded_rectangle([mx - 150, cy - 30, mx - 110, cy + 30], radius=10, fill=fg)
    for i, r in enumerate((55, 85, 115)):
        bbox = [mx + 40 - r * 0.3, cy - r * 0.6, mx + 40 + r, cy + r * 0.6]
        draw.arc(bbox, -40, 40, fill=_tint(fg, i * 0.15), width=7)

    for i, (dx, dy) in enumerate(((110, -70), (150, 60))):
        px, py = cx + dx, cy + dy
        color = fg if i == 0 else accent
        draw.ellipse([px - 20, py - 46, px + 20, py - 6], fill=color)
        draw.rounded_rectangle([px - 32, py - 4, px + 32, py + 62], radius=24, fill=color)


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


def generate_editorial_illustration(role: str, page_number: int, out_dir: str, brand, visual_type: str = None) -> dict:
    """Deterministic per (role, page_number, brand palette) -- same inputs
    always produce the same file (idempotent, cacheable), while varying
    color pairing/layout by page_number so no two pages in one carousel
    render identically even when a role repeats. Never raises for an
    unrecognized role -- falls back to a generic document scene.

    visual_type (the page's own informative visual_data.type, e.g.
    "comparison") is a stronger content signal than role alone -- a page
    whose role is generic ("examples") but whose actual content is a
    before/after comparison should still get the comparison scene, not a
    role-only fallback. Never a page-number-specific rule."""
    os.makedirs(out_dir, exist_ok=True)
    if visual_type == "comparison":
        scene_fn = _scene_balance
    else:
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
    cx, cy = _CANVAS[0] // 2, _CANVAS[1] // 2
    _bg_accents(draw, cx, cy, fg, accent, variant)
    scene_fn(draw, cx, cy, fg, accent, variant)

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
