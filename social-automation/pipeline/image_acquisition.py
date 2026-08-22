from __future__ import annotations

"""Reusable, source-agnostic real-image acquisition for SWIPE_INFO. Given
ONE verified FactSheet Source (whatever topic/publisher/URL it happens to
be -- nothing here is topic-specific), discovers, downloads, and rights-
verifies genuinely reusable images, then deterministically assigns them to
carousel page roles. No AI generation, no paid image API, no Google Images
scraping, no unlicensed stock photos -- see module docstrings below for the
exact search order and rejection rules.

Never fabricates a substitute when nothing suitable is found: callers must
treat an empty accepted-image list as a legitimate, honest outcome (the
existing deterministic chart/checklist/process visuals stay in place).
"""

import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request

from core.models import Source

MIN_BYTES = 3000
MIN_DIMENSION = 150
MAX_CANDIDATES_TO_FETCH = 12
MAX_IMAGES = 4

_FILE_LINK_RE = re.compile(r'href="([^"]+\.(?:pdf|jpg|jpeg|png|webp))"', re.I)
_IMG_TAG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
_DECORATIVE_NAME_RE = re.compile(r"(logo|icon|btn|button|banner|bullet|spacer|arrow|bg_|background|pixel|track)", re.I)

# Rights gate: only source types with a clear public-reuse basis are ever
# considered. Search order (C) "other clearly reusable/public official
# sources" is intentionally not implemented here -- only (A)/(B) against the
# verified source itself, which is what "never fabricate/guess a source"
# actually allows without a semantic judgment call.
_ELIGIBLE_SOURCE_TYPES = {"government", "public_institution", "official_operator"}

# Deterministic role priority for image placement -- never a hardcoded page
# number. "hook" is deliberately excluded: it already carries the primary
# stat visual and stays that way regardless of images found.
_IMAGE_ELIGIBLE_ROLE_PRIORITY = ["why_now", "cta", "conditions", "examples", "eligibility"]

# Per-publisher attachment-discovery override. Most sites expose a plain
# `<a href="....pdf">`/`<img src="...">` link the generic scan below already
# finds; a few (like NTS) embed the real download URL in a JS-only blob with
# no literal .pdf extension in the href, which needs the site's own already-
# built adapter (reused, not duplicated) instead of the generic regex.
def _nts_attachment_urls(html: str, source_url: str) -> list:
    from pipeline.live_discovery import _find_nts_pdf_download_url

    found = _find_nts_pdf_download_url(html)
    return [found[0]] if found else []


_PUBLISHER_ATTACHMENT_FINDERS = {
    "국세청": _nts_attachment_urls,
}


def _fetch(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SWIPE_INFO image acquisition)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _same_domain(candidate_url: str, source_url: str) -> bool:
    return urllib.parse.urlparse(candidate_url).netloc == urllib.parse.urlparse(source_url).netloc


def _image_dimensions(data: bytes):
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return (0, 0)


def _discover_candidate_urls(source: Source, html: str) -> list:
    """Returns [(url, is_pdf), ...]. Search order A (document embedded in
    the source) then B (images linked/embedded on the source's own page)."""
    candidates = []

    if source.url.lower().split("?")[0].endswith(".pdf"):
        candidates.append((source.url, True))

    finder = _PUBLISHER_ATTACHMENT_FINDERS.get(source.publisher)
    if finder is not None:
        for url in finder(html, source.url):
            candidates.append((url, True))

    for href in _FILE_LINK_RE.findall(html):
        url = urllib.parse.urljoin(source.url, href)
        candidates.append((url, url.lower().split("?")[0].endswith(".pdf")))
    for src in _IMG_TAG_RE.findall(html):
        url = urllib.parse.urljoin(source.url, src)
        candidates.append((url, False))

    return candidates[:MAX_CANDIDATES_TO_FETCH]


def _maybe_accept(data: bytes, filename: str, origin_url: str, method: str, source: Source, out_dir: str, accepted: list, seen_hashes: set, max_images: int) -> None:
    if len(accepted) >= max_images:
        return
    if len(data) < MIN_BYTES:
        return  # tiny/tracking-pixel/icon rejection by size
    if _DECORATIVE_NAME_RE.search(filename):
        return  # decorative logo/icon/banner/tracking-pixel filename rejection
    digest = hashlib.sha256(data).hexdigest()
    if digest in seen_hashes:
        return  # duplicate rejection
    w, h = _image_dimensions(data)
    if w < MIN_DIMENSION or h < MIN_DIMENSION:
        return  # tiny-dimension rejection (also rejects non-image/corrupt data, which decodes to 0x0)
    seen_hashes.add(digest)

    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    safe_name = f"img_{digest[:12]}{ext}"
    path = os.path.join(out_dir, safe_name)
    with open(path, "wb") as f:
        f.write(data)

    accepted.append(
        {
            "file": safe_name,
            "path": path,
            "source_url": origin_url,
            "publisher": source.publisher,
            "description": f"Image acquired from the verified official source ({source.url})",
            "rights": "Same-origin asset from a verified government/public-institution source; used for citation/informational purposes only, not re-edited.",
            "acquisition_method": method,
            "width": w,
            "height": h,
            "bytes": len(data),
        }
    )


def _load_cached(out_dir: str) -> list:
    path = os.path.join(out_dir, "asset_sources.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    result = []
    for e in entries:
        file_path = os.path.join(out_dir, e["file"])
        if os.path.isfile(file_path):
            result.append({**e, "path": file_path})
    return result


def _write_sources_json(out_dir: str, accepted: list) -> None:
    path = os.path.join(out_dir, "asset_sources.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in a.items() if k != "path"} for a in accepted], f, ensure_ascii=False, indent=2)


def discover_and_acquire_images(source: Source, out_dir: str, max_images: int = MAX_IMAGES) -> list:
    """Main entry point. Idempotent: if out_dir already has a valid
    asset_sources.json from a prior run for this exact source, reuses it
    without any network call. Returns [] (never raises, never fabricates)
    if no legally-clear, sufficiently-real image can be found."""
    if source.source_type.value not in _ELIGIBLE_SOURCE_TYPES:
        return []

    cached = _load_cached(out_dir)
    if cached:
        return cached[:max_images]

    os.makedirs(out_dir, exist_ok=True)
    try:
        html = _fetch(source.url).decode("utf-8", errors="replace")
    except Exception:
        html = ""

    accepted = []
    seen_hashes = set()

    for url, is_pdf in _discover_candidate_urls(source, html):
        if len(accepted) >= max_images:
            break
        if not _same_domain(url, source.url):
            continue  # rights gate: only same-origin as the verified official source
        try:
            data = _fetch(url, timeout=15)
        except Exception:
            continue

        if is_pdf:
            try:
                from pypdf import PdfReader

                reader = PdfReader(io.BytesIO(data))
                for page in reader.pages:
                    for img in page.images:
                        _maybe_accept(img.data, img.name, url, "embedded-in-document", source, out_dir, accepted, seen_hashes, max_images)
                        if len(accepted) >= max_images:
                            break
                    if len(accepted) >= max_images:
                        break
            except Exception:
                continue
        else:
            filename = os.path.basename(urllib.parse.urlparse(url).path) or "image.jpg"
            _maybe_accept(data, filename, url, "linked-on-official-page", source, out_dir, accepted, seen_hashes, max_images)

    if accepted:
        _write_sources_json(out_dir, accepted)
    return accepted


def assign_images_to_pages(pages: list, accepted_images: list) -> list:
    """Deterministic role-priority placement -- never a hardcoded page
    number, never a Claude call. Only touches as many pages as there are
    accepted images, in _IMAGE_ELIGIBLE_ROLE_PRIORITY order, and only roles
    actually present in this topic's page plan. Caption is a prefix of the
    page's own real headline, so it's relevant by construction. Returns the
    list of page_numbers that were changed."""
    if not accepted_images:
        return []

    by_role = {p.role: p for p in pages}
    changed = []
    image_idx = 0
    for role in _IMAGE_ELIGIBLE_ROLE_PRIORITY:
        if image_idx >= len(accepted_images):
            break
        page = by_role.get(role)
        if page is None:
            continue
        img = accepted_images[image_idx]
        page.visual_data = {
            "type": "real_image",
            "image_path": img["path"],
            "caption": (page.headline or "")[:20],
        }
        changed.append(page.page_number)
        image_idx += 1
    return changed
