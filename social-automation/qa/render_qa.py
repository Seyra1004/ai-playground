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
