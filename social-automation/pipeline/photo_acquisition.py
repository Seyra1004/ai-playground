from __future__ import annotations

"""Reusable, ZERO-PAYG real-photo acquisition for SWIPE_INFO -- Priority
1-2 of the image chain (verified official-source images stay in pipeline.
image_acquisition; this module is the next fallback: rights-clear web
photography), used for ANY future topic, never hardcoded to one date/story.

Sources (both keyless, free, no PAYG):
  1. Openverse (api.openverse.org) -- aggregates CC-licensed photography
     from Wikimedia Commons, Flickr Commons, museums, etc.; built-in
     license filtering and a much larger, more editorial-photo-like pool
     than Commons' own search alone.
  2. Wikimedia Commons search -- fallback when Openverse yields nothing
     acceptable for a concept.

Search concepts are derived deterministically from each page's own
verified headline/body text (a small, reusable Korean-keyword ->
English-concept glossary -- the same style of deterministic marker
dictionary already used in pipeline/live_discovery.py and pipeline/
daily_state.py) with a role-keyed fallback when no glossary term is
present. Never the full headline verbatim, never hardcoded to one date.

Never fabricates documentary context: an accepted photo illustrates the
GENERAL concept a page conveys, not a claim that it depicts the actual
verified event/person/place. If nothing acceptable is found after
checking a real candidate pool across all derived concepts, the caller
gets None -- a weak/irrelevant photo is never silently substituted.
"""

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request

_OPENVERSE_URL = "https://api.openverse.org/v1/images/"
_COMMONS_URL = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "SWIPE_INFO-social-automation/1.0 (https://github.com/Seyra1004/ai-playground; editorial image acquisition)"

MIN_DIMENSION = 500
POOL_PER_SOURCE = 8

_REJECT_NAME_RE = re.compile(
    r"(logo|icon|diagram|screenshot|\bmap\b|route|schematic|floor.?plan|chart|flag|seal|emblem|"
    r"coat_of_arms|symbol|banner|portrait|headshot|mugshot)",
    re.I,
)
_REUSABLE_LICENSE_RE = re.compile(r"(cc[\s-]?by|cc0|public.?domain|pdm)", re.I)
_NON_REUSABLE_RE = re.compile(r"(non-?free|fair use|all rights reserved|copyrighted|nc\b|nd\b)", re.I)
# Commons/Openverse are archives, not curated stock libraries -- reject an
# obviously old scan (jarring next to current-day content) and a filename
# that's JUST a capitalized personal name (a named individual's portrait
# used completely out of context, a real misuse risk, not just a weak
# match).
_OLD_YEAR_RE = re.compile(r"\b(1[0-8]\d{2}|19[0-4]\d)\b")
_BOOK_SCAN_RE = re.compile(r"(plate|page \d+|illustration from|frontispiece)", re.I)
_NAMED_PERSON_RE = re.compile(r"^[A-Z][a-zA-Z'\-]+(\s[A-Z][a-zA-Z'\-]+){1,2}$")

# Deterministic Korean-keyword -> English-photo-concept glossary. Reusable
# across ANY future topic (a small marker dictionary, same pattern as
# core/scoring.py's/live_discovery.py's existing keyword signals) -- never
# rewritten per date/story. Order matters: earlier entries win when a page
# matches several.
_KEYWORD_CONCEPTS = [
    (("수해", "호우", "침수", "홍수", "태풍"), "flood damaged building street"),
    (("소상공인", "중소기업", "자영업", "상공인"), "small shop owner business"),
    (("가계", "가정", "세대", "가족"), "family household home"),
    (("대출", "상환"), "loan consultation bank"),
    (("카드", "결제"), "credit card payment"),
    (("보험료", "보험금", "보험"), "insurance paperwork"),
    (("채무", "연체", "조정"), "financial documents desk"),
    (("신청", "접수", "서류"), "paperwork application form"),
    (("문의", "상담", "콜센터"), "phone call customer support"),
    (("은행", "금융기관", "금융"), "bank consultation"),
    (("긴급", "재난", "특별재난", "피해"), "disaster relief emergency"),
    (("복구", "지원"), "recovery assistance volunteers"),
    (("위기", "경보", "주의", "제외"), "warning caution sign"),
]

_ROLE_FALLBACK_CONCEPT = {
    "hook": "family financial stress", "why_now": "official notice documents",
    "eligibility": "family small business owner", "amount": "financial documents desk",
    "conditions": "financial documents desk", "procedure": "consultation office",
    "comparison": "family small business", "examples": "family small business",
    "exclusions": "warning caution sign", "warnings": "warning caution sign",
    "cta": "phone call customer support",
}


def derive_concepts(headline: str, body: str, role: str, max_concepts: int = 4) -> list:
    """2-4 short, concrete, photographable concept phrases for one page --
    derived from its own verified text via the glossary above, never the
    raw headline. Falls back to a role-keyed generic concept when no
    glossary term is present (still never a fixed per-date literal)."""
    text = f"{headline} {body}"
    concepts = []
    for markers, concept in _KEYWORD_CONCEPTS:
        if any(m in text for m in markers) and concept not in concepts:
            concepts.append(concept)
        if len(concepts) >= max_concepts:
            break
    if len(concepts) < 2:
        fallback = _ROLE_FALLBACK_CONCEPT.get(role, "consultation office")
        if fallback not in concepts:
            concepts.append(fallback)
    return concepts[:max_concepts]


def _fetch_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fetch_bytes(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search_openverse(query: str, limit: int = POOL_PER_SOURCE) -> list:
    url = _OPENVERSE_URL + "?" + urllib.parse.urlencode(
        {"q": query, "license_type": "commercial,modification", "page_size": limit, "mature": "false", "category": "photograph"}
    )
    try:
        data = _fetch_json(url)
    except Exception:
        return []
    results = []
    for r in data.get("results", []) or []:
        results.append(
            {
                "source": "openverse", "title": r.get("title", ""), "url": r.get("url", ""),
                "descriptionurl": r.get("foreign_landing_url", "") or r.get("url", ""),
                "width": r.get("width") or 0, "height": r.get("height") or 0,
                "mime": "image/jpeg", "license": r.get("license", ""), "usage_terms": "",
                "artist": r.get("creator", "") or r.get("provider", "Openverse"),
            }
        )
    return results


def search_commons(query: str, max_results: int = POOL_PER_SOURCE) -> list:
    url = _COMMONS_URL + "?" + urllib.parse.urlencode(
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
                "source": "commons", "title": page.get("title", ""), "url": ii.get("url", ""),
                "descriptionurl": ii.get("descriptionurl", ""),
                "width": ii.get("width", 0), "height": ii.get("height", 0), "mime": ii.get("mime", ""),
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
    return bool(_REUSABLE_LICENSE_RE.search(license_text)) or candidate.get("source") == "openverse"


def _concept_words(concept: str) -> set:
    return {w for w in concept.lower().split() if len(w) >= 4}


def _is_relevant(candidate: dict, concept: str) -> bool:
    """The one check that stops an irrelevant-but-highly-ranked result
    (a route map for "phone call", a flag for "loan consultation") from
    being accepted just because the API returned it: the title must
    actually contain one of the concept's own meaningful words."""
    title = candidate.get("title", "").lower()
    words = _concept_words(concept)
    return any(w in title for w in words) if words else True


def _passes_generic_gates(candidate: dict) -> bool:
    title = candidate.get("title", "")
    stem = re.sub(r"^File:", "", title)
    stem = re.sub(r"\.\w+$", "", stem).strip()
    if not candidate.get("url") or not candidate.get("mime", "").startswith("image/"):
        return False
    if candidate.get("mime") in ("image/svg+xml", "image/gif"):
        return False
    if min(candidate.get("width", 0) or 0, candidate.get("height", 0) or 0) < MIN_DIMENSION:
        return False
    if _REJECT_NAME_RE.search(title) or _OLD_YEAR_RE.search(title) or _BOOK_SCAN_RE.search(title):
        return False
    if _NAMED_PERSON_RE.match(stem):
        return False
    if not _is_reusable(candidate):
        return False
    return True


def _select_candidate(candidates: list, concept: str, seen_hashes: set) -> dict:
    for c in candidates:
        if not _passes_generic_gates(c):
            continue
        if not _is_relevant(c, concept):
            continue
        try:
            data = _fetch_bytes(c["url"])
        except Exception:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hashes:
            continue
        c["_bytes"] = data
        c["_hash"] = digest
        return c
    return None


def acquire_photo_for_page(role: str, headline: str, body: str, page_number: int, out_dir: str, seen_hashes: set) -> dict:
    """Tries each derived concept (Openverse pool first, then Commons) in
    order until one candidate passes every quality/relevance/license gate.
    Returns an asset dict (file/path/source_url/publisher/description/
    rights/acquisition_method/width/height/bytes) or None if NOTHING
    acceptable was found across all concepts -- callers must treat that as
    NO_ACCEPTABLE_PHOTO, never substitute a weak result to hit coverage."""
    concepts = derive_concepts(headline, body, role)
    chosen, chosen_concept = None, None
    for concept in concepts:
        pool = search_openverse(concept) + search_commons(concept)
        chosen = _select_candidate(pool, concept, seen_hashes)
        if chosen is not None:
            chosen_concept = concept
            break
    if chosen is None:
        return None

    os.makedirs(out_dir, exist_ok=True)
    ext = "jpg" if "jpeg" in chosen["mime"] else (chosen["mime"].split("/")[-1] if chosen.get("mime") else "jpg")
    fname = f"photo_{page_number}.{ext}"
    path = os.path.join(out_dir, fname)
    with open(path, "wb") as f:
        f.write(chosen["_bytes"])
    seen_hashes.add(chosen["_hash"])

    attribution = chosen.get("artist") or chosen.get("source", "").title()
    return {
        "file": fname,
        "path": path,
        "source_url": chosen.get("descriptionurl") or chosen.get("url"),
        "publisher": "Openverse" if chosen.get("source") == "openverse" else "Wikimedia Commons",
        "description": f"Editorial photo for '{role}' page, concept: {chosen_concept!r}. {chosen['title']}",
        "rights": f"{chosen.get('license') or chosen.get('usage_terms') or 'reusable license'} -- {attribution}",
        "acquisition_method": f"{chosen.get('source')}_search",
        "width": chosen.get("width", 0),
        "height": chosen.get("height", 0),
        "bytes": len(chosen["_bytes"]),
    }
