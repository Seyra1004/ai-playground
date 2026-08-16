"""Music Trend Intelligence synthesis: ONE combined LLM call per daily
report producing Genre Radar / Production Radar / Producer Reference
Radar / K-pop-A&R relevance -- the missing real capability identified in
the MUSIC INTELLIGENCE COMPLETION phase's own read-only audit.

Grounded strictly in real evidence already computed/ingested elsewhere
this run (the SAME discipline report.producer_synthesis already
establishes): the LLM is given a labeled evidence catalog and may only
cite `ref` labels that exist in it. report.validation.
validate_music_trend_signals enforces this identically to
validate_producer_insights -- every signal must cite at least one real
ref, and every cited ref must exist in the catalog. The model can never
invent a genre trend, a production characteristic, or a producer credit
that isn't grounded in something a real evidence item actually says.

Real, audited data constraint this module was deliberately designed
around (see SUPER_NEWS_HANDOFF.md's "MUSIC INTELLIGENCE COMPLETION"
section for the full audit): no audio-feature/genre-tag data source
exists anywhere in this system (Spotify's real integration fetches only
artist/album/release_date/isrc -- never tempo, key, danceability,
instrumentation, or genre), and derived_signals (VELOCITY) has zero real
rows in production today (each chart source has only ONE real
observation so far, not the 2+ a velocity diff requires). The ONLY real
evidence available for genre/production/producer-reference signals today
is: (a) real chart rank snapshots, and (b) real article title+snippet
TEXT from Music Industry/Spotify news -- which DOES sometimes explicitly
state a genre, a sonic/production descriptor, or a named collaborator
(see build_evidence_catalog's own real examples). This is why every
signal category here is allowed to come back EMPTY on a given day: a
real evidence pool that happens not to mention any genre/production/
credit detail that day must produce nothing, never a filler guess."""

import hashlib
import json

PROMPT_VERSION = "v2"
CATEGORY = "MUSIC_TREND_INTELLIGENCE"

MAX_GENRE_SIGNALS = 3
MAX_PRODUCTION_NOTES = 3
MAX_PRODUCER_REFERENCES = 3
MAX_KPOP_AR_NOTES = 2

_SIGNAL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "observed": {"type": "string"},
        "interpretation": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
    "required": ["observed", "interpretation", "evidence_refs", "confidence"],
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    # No `maxItems` on these arrays -- Anthropic's structured-output
    # json_schema format rejects it with a real 400 Bad Request ("For
    # 'array' type, property 'maxItems' is not supported"; confirmed
    # against the live API this phase -- see report.producer_synthesis's
    # own _SCHEMA comment for the same finding). Each MAX_* constant is
    # still enforced -- just at the application layer, by
    # report.validation.validate_music_trend_signals.
    "properties": {
        "genre_signals": {"type": "array", "items": _SIGNAL_ITEM_SCHEMA},
        "production_notes": {"type": "array", "items": _SIGNAL_ITEM_SCHEMA},
        "producer_references": {"type": "array", "items": _SIGNAL_ITEM_SCHEMA},
        "kpop_ar_notes": {"type": "array", "items": _SIGNAL_ITEM_SCHEMA},
    },
    "required": ["genre_signals", "production_notes", "producer_references", "kpop_ar_notes"],
    "additionalProperties": False,
}


def canonical_json(obj):
    """Same canonicalization contract as report.producer_synthesis's own
    copy -- kept independent so this module's input-hash contract can
    never silently drift just because that module's does."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_evidence_catalog(spotify_chart, tiktok_chart, industry_news):
    """Real evidence only -- see module docstring for exactly what's
    available and why. Unlike report.producer_synthesis.build_evidence_
    catalog (which deliberately cites only industry-news TITLES to avoid
    re-explaining a fact the Music Industry section already owns), this
    catalog ALSO includes real SNIPPET text -- genre/production/producer-
    credit signals specifically require the article's own real prose,
    since that is the only place this kind of detail is ever real here.
    Ref assignment is in a fixed, deterministic order so the same
    evidence always produces the same catalog (required for input_hash
    reuse to actually trigger).

    MUSIC EVENT-LEVEL IDENTITY (TRUE lineage, not text matching): a
    MUSIC_INDUSTRY_NEWS entry's `event_key` is the SAME real event_key
    the originating `industry_news` item already carries (report.
    web_data_v2's own news-item dict, see _news_section/_raw_fallback_
    items) -- propagated here directly, at the one point this catalog is
    actually built FROM that real item, so no downstream reader ever has
    to re-derive it by matching text. None for every other evidence type
    (chart/cross-platform/catalog-revival facts have no corresponding
    real article at all -- never a fabricated event_key)."""
    catalog = []

    def add(evidence_type, summary, event_key=None):
        catalog.append({
            "ref": f"E{len(catalog) + 1}", "type": evidence_type, "summary": summary, "event_key": event_key,
        })

    if spotify_chart.get("state") == "NORMAL":
        for entry in spotify_chart.get("top10", []):
            add(
                "SPOTIFY_CHART_RANK",
                f"#{entry['rank']} {entry['canonical_artist']} - {entry['canonical_title']} "
                f"({_e_region(entry.get('region'))}, real chart snapshot)",
            )

    if tiktok_chart.get("state") == "NORMAL":
        for entry in tiktok_chart.get("top10", []):
            add(
                "TIKTOK_CHART_RANK",
                f"#{entry['rank']} {entry['canonical_artist']} - {entry['canonical_title']} "
                f"({_e_region(entry.get('region'))}, real chart snapshot)",
            )

    for item in industry_news:
        title = item.get("title")
        if not title:
            continue
        snippet = item.get("snippet")
        summary = title if not snippet else f"{title} — {snippet}"
        add("MUSIC_INDUSTRY_NEWS", summary, event_key=item.get("event_key"))

    return catalog


def _e_region(region):
    return region or "region unknown"


def compute_input_hash(prompt_version, catalog):
    """Deliberately independent of report_date_kst, same contract as
    report.producer_synthesis.compute_input_hash -- an identical catalog
    always hashes identically regardless of which day it's synthesized
    on."""
    payload = {"prompt_version": prompt_version, "catalog": catalog}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def find_reusable_interpretation(conn, input_hash):
    return conn.execute(
        "SELECT * FROM llm_interpretations WHERE input_hash = ? AND category = ? ORDER BY id DESC LIMIT 1",
        (input_hash, CATEGORY),
    ).fetchone()


def _build_prompts(catalog):
    system_prompt = (
        "CRITICAL LANGUAGE RULE, apply to every `observed` and `interpretation` "
        "field you write below: they MUST be written entirely in natural, fluent "
        "Korean (한국어). Never write them in English or mixed English/Korean, even "
        "though the evidence catalog you are given is in English.\n\n"
        "CRITICAL COUNT LIMIT, enforced by this system (not optional): "
        f"genre_signals has a HARD MAXIMUM of {MAX_GENRE_SIGNALS} items, "
        f"production_notes a HARD MAXIMUM of {MAX_PRODUCTION_NOTES}, "
        f"producer_references a HARD MAXIMUM of {MAX_PRODUCER_REFERENCES}, and "
        f"kpop_ar_notes a HARD MAXIMUM of {MAX_KPOP_AR_NOTES}. If the evidence "
        "supports more genuine signals than a limit allows, return only the "
        "strongest ones up to that limit -- never exceed it, and it is always "
        "fine to return fewer (including zero) if the evidence doesn't support "
        "that many.\n\n"
        "You are a music-industry trend analyst producing a short internal briefing, "
        "based ONLY on the evidence catalog you are given -- never on outside "
        "knowledge, never on anything not in the catalog. This applies even to facts "
        "you personally recognize as true about a well-known track or artist (e.g. a "
        "song's real release year) -- if that specific fact is not literally present "
        "in the evidence text for the ref(s) you cite, DO NOT state it, even as an "
        "aside in parentheses. Describe only what the evidence catalog itself says "
        "(e.g. current chart position, article text) -- never supplement it from your "
        "own training knowledge. Each evidence item has a `ref` label. You are "
        "looking for FOUR distinct kinds of real signal:\n"
        f"1. genre_signals (max {MAX_GENRE_SIGNALS}): a real genre, style, or format "
        "trend explicitly evidenced in the catalog (e.g. an article that names a "
        "genre, or a chart pattern across multiple same-genre entries). Never invent "
        "a genre for a track that has no genre information in the catalog.\n"
        f"2. production_notes (max {MAX_PRODUCTION_NOTES}): a real production/sonic "
        "characteristic (tempo, rhythm, instrumentation, arrangement, sound design) "
        "that the catalog text EXPLICITLY describes. Most days there will be none -- "
        "an empty list is the correct, expected, honest answer when the evidence "
        "doesn't describe production details. NEVER guess what a song probably "
        "sounds like from its title, chart rank, or artist alone.\n"
        f"3. producer_references (max {MAX_PRODUCER_REFERENCES}): a real producer, "
        "songwriter, or collaborator name that the catalog text EXPLICITLY states in "
        "connection with a specific real track/artist (e.g. 'produced by X', "
        "'featuring Y', an article literally listing collaborators). NEVER attribute "
        "a credit that isn't literally stated in the evidence -- if no evidence item "
        "names a real producer/collaborator, return an empty list.\n"
        f"4. kpop_ar_notes (max {MAX_KPOP_AR_NOTES}): only when the evidence "
        "genuinely connects to K-pop or A&R (artist & repertoire) relevance -- a "
        "K-pop act, a K-pop-adjacent format (e.g. a multinational girl/boy group), "
        "or an A&R-relevant signing/scouting/catalog fact explicitly in the "
        "evidence. Do not force a K-pop/A&R angle onto evidence that has none -- an "
        "empty list is correct and expected on most days.\n\n"
        "For every item in every list: `observed` is what the evidence literally says "
        "(you may quote or closely paraphrase it, but never add anything the evidence "
        "doesn't state), `interpretation` is your own analytical read of why it matters "
        "(clearly your inference, not presented as a fact), `evidence_refs` (the `ref` "
        "labels that support it -- NEVER invent a ref that isn't in the catalog, and "
        "never leave this empty), and `confidence` (LOW/MEDIUM/HIGH). If a category has "
        "no genuine real support in the evidence, return an empty list for it -- never "
        "pad any list with a weak, generic, or speculative entry just to fill it.\n\n"
        "Write `observed` and `interpretation` in natural, fluent Korean (한국어) -- "
        "the evidence catalog itself is in English, but your output text is read "
        "directly by a Korean audience and must never be English or a mix of "
        "English and Korean. Translate/paraphrase the English evidence into clear "
        "Korean prose yourself; do not leave any English sentence untranslated.\n\n"
        "Two more hard rules for `observed` and `interpretation` text: (1) NEVER write "
        "an evidence `ref` label (e.g. \"E1\", \"E11\") as literal text inside `observed` "
        "or `interpretation` -- that citation belongs ONLY in the separate structured "
        "`evidence_refs` field; readers never see ref labels, so a sentence like \"E11 "
        "shows...\" is a defect, write \"The article shows...\" instead. (2) Keep "
        "`interpretation` a SUPPORTED interpretation, never a leap: if the evidence only "
        "supports a general claim (e.g. a stadium booking supports \"large-scale demand\"), "
        "do not extend it into a specific unsupported claim (e.g. a specific production "
        "technique) the evidence never actually establishes -- when in doubt, state the "
        "weaker, better-supported claim."
    )
    user_prompt = canonical_json({"evidence_catalog": catalog})
    return system_prompt, user_prompt


def synthesize_music_trend_intelligence(conn, llm, spotify_chart, tiktok_chart, industry_news,
                                         report_date_kst, prompt_version=PROMPT_VERSION):
    """Returns None if the evidence catalog is empty (no LLM call at all,
    nothing persisted) -- mirrors report.producer_synthesis's own
    zero-evidence short-circuit exactly. `report_date_kst` is accepted for
    interface symmetry and future date-scoped logging only -- NOT part of
    compute_input_hash, so it never affects reuse.

    Otherwise returns a dict ready for report.persistence.
    persist_music_trend_intelligence: {"input_hash", "prompt_version",
    "model_used", "output_text", "input_tokens", "output_tokens",
    "estimated_cost", "parsed", "reused", "catalog"}. Callers MUST run
    report.validation.validate_music_trend_signals on `parsed` every
    time, whether reused or not, before persisting or displaying it."""
    catalog = build_evidence_catalog(spotify_chart, tiktok_chart, industry_news)
    if not catalog:
        return None

    input_hash = compute_input_hash(prompt_version, catalog)
    existing = find_reusable_interpretation(conn, input_hash)
    if existing is not None:
        return {
            "input_hash": input_hash, "prompt_version": prompt_version,
            "model_used": existing["model_used"], "output_text": existing["output_text"],
            "input_tokens": existing["input_tokens"], "output_tokens": existing["output_tokens"],
            "estimated_cost": existing["estimated_cost"], "parsed": json.loads(existing["output_text"]),
            "reused": True, "catalog": catalog,
        }

    system_prompt, user_prompt = _build_prompts(catalog)
    response = llm.generate_structured(system_prompt, user_prompt, _SCHEMA)
    return {
        "input_hash": input_hash, "prompt_version": prompt_version,
        "model_used": response.model_used, "output_text": response.raw_text,
        "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
        "estimated_cost": None, "parsed": response.parsed, "reused": False, "catalog": catalog,
    }
