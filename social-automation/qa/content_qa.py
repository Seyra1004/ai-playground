from __future__ import annotations

import json
import os

from core.factcheck import validate_fact_sheet_claims
from core.models import CanonicalContent, InstagramContent, QAResult, QAStatus

# Configurable per-page body density guardrail for readable mobile carousel text.
MAX_BODY_CHARS = 220


def run_content_qa(
    canonical: CanonicalContent,
    instagram_content: InstagramContent,
    pages_min: int,
    pages_max: int,
) -> QAResult:
    passed = []
    failed = []
    notes = []

    page_count = len(canonical.pages)
    if pages_min <= page_count <= pages_max:
        passed.append("page_count_in_range")
    else:
        failed.append(f"page_count_out_of_range:{page_count}")

    roles_seen = []
    for page in canonical.pages:
        if not page.headline or not page.headline.strip():
            failed.append(f"missing_headline:page_{page.page_number}")
        if not page.visual_ref or not page.visual_ref.strip():
            failed.append(f"missing_visual_ref:page_{page.page_number}")
        if page.body and len(page.body) > MAX_BODY_CHARS:
            failed.append(f"body_too_dense:page_{page.page_number}:{len(page.body)}chars")
        if page.role in roles_seen:
            failed.append(f"duplicate_page_role:{page.role}")
        roles_seen.append(page.role)

    if canonical.pages:
        passed.append("headline_and_visual_checks_ran")

    if canonical.pages and canonical.pages[-1].role == "cta" and canonical.pages[-1].body.strip():
        passed.append("final_page_has_cta")
    else:
        failed.append("final_page_missing_cta")

    fact_status, _claim_results = validate_fact_sheet_claims(
        canonical.fact_sheet.claims, canonical.fact_sheet.sources
    )
    if fact_status == QAStatus.PASS:
        passed.append("source_linkage_ok")
    elif fact_status == QAStatus.NEEDS_REVIEW:
        notes.append("source_linkage_needs_review")
    else:
        failed.append("source_linkage_failed")

    if instagram_content is not None:
        if len(instagram_content.pages) == page_count:
            passed.append("instagram_page_count_matches_canonical")
        else:
            failed.append("instagram_page_count_mismatch")

    if failed:
        status = QAStatus.FAIL
    elif fact_status == QAStatus.NEEDS_REVIEW:
        status = QAStatus.NEEDS_REVIEW
    else:
        status = QAStatus.PASS

    return QAResult(status=status, checks_passed=passed, checks_failed=failed, notes=notes)


# Common Korean particles (josa)/verb-ending fragments that attach directly
# to a word with no space -- stripped (longest match first, generic across
# any word/topic) so relevance matching compares word STEMS instead of exact
# surface forms. Without this, "가족에게" (visual) vs "가족과"/"가족은" (page
# text) share the same root "가족" but never literally contain each other,
# producing a false "unrelated" verdict despite being the same word.
_KOREAN_PARTICLE_SUFFIXES = sorted(
    [
        "으로부터", "에게서", "에서는", "으로써", "이라도", "이라서", "이랑은",
        "라도", "라서", "에게", "께서", "에는", "으로", "에서", "부터", "까지",
        "마저", "조차", "마다", "같이", "처럼", "이나", "나마", "이란", "이랑",
        "보다", "와는", "과는", "은", "는", "이", "가", "을", "를", "의", "에",
        "로", "와", "과", "도", "만", "나", "란", "랑", "라",
    ],
    key=len,
    reverse=True,
)


def _korean_stem(word: str) -> str:
    for suffix in _KOREAN_PARTICLE_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            return word[: -len(suffix)]
    return word


def _korean_root_overlap(a: str, b: str) -> bool:
    """Fallback for verb/adjective conjugation variance that particle-
    stripping alone doesn't cover (e.g. "공유하기" vs "공유해" -- different
    endings on the same "공유" root). Two stems overlap if one contains the
    other, or -- only when BOTH are at least 3 chars, to avoid loosely
    matching short/common syllables -- if they share the same 2-char leading
    root (each Hangul syllable block is a full phonetic unit, so a shared
    2-char prefix on longer words is a meaningfully specific match)."""
    if a in b or b in a:
        return True
    if len(a) < 3 or len(b) < 3:
        return False
    return a[:2] == b[:2]


def check_visual_quality(canonical: CanonicalContent) -> QAResult:
    """Deterministic visual-relevance QA: a page with no visual_data at all
    -> FAIL, the exact same visual_data reused verbatim on 2+ pages -> FAIL
    (a real evidence card/chart/diagram should never be identical across
    pages), and a visual whose own text shares no token with its page's
    headline/body -> NEEDS_REVIEW (a deterministic proxy for "this doesn't
    look related to the page" -- not a semantic judgment call)."""
    import json as _json

    passed, failed, notes = [], [], []
    seen_signatures = {}

    for page in canonical.pages:
        vd = page.visual_data or {}
        if not vd or not vd.get("type"):
            failed.append(f"missing_visual:page_{page.page_number}")
            continue

        signature = _json.dumps(vd, sort_keys=True, ensure_ascii=False)
        if signature in seen_signatures:
            failed.append(f"duplicate_visual:page_{page.page_number}_matches_page_{seen_signatures[signature]}")
        else:
            seen_signatures[signature] = page.page_number

        visual_strings = []
        for v in vd.values():
            if isinstance(v, (str, int, float)):
                visual_strings.append(str(v))
            elif isinstance(v, dict):
                visual_strings.extend(str(x) for x in v.values() if isinstance(x, (str, int, float)))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        visual_strings.extend(str(x) for x in item.values() if isinstance(x, (str, int, float)))
                    else:
                        visual_strings.append(str(item))
        # Substring containment, not exact token equality: Korean particles
        # attach directly to nouns with no space ("국세청" vs "국세청이"), so a
        # whitespace-token set intersection would false-flag obviously
        # relevant visuals. A visual word appearing anywhere inside the
        # page text (or vice versa) is treated as a real match.
        visual_words = {w for s in visual_strings for w in s.replace(",", " ").split() if len(w) >= 2}
        page_text = f"{page.headline} {page.body}"
        page_words = {w for w in page_text.replace(",", " ").split() if len(w) >= 2}
        has_overlap = any(w in page_text for w in visual_words) or any(w in " ".join(visual_strings) for w in page_words)
        if not has_overlap:
            # Raw substring containment misses matches that differ only by a
            # Korean particle ("가족에게" vs "가족과") or a verb/adjective
            # ending ("공유하기" vs "공유해") on the same word -- compare
            # stemmed roots as a fallback before concluding the visual is
            # actually unrelated to the page.
            visual_stems = {_korean_stem(w) for w in visual_words}
            page_stems = {_korean_stem(w) for w in page_words}
            has_overlap = any(_korean_root_overlap(vs, ps) for vs in visual_stems for ps in page_stems)
        if visual_words and page_words and not has_overlap:
            notes.append(f"visual_relevance_uncertain:page_{page.page_number}")

    if canonical.pages:
        passed.append("visual_presence_checked")

    if failed:
        status = QAStatus.FAIL
    elif notes:
        status = QAStatus.NEEDS_REVIEW
    else:
        status = QAStatus.PASS

    return QAResult(status=status, checks_passed=passed, checks_failed=failed, notes=notes)


def check_real_images(canonical: CanonicalContent, min_files: int = 2, min_pages: int = 2) -> QAResult:
    """Hard QA gate for real_image visuals: CSS/emoji/generated diagrams are
    explicitly NOT counted here -- only visual_data.type == "real_image"
    pages count. Verifies (a) the referenced image file actually exists on
    disk, (b) it has a source/rights entry in the sibling
    asset_sources.json, and (c) the min file/page counts are met. FAILs
    (never a silent PASS) if real images can't be verified."""
    passed, failed = [], []
    image_paths_used = set()

    for page in canonical.pages:
        vd = page.visual_data or {}
        if vd.get("type") != "real_image":
            continue

        image_path = vd.get("image_path", "")
        if not image_path or not os.path.isfile(image_path):
            failed.append(f"real_image_file_missing:page_{page.page_number}:{image_path!r}")
            continue

        sources_path = os.path.join(os.path.dirname(image_path), "asset_sources.json")
        filename = os.path.basename(image_path)
        has_metadata = False
        if os.path.isfile(sources_path):
            with open(sources_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            has_metadata = any(e.get("file") == filename and e.get("publisher") and e.get("rights") for e in entries)
        if not has_metadata:
            failed.append(f"real_image_missing_source_rights_metadata:page_{page.page_number}:{filename}")
            continue

        image_paths_used.add(image_path)

    real_image_pages = sum(1 for p in canonical.pages if (p.visual_data or {}).get("type") == "real_image")

    if not failed:
        if len(image_paths_used) < min_files:
            failed.append(f"real_image_file_count_below_minimum:{len(image_paths_used)}<{min_files}")
        if real_image_pages < min_pages:
            failed.append(f"real_image_page_count_below_minimum:{real_image_pages}<{min_pages}")

    if not failed:
        passed.append(f"real_images_verified:{len(image_paths_used)}_files_{real_image_pages}_pages")

    status = QAStatus.FAIL if failed else QAStatus.PASS
    return QAResult(status=status, checks_passed=passed, checks_failed=failed)
