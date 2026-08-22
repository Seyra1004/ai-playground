from __future__ import annotations

from core.config import BrandConfig
from core.models import QAResult, QAStatus


def run_render_qa(renderer_input: list, brand: BrandConfig) -> QAResult:
    passed = []
    failed = []

    if brand.canvas_width == 1080 and brand.canvas_height == 1350:
        passed.append("canvas_size_correct")
    else:
        failed.append(f"canvas_size_incorrect:{brand.canvas_width}x{brand.canvas_height}")

    for page_render in renderer_input:
        if page_render.get("width") != 1080 or page_render.get("height") != 1350:
            failed.append(f"render_page_size_mismatch:page_{page_render.get('page_number')}")
    if renderer_input:
        passed.append("render_page_sizes_checked")

    status = QAStatus.FAIL if failed else QAStatus.PASS
    return QAResult(status=status, checks_passed=passed, checks_failed=failed)


_KOREAN_FONT_MARKERS = ("noto", "cjk", " kr", "gothic", "batang", "myeongjo", "pretendard")


def verify_korean_font_available() -> QAResult:
    """Deterministic PRE-render gate: refuses to proceed if the render
    host has no font that actually covers Hangul -- this is the real root
    cause of Korean text rendering as broken/tofu glyphs (confirmed
    2026-08-22: a fresh render host had zero CJK fonts installed, and
    dimension/structural QA alone reported PASS anyway since it never
    looks at what's inside the pixels). fc-match ALWAYS resolves to SOME
    family, even a Latin-only fallback, so a resolved family with no
    Korean-capable marker in its name is the actual failure signal here."""
    import shutil
    import subprocess

    if shutil.which("fc-match") is None:
        return QAResult(
            status=QAStatus.PASS,
            notes=["fc-match not available on this platform -- Korean font coverage not verified here (expected on non-Linux dev machines)"],
        )

    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{family}\n", "sans-serif:lang=ko"],
            capture_output=True, text=True, timeout=5,
        )
        family = (result.stdout or "").strip()
    except Exception as exc:
        return QAResult(status=QAStatus.FAIL, checks_failed=[f"fc-match invocation failed: {exc}"])

    if not family or not any(marker in family.lower() for marker in _KOREAN_FONT_MARKERS):
        return QAResult(
            status=QAStatus.FAIL,
            checks_failed=[
                f"no Korean-capable font resolved for sans-serif:lang=ko (fc-match returned {family!r}) "
                "-- Hangul would render as broken/tofu glyphs"
            ],
        )

    return QAResult(status=QAStatus.PASS, checks_passed=[f"korean_font_available:{family}"])


def verify_png_dimensions(png_paths: list, brand: BrandConfig) -> QAResult:
    """Post-render P0 check on the actual rendered files (not just the
    pre-render HTML spec) -- catches a real Playwright/viewport malfunction
    that the HTML-level check above cannot see."""
    from PIL import Image

    passed = []
    failed = []
    for path in png_paths:
        with Image.open(path) as im:
            size = im.size
        if size != (brand.canvas_width, brand.canvas_height):
            failed.append(f"png_dimension_mismatch:{path}:{size}")
    if png_paths and not failed:
        passed.append("png_dimensions_correct")

    status = QAStatus.FAIL if failed else QAStatus.PASS
    return QAResult(status=status, checks_passed=passed, checks_failed=failed)
