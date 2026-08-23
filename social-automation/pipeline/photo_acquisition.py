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
verified headline/body text via a small, reusable Korean-keyword ->
English-(subject, query) glossary (same style of deterministic marker
dictionary already used in pipeline/live_discovery.py and pipeline/
daily_state.py) -- topic-adaptive, never the full headline verbatim,
never hardcoded to one date/story.

When the page's own text matches nothing in that content glossary, a
SECOND, deliberately generic and role-keyed (not topic-keyed) tier
supplies one broad contextual scene -- a relatable human moment or a
documents/desk moment, the same two categories for every topic every
day -- so a real photo opportunity still gets a genuine attempt instead
of the page being searched zero times. This is NOT the old bug (a
previous topic's specific leftover vocabulary, e.g. "family small
business owner", leaking onto an unrelated page): the fallback text is
always the same two universal categories, never anything derived from a
different topic's content. Every candidate, from either tier, still has
to clear the exact same quality/relevance/license gates below -- NO_PHOTO
remains the outcome whenever nothing genuinely acceptable turns up.

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
    r"coat_of_arms|symbol|banner|portrait|headshot|mugshot|"
    # A contextual/lifestyle photo must never imply the photographed
    # person is an actual public official or specific real event's
    # subject -- reject named-office/political-figure imagery outright,
    # regardless of how well it otherwise matches a generic query.
    r"white house|oval office|\bpresident\b|\bminister\b|\bsenator\b|\bgovernor\b|\bprime minister\b|"
    # Wellcome Collection's historical print/engraving archive (accession
    # IDs like "Wellcome V0011051") is predominantly 18th-19th century
    # satirical/medical illustrations and engravings, not photography --
    # confirmed by three false positives on a "doctor patient" query
    # ("A gouty patient in his room full of unproductive doctors",
    # "Doctor Spurzheim his consulting room"). Reject the whole source
    # rather than trying to keyword-filter individual old illustrations.
    r"wellcome\s*v\d{5,}|\bengraving\b|\bcaricature\b|\blithograph\b|\betching\b)",
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

# Deterministic Korean-keyword -> (SUBJECT, QUERY) glossary. Each entry is
# a durable, reusable real-world concept (a person/object/action a camera
# could actually photograph) -- never a one-off compound scene tied to a
# single story. SUBJECT is the mandatory relevance anchor (the noun a
# candidate's title must actually contain, see _is_relevant); QUERY is the
# full search phrase sent to Openverse/Commons. Small and bounded on
# purpose -- covers the recurring SWIPE_INFO domains (work, money/benefits,
# consumer/scam protection, tech, transport, tax, housing, insurance,
# disaster) without becoming a topic-specific dictionary. Order matters:
# earlier entries win when a page's text matches several.
_KEYWORD_CONCEPTS = [
    (("수해", "호우", "침수", "홍수", "태풍"), "flooded street", "flooded street damaged building"),
    (("소상공인", "중소기업", "자영업", "상공인"), "small shop owner", "small shop owner business"),
    (("가계", "가정", "세대", "가족"), "family household", "family household home"),
    # Natural stock-photo-style scene phrasing, not professional-jargon
    # phrases -- "office worker workplace" tested empirically to return
    # near-zero real coverage on Commons/Openverse; "person using laptop
    # office" style phrasing returns real, high-res, appropriately
    # generic results for the same underlying concept.
    (("근로자", "직장인", "노동자", "근무자", "사업장"), "office worker", "person using laptop office"),
    (("야근", "초과근무", "연장근무", "시간외", "야간근무"), "office worker", "person working late office desk"),
    (("병원", "진료", "치료", "시술", "검사", "의사", "환자"), "doctor patient", "doctor patient consultation room"),
    (("환급", "환불", "돌려받"), "receipt payment", "receipt payment consumer"),
    (("카드", "결제"), "credit card payment", "credit card payment"),
    (("보험료", "보험금", "보험"), "insurance documents", "insurance documents paperwork"),
    (("채무", "연체", "조정"), "financial documents", "financial documents desk"),
    (("세금", "국세", "지방세", "납세"), "tax documents", "tax documents paperwork"),
    (("전세", "월세", "임대차", "부동산"), "housing documents", "housing rental documents keys"),
    (("스마트폰", "휴대폰", "앱"), "smartphone user", "person using smartphone app"),
    (("보이스피싱", "스미싱", "사기"), "suspicious phone message", "person looking at suspicious phone message"),
    (("신청", "접수", "서류"), "paperwork application", "paperwork application form"),
    (("문의", "상담", "콜센터"), "phone call customer support", "phone call customer support"),
    (("위기", "경보", "주의", "제외"), "warning caution sign", "warning caution sign"),
]
# NOTE: three entries were deliberately removed after live testing, not
# just left out: "disaster relief emergency" (긴급/재난/특별재난/피해),
# "recovery assistance volunteers" (복구/지원), and "bank consultation"
# (대출/상환, 은행/금융기관/금융). Each passed the word-match relevance
# gate but Commons/Openverse's actual archive for those terms skews toward
# foreign military/humanitarian-operation photography or bare bank/
# institution BUILDING architecture shots -- both explicitly banned
# ("unrelated countries/contexts", "generic buildings"). A genuine flood
# scene is still covered by the more specific "수해/호우/침수/홍수/태풍"
# entry above; pages that would have matched only these removed markers
# now correctly fall through to NO_PHOTO instead of a wrong-context photo.
# A fourth, "commuter public transportation" (대중교통/버스/지하철/통근), was
# removed the same way: its top real results were a US transit-authority
# mask-mandate notice photo and a 1970s NARA archival Philadelphia parking-
# lot photo -- country-specific institutional/archival imagery, not a
# generic commuting scene.


# Tier 2: a GENERIC, role-keyed, UNIVERSAL contextual fallback -- used
# ONLY when the page's own text matched nothing in the content glossary
# above. This is deliberately NOT a topic's specific vocabulary leaking
# into another topic (the earlier, separately-fixed bug) -- it is the
# SAME two broad scene categories for every topic, every day: a relatable
# human moment for reader-facing roles, a documents/process moment for
# structural roles. It only ever widens the SEARCH attempt; every
# candidate it finds still has to clear the exact same quality/relevance/
# license gates as a content-derived concept, so a page still correctly
# gets NO_PHOTO if nothing genuinely acceptable turns up.
_ROLE_FALLBACK_QUERIES = {
    "hook": ("person", "person reading document at desk"),
    "why_now": ("person", "person using smartphone concerned"),
    "eligibility": ("person", "person reading document at desk"),
    "warnings": ("person", "person using smartphone concerned"),
    "cta": ("person", "person using smartphone app"),
    "conditions": ("documents", "hands filling out paperwork desk"),
    "procedure": ("documents", "hands filling out paperwork desk"),
    "amount": ("documents", "person writing document office"),
    "comparison": ("documents", "hands filling out paperwork desk"),
    "examples": ("documents", "hands filling out paperwork desk"),
    "exclusions": ("documents", "person writing document office"),
}


def derive_concepts(headline: str, body: str, role: str = "", max_concepts: int = 3) -> list:
    """Up to 3 (subject, query) concept pairs for one page. Tier 1 is
    derived ONLY from the page's own verified text via the content
    glossary above (most-matched concepts first). If that finds nothing,
    Tier 2 adds ONE generic, role-keyed contextual scene (see
    _ROLE_FALLBACK_QUERIES) so a page is never searched zero times just
    because its text happens to contain no glossary marker -- a real
    photo opportunity (a relatable moment, a documents/desk scene) still
    gets a genuine attempt, gated by the same relevance/quality checks as
    everything else."""
    text = f"{headline} {body}"
    concepts = []
    for markers, subject, query in _KEYWORD_CONCEPTS:
        if any(m in text for m in markers):
            pair = (subject, query)
            if pair not in concepts:
                concepts.append(pair)
        if len(concepts) >= max_concepts:
            break
    if not concepts:
        fallback = _ROLE_FALLBACK_QUERIES.get(role)
        if fallback:
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


def _is_relevant(candidate: dict, subject: str) -> bool:
    """The semantic relevance gate: a candidate's title must actually
    contain one of the page's SUBJECT's own meaningful words -- not just
    any word from the (longer, looser) full search query. This is what
    makes an off-topic-but-plausible-sounding result (e.g. a Ghanaian
    "Agricultural business owner" photo surfacing for a "small shop
    owner"-style query) fail automatically: "agricultural"/"ghana" share
    no word with the subject "small shop owner", so it's rejected even
    though the broader query happened to return it. License/resolution
    alone were never enough; this check is mandatory before has_photo can
    ever be set True."""
    title = candidate.get("title", "").lower()
    words = _concept_words(subject)
    if not words:
        return False
    # Word-boundary match, not a bare substring check -- "bank" must appear
    # as its own word, not merely inside an unrelated compound like a Dutch
    # "spaarbank"/"savingsbank" archival building name.
    return any(re.search(rf"\b{re.escape(w)}\b", title) for w in words)


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
    # Openverse's own category=photograph filter isn't fully reliable for
    # aggregated third-party sources (confirmed: a "Doctor Spurzheim his
    # consulting room" engraving passed it, but the source's own landing
    # page URL self-describes it as "image-cartoon-person-art"). The
    # description/landing URL is a second, independent signal worth
    # checking even when the title itself looks innocuous.
    if re.search(r"cartoon|illustration|clipart|vector-art|-art\b", candidate.get("descriptionurl", ""), re.I):
        return False
    if _NAMED_PERSON_RE.match(stem):
        return False
    if not _is_reusable(candidate):
        return False
    return True


def _select_candidate(candidates: list, subject: str, seen_hashes: set) -> dict:
    for c in candidates:
        if not _passes_generic_gates(c):
            continue
        if not _is_relevant(c, subject):
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
    """Tries each derived (subject, query) concept (Openverse pool first,
    then Commons) in order -- max 3, a small query ladder, never a broad
    search spree -- until one candidate passes every quality/semantic-
    relevance/license gate. If the page's own text yields NO concept at
    all, this makes ZERO network calls and returns None immediately: a
    page whose subject can't be confidently derived from its own verified
    text must never search the web with borrowed vocabulary. Returns an
    asset dict (file/path/source_url/publisher/description/rights/
    acquisition_method/width/height/bytes) or None if nothing acceptable
    was found -- callers must treat that as NO_ACCEPTABLE_PHOTO, never
    substitute a weak result to hit coverage."""
    concepts = derive_concepts(headline, body, role)
    if not concepts:
        return None

    chosen, chosen_subject, chosen_query = None, None, None
    for subject, query in concepts:
        pool = search_openverse(query) + search_commons(query)
        chosen = _select_candidate(pool, subject, seen_hashes)
        if chosen is not None:
            chosen_subject, chosen_query = subject, query
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
        "description": f"Editorial photo for '{role}' page, subject: {chosen_subject!r}, query: {chosen_query!r}. {chosen['title']}",
        "rights": f"{chosen.get('license') or chosen.get('usage_terms') or 'reusable license'} -- {attribution}",
        "acquisition_method": f"{chosen.get('source')}_search",
        "width": chosen.get("width", 0),
        "height": chosen.get("height", 0),
        "bytes": len(chosen["_bytes"]),
    }
