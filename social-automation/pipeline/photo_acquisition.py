from __future__ import annotations

"""Reusable, ZERO-PAYG real-photo acquisition for SWIPE_INFO -- Priority
1-2 of the image chain (verified official-source images stay in pipeline.
image_acquisition; this module is the next fallback: rights-clear web
photography), used for ANY future topic, never hardcoded to one date/story.

Sources, searched in this order per concept (never once per source
overall -- once per derived concept, up to 3 concepts per page):
  1. Pexels (api.pexels.com) -- modern editorial/lifestyle stock
     photography; the strongest source for realistic people-in-situations
     imagery Commons/Openverse alone under-cover. Requires PEXELS_API_KEY
     in the environment; silently contributes zero candidates (never
     raises) when unconfigured, so the pipeline degrades cleanly to the
     sources below.
  2. Openverse (api.openverse.org) -- aggregates CC-licensed photography
     from Wikimedia Commons, Flickr Commons, museums, etc.
  3. Wikimedia Commons search -- fallback when the above yield nothing
     acceptable for a concept.

Search concepts are derived deterministically from each page's own
verified headline/body text via a small, reusable Korean-keyword ->
English-(subject, query) glossary (same style of deterministic marker
dictionary already used in pipeline/live_discovery.py and pipeline/
daily_state.py) -- topic-adaptive, never the full headline verbatim,
never hardcoded to one date/story. There is deliberately NO generic
role-based fallback: a page whose text matches no glossary marker
returns zero concepts, and the caller must treat that as an immediate
NO_PHOTO -- a broad-category "relatable person" or "documents on a desk"
substitute is not editorially specific to any one page's actual
distinctive subject.

Never fabricates documentary context: an accepted photo illustrates the
GENERAL concept a page conveys, not a claim that it depicts the actual
verified event/person/place. If nothing acceptable is found after
checking a real candidate pool (across all sources, across all derived
concepts), the caller gets None -- a weak/irrelevant photo is never
silently substituted.
"""

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

from pipeline.semantic_claude_cli import DEFAULT_MODEL, _resolve_executable

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
    (("야근", "초과근무", "연장근무", "시간외", "야간근무"), "office worker", "person working late office desk"),
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
#
# Two more were removed after a DISTINCTIVE-SUBJECT review (not a
# safety/relevance-gate failure -- these passed every existing check):
# "근로자/직장인/사업장" -> "office worker" and "병원/치료/시술" -> "doctor
# patient consultation room" were both broad-CATEGORY matches, not
# editorially-specific to any one page's actual subject -- a generic
# "person at a laptop" or "stethoscope checkup" photo doesn't distinctly
# represent "eligibility for a specific leave type" or "fertility
# treatment" any more than it represents dozens of unrelated topics. A
# genuinely specific alternative ("fertility clinic") was tested and
# rejected too: Commons only has photos of real, NAMED clinics in random
# countries (Chennai, Copenhagen, Tallinn, a UK "geograph.org.uk" street-
# view site) -- using one would wrongly imply a specific real overseas
# institution is associated with a Korean government policy. When a topic
# genuinely has no distinctive, safe, on-subject photo available, NO_PHOTO
# is correct, not a generic broad-category substitute.
#
# There is deliberately NO generic role-based fallback tier (removed after
# this review) -- a "relatable human moment" or "documents on a desk"
# stock photo is exactly the kind of broad-category substitute this
# section exists to reject.
_GEOGRAPH_SOURCE_RE = re.compile(r"geograph\.org\.uk", re.I)


# Words that, ALONE or in combination with only each other, collapse a
# concept into a broad category rather than this page's actual
# distinctive subject -- "medical clinic" passes (clinic isn't generic),
# "doctor patient" fails (both words are). Applied to EVERY concept from
# EITHER tier below, so the semantic fallback can never smuggle back the
# broad-category substitutes already proven unsafe (generic doctor/
# patient, generic office worker, generic person/documents).
_GENERIC_CONCEPT_WORDS = {
    "healthcare", "medical", "office", "worker", "workplace", "finance", "financial",
    "phone", "smartphone", "house", "home", "document", "documents", "paperwork",
    "person", "people", "consultation", "doctor", "patient", "employee", "desk",
}

# Function words that carry no topical meaning of their own -- excluded
# before the GENERIC-vs-IRRELEVANT subset check in classify_relevance so a
# stray preposition/article never keeps an otherwise-generic title out of
# the GENERIC bucket.
_STOPWORDS = {"with", "from", "this", "that", "your", "their", "have", "been", "over", "into", "onto"}


def _is_generic_concept(subject: str) -> bool:
    """True when EVERY word of the subject is itself a broad-category
    term -- i.e. the concept carries no distinctive meaning of its own
    beyond the category. A subject with at least one non-generic word
    (e.g. "fertility" in "fertility clinic") passes."""
    words = {w.strip(".,").lower() for w in (subject or "").split()}
    if not words:
        return True
    return words.issubset(_GENERIC_CONCEPT_WORDS)


_CONCEPT_CLI_SCHEMA = {
    "type": "object",
    "required": ["concepts"],
    "properties": {
        "concepts": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["subject", "query"],
                "properties": {"subject": {"type": "string"}, "query": {"type": "string"}},
            },
        }
    },
}

_CONCEPT_CLI_SYSTEM_PROMPT = (
    "You derive PHOTOGRAPHABLE visual search concepts for one page of a Korean practical-"
    "information Instagram carousel, from that page's own already-verified headline/body text "
    "ONLY -- never invent facts, never use general world knowledge about the topic beyond what's "
    "in the text. Return 1-3 concepts. Each concept must be a real-world subject/scene/object a "
    "camera could actually photograph, DISTINCTIVE to this page's actual subject matter -- reject "
    "your own idea if it would only represent a broad category (healthcare, medical, office, "
    "worker, finance, phone, house, documents, person, consultation, doctor, patient) and nothing "
    "more specific. 'subject' = a short (2-4 word) noun phrase that is the mandatory relevance "
    "anchor (a candidate photo's title must contain at least one of these words); 'query' = a "
    "natural, concrete English search phrase for a stock-photo API (e.g. 'fertility clinic "
    "consultation room', not corporate jargon like 'office worker workplace'). If nothing "
    "distinctive can honestly be derived from this page's own text, return an empty concepts "
    "array -- never guess or force a generic category."
)


def _run_concept_cli(headline: str, body: str, role: str, timeout_seconds: int = 60) -> list:
    try:
        executable = _resolve_executable()
    except Exception:
        return []
    user_prompt = json.dumps({"headline": headline, "body": body, "role": role}, ensure_ascii=False)
    cmd = [
        executable, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(_CONCEPT_CLI_SCHEMA),
        "--tools", "",
        "--no-session-persistence",
        "--model", DEFAULT_MODEL,
        "--system-prompt", _CONCEPT_CLI_SYSTEM_PROMPT,
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        result = subprocess.run(
            cmd, shell=False, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=timeout_seconds, input=user_prompt,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except Exception:
        return []
    if payload.get("is_error"):
        return []
    parsed = payload.get("structured_output")
    if parsed is None:
        raw = payload.get("result")
        if not isinstance(raw, str):
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
    return parsed.get("concepts") or []


def derive_concepts_semantic(headline: str, body: str, role: str, max_concepts: int = 3) -> list:
    """Tier 2 concept generation -- called ONLY when the deterministic
    glossary (Tier 1) finds nothing, or finds only overly-broad concepts.
    Calls the SAME ZERO-PAYG, already-authenticated subscription `claude`
    CLI pattern already used for topic transformation and semantic
    authoring elsewhere in this pipeline (never api.anthropic.com; never
    a new paid service). Every returned concept still passes through the
    identical _is_generic_concept validator Tier 1 uses -- the fallback
    can never smuggle back a broad-category substitute."""
    raw = _run_concept_cli(headline, body, role)
    concepts = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        subject, query = str(c.get("subject", "")).strip(), str(c.get("query", "")).strip()
        if not subject or not query or _is_generic_concept(subject):
            continue
        pair = (subject, query)
        if pair not in concepts:
            concepts.append(pair)
        if len(concepts) >= max_concepts:
            break
    return concepts


def derive_concepts(headline: str, body: str, role: str = "", max_concepts: int = 3) -> list:
    """Up to 3 (subject, query) concept pairs for one page.

    Tier 1 (fast, free, deterministic): matched from the page's own text
    against the glossary above, most-matched first, filtered through the
    same anti-generic check Tier 2 uses.

    Tier 2 (only when Tier 1 finds nothing): a small, structured call to
    the local `claude` CLI subscription (see derive_concepts_semantic) to
    handle topics with no glossary entry -- SWIPE_INFO covers open-ended
    future domains a fixed keyword dictionary can never fully anticipate.

    Either tier can legitimately return [] -- callers MUST treat that as
    an immediate NO_PHOTO. No generic/role-based substitute: a
    broad-category "relatable person" or "documents on a desk" photo is
    not editorially specific to any one page's actual distinctive
    subject, and NO_PHOTO is always preferable to one that could
    represent almost any topic."""
    text = f"{headline} {body}"
    concepts = []
    for markers, subject, query in _KEYWORD_CONCEPTS:
        if any(m in text for m in markers) and not _is_generic_concept(subject):
            pair = (subject, query)
            if pair not in concepts:
                concepts.append(pair)
        if len(concepts) >= max_concepts:
            break
    if concepts:
        return _enforce_ladder_coherence(concepts[:max_concepts])
    return _enforce_ladder_coherence(derive_concepts_semantic(headline, body, role, max_concepts))


def _enforce_ladder_coherence(concepts: list) -> list:
    """Keep the first (most distinctive) concept as the ladder's anchor;
    drop any later concept that shares no non-generic word with it.

    Evidence from a live Golden Test trace: Tier 2 returned a 3-concept
    ladder for one page where concepts #1/#2 stayed anchored to the same
    real subject but #3 drifted to an unrelated idea ("calendar with days
    marked for leave") that individually passed _is_generic_concept (no
    single word of it is generic) but shared nothing with the page's
    actual distinctive subject -- wasting a search on a query that could
    not possibly return a relevant photo. A query ladder must stay
    orbiting the SAME real-world subject at increasing breadth, never
    drift into an unrelated idea just because no single word triggered
    the generic-word filter."""
    if len(concepts) <= 1:
        return concepts
    anchor_words = _concept_words(concepts[0][0]) - _GENERIC_CONCEPT_WORDS
    if not anchor_words:
        return concepts
    kept = [concepts[0]]
    for subject, query in concepts[1:]:
        candidate_words = _concept_words(subject) | _concept_words(query)
        if anchor_words & candidate_words:
            kept.append((subject, query))
    return kept


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


_PEXELS_URL = "https://api.pexels.com/v1/search"
PEXELS_API_KEY_ENV = "PEXELS_API_KEY"


def search_pexels(query: str, limit: int = POOL_PER_SOURCE) -> list:
    """Modern editorial/lifestyle stock photography -- the source most
    likely to cover realistic people-in-situations imagery that Commons/
    Openverse alone under-cover. Reads the key from PEXELS_API_KEY in the
    environment; NEVER hardcoded. Returns [] (never raises) when
    unconfigured or on any request failure, so the caller transparently
    falls through to Commons/Openverse -- the same safe-fallback contract
    every other source here already follows."""
    api_key = os.environ.get(PEXELS_API_KEY_ENV, "")
    if not api_key:
        return []
    url = _PEXELS_URL + "?" + urllib.parse.urlencode({"query": query, "per_page": limit, "orientation": "portrait"})
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    results = []
    for p in data.get("photos", []) or []:
        src = p.get("src", {}) or {}
        results.append(
            {
                "source": "pexels",
                "title": p.get("alt") or f"Photo by {p.get('photographer', '')}",
                "url": src.get("large2x") or src.get("original") or src.get("large") or "",
                "descriptionurl": p.get("url", ""),
                "width": p.get("width", 0) or 0, "height": p.get("height", 0) or 0,
                "mime": "image/jpeg",
                "license": "pexels license", "usage_terms": "Pexels License -- free to use, no attribution required",
                "artist": p.get("photographer", "") or "Pexels",
            }
        )
    return results


_PIXABAY_URL = "https://pixabay.com/api/"
PIXABAY_API_KEY_ENV = "PIXABAY_API_KEY"


def search_pixabay(query: str, limit: int = POOL_PER_SOURCE) -> list:
    """Second modern-editorial-photography source, same free-credential
    pattern as search_pexels: reads PIXABAY_API_KEY from the environment,
    NEVER hardcoded, returns [] (never raises) when unconfigured or on any
    request failure. Pixabay's own content license is free for commercial
    and noncommercial use with no attribution required -- the same
    "inherently reusable" tier as Pexels/Openverse (see _is_reusable)."""
    api_key = os.environ.get(PIXABAY_API_KEY_ENV, "")
    if not api_key:
        return []
    url = _PIXABAY_URL + "?" + urllib.parse.urlencode(
        {"key": api_key, "q": query, "image_type": "photo", "per_page": limit, "safesearch": "true"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    results = []
    for h in data.get("hits", []) or []:
        results.append(
            {
                "source": "pixabay",
                "title": h.get("tags", "") or f"Photo by {h.get('user', '')}",
                "url": h.get("largeImageURL") or h.get("webformatURL") or "",
                "descriptionurl": h.get("pageURL", ""),
                "width": h.get("imageWidth", 0) or 0, "height": h.get("imageHeight", 0) or 0,
                "mime": "image/jpeg",
                "license": "pixabay license", "usage_terms": "Pixabay Content License -- free to use, no attribution required",
                "artist": h.get("user", "") or "Pixabay",
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
    if candidate.get("source") in ("openverse", "pexels", "pixabay"):
        return True
    return bool(_REUSABLE_LICENSE_RE.search(license_text))


def _concept_words(concept: str) -> set:
    return {w for w in concept.lower().split() if len(w) >= 4}


def _word_in_title(title: str, words: set) -> bool:
    # Word-boundary match, not a bare substring check -- "bank" must appear
    # as its own word, not merely inside an unrelated compound like a Dutch
    # "spaarbank"/"savingsbank" archival building name.
    return any(re.search(rf"\b{re.escape(w)}\b", title) for w in words)


def _context_words(query: str, subject_words: set) -> set:
    """The query's own vocabulary MINUS the subject's words -- describes
    the same real-world environment/action/object the subject names, so a
    candidate matching one of these still depicts a genuinely relevant
    scene, not just an exact restatement of the subject noun phrase.
    Generic-only words (see _GENERIC_CONCEPT_WORDS) never count -- a
    context match must still be domain-specific, purely mechanical
    set-difference over already-derived text, so this generalizes to any
    topic's own query without new per-domain data."""
    words = _concept_words(query) - subject_words
    return {w for w in words if w not in _GENERIC_CONCEPT_WORDS}


def classify_relevance(candidate: dict, subject: str, query: str = "") -> str:
    """EXACT_SUBJECT / CONTEXTUALLY_RELEVANT / GENERIC / IRRELEVANT.

    A useful editorial photo does not always have to literally restate the
    subject noun phrase in its title -- a candidate whose title engages
    with the query's own non-generic context vocabulary (the environment/
    action/object associated with the same real-world subject) is still
    a legitimate match. A candidate whose only meaningful words are broad-
    category terms (see _GENERIC_CONCEPT_WORDS) is GENERIC and must be
    rejected; a candidate with no meaningful overlap at all is IRRELEVANT.
    Both EXACT_SUBJECT and CONTEXTUALLY_RELEVANT pass; GENERIC and
    IRRELEVANT never do (see _is_relevant)."""
    title = candidate.get("title", "").lower()
    subject_words = _concept_words(subject)
    if subject_words and _word_in_title(title, subject_words):
        return "EXACT_SUBJECT"
    ctx_words = _context_words(query, subject_words)
    if ctx_words and _word_in_title(title, ctx_words):
        return "CONTEXTUALLY_RELEVANT"
    # GENERIC vs IRRELEVANT is a reporting distinction only -- both reject
    # (see _is_relevant) -- so function words ("with", "from", "this")
    # are stripped before the subset check: a title that's substantively
    # ONLY broad-category words is GENERIC even if it also contains a
    # stray preposition or article.
    title_words = {w.strip(".,()") for w in title.split() if len(w) >= 4} - _STOPWORDS
    if title_words and title_words.issubset(_GENERIC_CONCEPT_WORDS):
        return "GENERIC"
    return "IRRELEVANT"


def _is_relevant(candidate: dict, subject: str, query: str = "") -> bool:
    """The semantic relevance gate: mandatory before has_photo can ever be
    set True. Passes only EXACT_SUBJECT and CONTEXTUALLY_RELEVANT
    candidates (see classify_relevance) -- GENERIC and IRRELEVANT never
    pass regardless of license/resolution."""
    if not _concept_words(subject):
        return False
    return classify_relevance(candidate, subject, query) in ("EXACT_SUBJECT", "CONTEXTUALLY_RELEVANT")


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
    # geograph.org.uk is a UK/Ireland amateur street-view documentation
    # project -- confirmed (via a "fertility clinic" query) to return only
    # exterior/building shots of specific, real, named institutions,
    # never generic editorial photography. Any of its results implies a
    # specific real building/location, wrong for general contextual use.
    if _GEOGRAPH_SOURCE_RE.search(title) or _GEOGRAPH_SOURCE_RE.search(candidate.get("descriptionurl", "")):
        return False
    if _NAMED_PERSON_RE.match(stem):
        return False
    if not _is_reusable(candidate):
        return False
    return True


def _select_candidate(candidates: list, subject: str, seen_hashes: set, query: str = "") -> dict:
    for c in candidates:
        if not _passes_generic_gates(c):
            continue
        if not _is_relevant(c, subject, query):
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


def acquire_photo_for_page(
    role: str, headline: str, body: str, page_number: int, out_dir: str, seen_hashes: set,
    concepts: list = None, debug_log: list = None,
) -> dict:
    """Tries each derived (subject, query) concept in order -- max 3, a
    small query ladder, never a broad search spree -- until one candidate
    passes every quality/semantic-relevance/license gate. Per concept,
    searches Pexels and Pixabay (modern editorial/lifestyle photography,
    each only when its own *_API_KEY is configured) plus Openverse and
    Commons, merging all four pools before gating/selection so a stronger
    modern result is never skipped just because an older archive answered
    first.
    If the page's own text yields NO concept at all, this makes ZERO
    network calls and returns None immediately: a page whose subject
    can't be confidently derived from its own verified text must never
    search the web with borrowed vocabulary. Returns an asset dict (file/
    path/source_url/publisher/description/rights/acquisition_method/
    width/height/bytes) or None if nothing acceptable was found across
    every source -- callers must treat that as NO_ACCEPTABLE_PHOTO, never
    substitute a weak result to hit coverage."""
    # concepts: pass-through so an upstream planner's already-computed
    # (subject, query) ladder is used verbatim instead of re-deriving it
    # here (avoids a second concept-generation call and keeps acquisition
    # and the planner's logged decision the same thing, not two guesses).
    if concepts is None:
        concepts = derive_concepts(headline, body, role)
    if not concepts:
        if debug_log is not None:
            debug_log.append({"queries_tried": [], "candidates_found": 0, "reason": "no_distinctive_subject"})
        return None

    chosen, chosen_subject, chosen_query = None, None, None
    for subject, query in concepts:
        pool = search_pexels(query) + search_pixabay(query) + search_openverse(query) + search_commons(query)
        selected = _select_candidate(pool, subject, seen_hashes, query)
        if debug_log is not None:
            breakdown = {"EXACT_SUBJECT": 0, "CONTEXTUALLY_RELEVANT": 0, "GENERIC": 0, "IRRELEVANT": 0, "gate_rejected": 0}
            for c in pool:
                if not _passes_generic_gates(c):
                    breakdown["gate_rejected"] += 1
                else:
                    breakdown[classify_relevance(c, subject, query)] += 1
            debug_log.append({
                "subject": subject, "query": query, "candidates_found": len(pool),
                "relevance_breakdown": breakdown, "accepted": selected is not None,
            })
        if selected is not None:
            chosen, chosen_subject, chosen_query = selected, subject, query
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
        "publisher": {"openverse": "Openverse", "pexels": "Pexels"}.get(chosen.get("source"), "Wikimedia Commons"),
        "description": f"Editorial photo for '{role}' page, subject: {chosen_subject!r}, query: {chosen_query!r}. {chosen['title']}",
        "rights": f"{chosen.get('license') or chosen.get('usage_terms') or 'reusable license'} -- {attribution}",
        "acquisition_method": f"{chosen.get('source')}_search",
        "width": chosen.get("width", 0),
        "height": chosen.get("height", 0),
        "bytes": len(chosen["_bytes"]),
    }
