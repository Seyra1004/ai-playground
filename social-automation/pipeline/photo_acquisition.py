from __future__ import annotations

"""Reusable, ZERO-PAYG real-photo acquisition for SWIPE_INFO -- Priority
1-2 of the image chain (verified official-source images stay in pipeline.
image_acquisition; this module is the next fallback: rights-clear web
photography), used for ANY future topic, never hardcoded to one date/story.

Source: Wikimedia Commons' public search API (no API key, no login, no
PAYG) -- every result carries its own machine-readable license metadata
(extmetadata.LicenseShortName/UsageTerms), which this module checks before
accepting anything. Search concepts are derived generically from the
page's ROLE and the fact sheet's CATEGORY (both already computed for every
topic by the existing pipeline), never from today's specific headline
wording, so nothing here needs per-topic maintenance.

Never fabricates documentary context: an accepted photo illustrates the
GENERAL concept a page conveys (e.g. "financial hardship" for a hook
page), not a claim that it depicts the actual verified event/person/place.
"""

import io
import json
import re
import urllib.parse
import urllib.request

_API_URL = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "SWIPE_INFO-social-automation/1.0 (https://github.com/Seyra1004/ai-playground; editorial image acquisition)"

MIN_DIMENSION = 500
MAX_CANDIDATES = 8

_DECORATIVE_NAME_RE = re.compile(
    r"(logo|icon|diagram|screenshot|map|chart|flag|seal|emblem|coat_of_arms|symbol|banner)", re.I
)
_REUSABLE_LICENSE_RE = re.compile(r"(cc[\s-]?by|cc0|public domain|pdm)", re.I)
_NON_REUSABLE_RE = re.compile(r"(non-?free|fair use|all rights reserved|copyrighted)", re.I)

# Generic, topic-agnostic search concepts -- keyed by the FIXED role/
# category vocabulary every topic already uses (core/page_selector.py's
# roles, live_discovery.py's categories), never by a specific headline.
_ROLE_CONCEPT = {
    "hook": "hardship problem worried",
    "why_now": "official notice announcement paperwork",
    "eligibility": "people meeting consultation",
    "amount": "documents paperwork finance",
    "conditions": "documents paperwork finance",
    "procedure": "office consultation process",
    "comparison": "two people contrast",
    "examples": "two people contrast",
    "exclusions": "warning caution sign",
    "warnings": "warning caution sign",
    "cta": "customer service phone call help",
}

_CATEGORY_CONCEPT = {
    "finance_savings": "financial consultation money bank",
    "welfare_benefits": "social welfare support community",
    "employment_labor": "workplace employment office",
    "daily_life_policy": "community assistance government office",
    "health_insurance": "hospital medical consultation",
}


def build_search_query(role: str, category: str) -> str:
    role_part = _ROLE_CONCEPT.get(role, "office consultation")
    cat_part = _CATEGORY_CONCEPT.get(category, "")
    return f"{role_part} {cat_part}".strip()


def _fetch_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fetch_bytes(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search_commons(query: str, max_results: int = MAX_CANDIDATES) -> list:
    """Returns raw Commons search result dicts (title, url, width, height,
    mime, license, artist, descriptionurl) ranked by Commons' own search
    relevance. Never raises -- returns [] on any network/parse failure so a
    source hiccup safely falls through to the next page/priority."""
    url = _API_URL + "?" + urllib.parse.urlencode(
        {
            "action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6, "gsrlimit": max_results, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime", "format": "json",
        }
    )
    try:
        data = _fetch_json(url)
    except Exception:
        return []

    results = []
    for page in (data.get("query", {}) or {}).get("pages", {}).values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        ii = infos[0]
        meta = ii.get("extmetadata", {}) or {}
        results.append(
            {
                "title": page.get("title", ""),
                "url": ii.get("url", ""),
                "descriptionurl": ii.get("descriptionurl", ""),
                "width": ii.get("width", 0),
                "height": ii.get("height", 0),
                "mime": ii.get("mime", ""),
                "license": (meta.get("LicenseShortName", {}) or {}).get("value", ""),
                "usage_terms": (meta.get("UsageTerms", {}) or {}).get("value", ""),
                "artist": re.sub(r"<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value", "")).strip(),
            }
        )
    return results


def _is_reusable(candidate: dict) -> bool:
    license_text = f"{candidate.get('license', '')} {candidate.get('usage_terms', '')}"
    if _NON_REUSABLE_RE.search(license_text):
        return False
    return bool(_REUSABLE_LICENSE_RE.search(license_text))


def _select_candidate(candidates: list, seen_hashes: set) -> dict:
    """Deterministic accept/reject pass: real bitmap photo, sufficient
    resolution, reusable license, not a decorative/non-photo filename, not
    a byte-identical duplicate of one already used this run."""
    for c in candidates:
        if not c.get("url") or not c.get("mime", "").startswith("image/"):
            continue
        if c.get("mime") in ("image/svg+xml", "image/gif"):
            continue
        if min(c.get("width", 0), c.get("height", 0)) < MIN_DIMENSION:
            continue
        if _DECORATIVE_NAME_RE.search(c.get("title", "")):
            continue
        if not _is_reusable(c):
            continue
        try:
            data = _fetch_bytes(c["url"])
        except Exception:
            continue
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            continue
        c["_bytes"] = data
        c["_hash"] = digest
        return c
    return None


def acquire_photo_for_page(role: str, category: str, page_number: int, out_dir: str, seen_hashes: set) -> dict:
    """Searches Commons for a rights-clear, semantically-relevant photo for
    one page (query derived from role+category, never today's headline),
    downloads and saves the first accepted candidate. Returns an asset dict
    (matching pipeline.image_acquisition's shape: file/path/source_url/
    publisher/description/rights/acquisition_method/width/height/bytes) or
    None if nothing acceptable was found -- callers must treat that as a
    legitimate outcome, never fabricate a substitute here."""
    import os

    query = build_search_query(role, category)
    candidates = search_commons(query)
    chosen = _select_candidate(candidates, seen_hashes)
    if chosen is None:
        return None

    os.makedirs(out_dir, exist_ok=True)
    ext = "jpg" if "jpeg" in chosen["mime"] else chosen["mime"].split("/")[-1]
    fname = f"commons_{page_number}.{ext}"
    path = os.path.join(out_dir, fname)
    with open(path, "wb") as f:
        f.write(chosen["_bytes"])
    seen_hashes.add(chosen["_hash"])

    attribution = chosen.get("artist") or "Wikimedia Commons"
    return {
        "file": fname,
        "path": path,
        "source_url": chosen.get("descriptionurl") or chosen.get("url"),
        "publisher": "Wikimedia Commons",
        "description": f"Editorial photo for '{role}' page, search query: {query!r}. {chosen['title']}",
        "rights": f"{chosen.get('license') or chosen.get('usage_terms') or 'reusable license'} -- {attribution}",
        "acquisition_method": "wikimedia_commons_search",
        "width": chosen.get("width", 0),
        "height": chosen.get("height", 0),
        "bytes": len(chosen["_bytes"]),
    }
