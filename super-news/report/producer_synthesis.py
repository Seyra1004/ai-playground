"""Producer Intelligence synthesis: ONE combined LLM call per daily report
covering all currently-real music intelligence evidence (Early Signal /
Catalog Revival / Cross-Platform / notable Spotify new entries / Music
Industry news) -- never one call per track, never one call per entity.

Grounded strictly in evidence already computed elsewhere this run: the LLM
is given a labeled evidence catalog (build_evidence_catalog) and may only
cite `ref` labels that exist in it. report.validation.
validate_producer_insights enforces this the same way news synthesis
enforces id-grounding for selections -- the model can never invent a
signal, cite a nonexistent ref, or fabricate a forecast; it can only
interpret/recommend on top of facts that already exist.

Reuse identity (compute_input_hash) is deliberately date-independent: it
covers prompt_version + the canonical evidence catalog only, NOT
report_date_kst. An unchanged evidence catalog reused on a LATER calendar
day still reuses the prior output with zero new LLM calls -- the date is
recorded as run metadata (via the caller's runs_row_id), never as part of
the synthesis identity, so it can never by itself force an unnecessary
re-synthesis. find_reusable_interpretation is scoped to this module's own
CATEGORY, so it can never accidentally reuse a NEWS_COMBINED row or vice
versa.

Callers MUST run report.validation.validate_producer_insights on the
returned `parsed` EVERY time, whether `reused` is True or False, before
persisting or displaying it -- reuse means "skip the LLM call," never
"skip validation." Returns None (no LLM call at all, nothing persisted)
when there is no meaningful evidence yet -- "nothing worth synthesizing
today" is a legitimate empty day, never padded with a generic
recommendation. A caller-side validation failure must likewise never fall
back to a fabricated deterministic recommendation -- see
report.persistence.persist_producer_intelligence's caller contract.
"""

import hashlib
import json

PROMPT_VERSION = "v2"
MAX_INSIGHTS = 5
CATEGORY = "MUSIC_PRODUCER_INTELLIGENCE"

_INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        # MUSIC INTELLIGENCE COMPLETION phase's explicit 6-question
        # contract, replacing the older bare action/why shape (zero real
        # rows existed under the old schema when this changed -- see
        # SUPER_NEWS_HANDOFF.md). what_is_moving is the OBSERVED FACT
        # (must be directly grounded in evidence_refs); the other three
        # are explicitly the model's own AI INFERENCE, never presented as
        # fact.
        "what_is_moving": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "what_to_watch": {"type": "string"},
        "what_could_i_make_now": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
    "required": [
        "what_is_moving", "why_it_matters", "what_to_watch", "what_could_i_make_now",
        "evidence_refs", "confidence",
    ],
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    # No `maxItems` here -- Anthropic's structured-output json_schema
    # format rejects it with a real 400 Bad Request ("For 'array' type,
    # property 'maxItems' is not supported"), which is exactly why every
    # real production Producer Intelligence run has failed at the
    # synthesis API call since this schema was introduced (confirmed
    # against the live API during the MUSIC INTELLIGENCE COMPLETION
    # phase). MAX_INSIGHTS is still enforced -- just at the application
    # layer, by report.validation.validate_producer_insights, the same
    # place that already re-checks every other invariant in this output.
    "properties": {"insights": {"type": "array", "items": _INSIGHT_SCHEMA}},
    "required": ["insights"],
    "additionalProperties": False,
}


def canonical_json(obj):
    """Same canonicalization contract as report.ai_synthesis.canonical_json
    (sorted keys, fixed separators, explicit UTF-8) -- kept as its own copy
    here rather than a shared import so this module's input-hash contract
    can never silently drift just because ai_synthesis's does."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_evidence_catalog(intelligence, spotify_chart, industry_news):
    """Turns already-computed, already-real facts into a labeled catalog
    the LLM can cite by `ref` -- never includes anything not already
    computed elsewhere this run, never invents a TikTok chart/trend signal
    (no TikTok evidence source exists here at all, matching the rest of
    V2). Ref assignment (E1, E2, ...) is in a fixed category order over
    inputs that are themselves already deterministically ordered (sorted
    source names, the diff/candidate-selection modules' own deterministic
    ordering), so the same evidence set always produces the same catalog
    -- required for input_hash stability and for reuse to actually
    trigger.

    `industry_news`: list of {"title", "reason", "snippet"} dicts (the
    TIKTOK/SPOTIFY news categories' already-selected, already-validated
    items) -- only `title` is cited here. `reason`/`snippet` are already
    that news item's OWN fact (context / why-it-matters) shown in the
    Music Industry section; citing them again here would have Producer
    Intelligence re-explain a fact instead of owning a new one (the
    fact-ownership rule), so only the identifying headline is included.

    MUSIC EVENT-LEVEL IDENTITY (TRUE lineage, not text matching): a
    MUSIC_INDUSTRY_NEWS entry's `event_key` is the SAME real event_key
    the originating `industry_news` item already carries -- propagated
    here directly, at the one point this catalog is actually built FROM
    that real item. None for every other evidence type here (EARLY_
    SIGNAL/CATALOG_REVIVAL/CROSS_PLATFORM/SPOTIFY_NEW_ENTRY are all chart/
    music_entity facts with no corresponding real article -- never a
    fabricated event_key)."""
    catalog = []

    def add(evidence_type, summary, event_key=None):
        catalog.append({
            "ref": f"E{len(catalog) + 1}", "type": evidence_type, "summary": summary, "event_key": event_key,
        })

    for source_name in sorted(intelligence["early_signal"].keys()):
        for c in intelligence["early_signal"][source_name]:
            add(
                "EARLY_SIGNAL",
                f"[{source_name}] {c['canonical_artist']} - {c['canonical_title']} "
                f"(+{int(c['rank_delta'])} rank positions)",
            )

    for source_name in sorted(intelligence["catalog_revival"].keys()):
        for c in intelligence["catalog_revival"][source_name]:
            add(
                "CATALOG_REVIVAL",
                f"[{source_name}] {c['canonical_artist']} - {c['canonical_title']} "
                f"(first seen {c['age_days']}d ago, {c['gap_days']}d observation gap, approximate)",
            )

    for entry in intelligence["cross_platform"]:
        add(
            "CROSS_PLATFORM",
            f"{entry['canonical_artist']} - {entry['canonical_title']} rising simultaneously on "
            f"{', '.join(entry['sources'])}",
        )

    if spotify_chart["state"] == "NORMAL":
        for e in spotify_chart["new_entries"]:
            add("SPOTIFY_NEW_ENTRY", f"{e['canonical_artist']} - {e['canonical_title']} debuted at #{e['rank']}")

    for item in industry_news:
        # Title only -- reason/snippet are already the Music Industry
        # section's own fact (context / why-it-matters), and Producer
        # Intelligence must cite a fact, not re-explain it (fact-ownership
        # rule). Keeps the catalog compact too.
        title = item.get("title")
        if title:
            add("MUSIC_INDUSTRY_NEWS", title, event_key=item.get("event_key"))

    return catalog


def compute_input_hash(prompt_version, catalog):
    """Deliberately independent of report_date_kst -- see module
    docstring. An identical catalog always hashes identically regardless
    of which day it's being synthesized on."""
    payload = {"prompt_version": prompt_version, "catalog": catalog}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def find_reusable_interpretation(conn, input_hash):
    return conn.execute(
        "SELECT * FROM llm_interpretations WHERE input_hash = ? AND category = ? ORDER BY id DESC LIMIT 1",
        (input_hash, CATEGORY),
    ).fetchone()


def _build_prompts(catalog):
    system_prompt = (
        "CRITICAL LANGUAGE RULE, apply to every field you write below: "
        "what_is_moving, why_it_matters, what_to_watch, and what_could_i_make_now "
        "MUST be written entirely in natural, fluent Korean (한국어). Never write "
        "any of those four fields in English or mixed English/Korean, even though "
        "the evidence catalog you are given is in English.\n\n"
        f"CRITICAL COUNT LIMIT, enforced by this system (not optional): return a "
        f"HARD MAXIMUM of {MAX_INSIGHTS} insights total, never more. If the "
        f"evidence supports more than {MAX_INSIGHTS} genuine insights, return only "
        f"the {MAX_INSIGHTS} strongest ones -- never exceed this limit, and it is "
        "always fine to return fewer (including zero) if the evidence doesn't "
        "support that many.\n\n"
        "You are a senior A&R / production-intelligence analyst writing a short, "
        "internal briefing for music producers, based ONLY on the evidence catalog "
        "you are given -- never on outside knowledge, never on anything not in the "
        "catalog. Each evidence item has a `ref` label. Write up to "
        f"{MAX_INSIGHTS} genuinely new insights. Each insight must answer, in order:\n"
        "1. what_is_moving -- the OBSERVED FACT. State only what the evidence "
        "literally shows (a real chart move, a real news item). This must be a "
        "fact grounded in evidence_refs, never your own opinion or speculation.\n"
        "2. why_it_matters -- your own AI INFERENCE about why this fact is "
        "significant. Clearly your interpretation, not presented as an additional "
        "fact.\n"
        "3. what_to_watch -- your own AI INFERENCE about what a producer should "
        "keep an eye on next, grounded in the same evidence -- never a baseless "
        "prediction with invented specifics (no fabricated dates, numbers, or "
        "names not in the evidence).\n"
        "4. what_could_i_make_now -- your own AI INFERENCE: a concrete MUSIC-MAKING "
        "action a working producer/composer/songwriter could act on today because "
        "of this signal -- e.g. a specific songwriting/arrangement/production/"
        "sound-selection/genre-blending direction to try, a demo strategy, an "
        "artist-targeting or A&R pitching angle, or a rights/business decision "
        "directly tied to the signal. This is advice about MAKING MUSIC or MAKING "
        "MUSIC-BUSINESS DECISIONS, from a producer/A&R professional's own point of "
        "view -- it must NEVER be advice about writing a newsletter, article, blog "
        "post, explainer, timeline, recap, or any other piece of editorial/content "
        "about this news; a producer reading this is not a content creator and has "
        "no use for that suggestion. Never invent a specific sonic characteristic "
        "(BPM, key, chord progression, instrumentation) the evidence doesn't "
        "actually support -- ground the suggestion in what's real (the genre/"
        "platform/rights/chart fact itself), not a fabricated musical detail. Only "
        "write a what_could_i_make_now when the evidence genuinely touches one of: "
        "songwriting, composition, melody, harmony, rhythm, arrangement, sound "
        "design, production, vocal production, a mixing-relevant production "
        "observation, genre movement, hit-song structure, an artist's sonic "
        "direction, label/A&R direction, K-pop/global pop market movement, release "
        "strategy, a music-platform change, copyright/licensing, AI music, or a "
        "songwriter/producer business impact -- if the evidence has no real "
        "connection to any of those, DROP this catalog entry from your output "
        "entirely rather than inventing a music-relevance connection that isn't "
        "there (same rule as the empty-list guidance above, applied per-entry).\n"
        "5. evidence_refs -- the `ref` labels that support what_is_moving -- NEVER "
        "invent a ref that isn't in the catalog, and never leave this empty.\n"
        "6. confidence -- LOW/MEDIUM/HIGH.\n\n"
        "Do not restate a catalog entry verbatim as what_is_moving without any "
        "synthesis across the other three fields. If the evidence is too thin or "
        "one-dimensional to support any genuine insight, return an empty list "
        "rather than padding with a weak or generic one.\n\n"
        "Write what_is_moving/why_it_matters/what_to_watch/what_could_i_make_now in "
        "natural, fluent Korean (한국어) -- the evidence catalog itself is in "
        "English, but your output text is read directly by a Korean audience and "
        "must never be English or a mix of English and Korean. Translate/paraphrase "
        "the English evidence into clear Korean prose yourself; do not leave any "
        "English sentence untranslated.\n\n"
        "Two more hard rules for what_is_moving/why_it_matters/what_to_watch/"
        "what_could_i_make_now text: (1) NEVER write an evidence `ref` label (e.g. "
        "\"E1\", \"E11\") as literal text inside these fields -- that citation belongs "
        "ONLY in the separate structured `evidence_refs` field; a sentence like \"E11 "
        "shows...\" is a defect, write \"The article shows...\" instead. (2) Keep every "
        "AI INFERENCE field a SUPPORTED interpretation, never a leap: if the evidence "
        "only supports a general claim (e.g. a stadium booking supports \"large-scale "
        "demand\"), do not extend it into a specific unsupported claim (e.g. a specific "
        "production technique) the evidence never actually establishes -- when in "
        "doubt, state the weaker, better-supported claim."
    )
    user_prompt = canonical_json({"evidence_catalog": catalog})
    return system_prompt, user_prompt


def synthesize_producer_intelligence(conn, llm, intelligence, spotify_chart, industry_news, report_date_kst,
                                      prompt_version=PROMPT_VERSION):
    """Returns None if the evidence catalog is empty (no LLM call is made,
    nothing is persisted) -- mirrors report.ai_synthesis.synthesize_news's
    zero-candidates short-circuit exactly. `report_date_kst` is accepted
    for interface symmetry with synthesize_news and so a future caller can
    log/trace by date, but is NOT part of compute_input_hash (see module
    docstring) and does not affect reuse.

    Otherwise returns a dict ready for
    report.persistence.persist_producer_intelligence: {"input_hash",
    "prompt_version", "model_used", "output_text", "input_tokens",
    "output_tokens", "estimated_cost", "parsed", "reused", "catalog"}.
    Callers must validate `parsed` (report.validation.
    validate_producer_insights) before using or persisting it, regardless
    of `reused`."""
    catalog = build_evidence_catalog(intelligence, spotify_chart, industry_news)
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
