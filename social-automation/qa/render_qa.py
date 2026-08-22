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
