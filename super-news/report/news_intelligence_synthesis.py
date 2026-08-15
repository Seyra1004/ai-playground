"""News Intelligence synthesis: WHAT HAPPENED / WHY IT MATTERS / WHAT TO
WATCH per real, already-displayed V2 news item -- ONE combined LLM call per
daily run covering every eligible item across AI/ECONOMY/SOCIETY (never one
call per item). Deliberately SEPARATE from report.ai_synthesis.py: that
module is Report V1's selection call (id + one-sentence `reason`), imported
directly by report.orchestrator.run_daily_report (V1's own orchestrator) and
shared with V1's Kakao-delivery text -- touching its schema would be a V1
change. This module never imports or is imported by report.ai_synthesis.py,
report.orchestrator.py, report.validation.py, or report.persistence.py, and
its own llm_interpretations rows use a dedicated category (NEWS_INTELLIGENCE_V2)
so they can never be confused with V1's NEWS_COMBINED rows or with Producer
Intelligence's MUSIC_PRODUCER_INTELLIGENCE rows. Same category-scoping
precedent as report.producer_synthesis.py.

Grounded strictly in evidence already computed elsewhere this run: each
item's title/snippet/source_count (real, already-collected/already-
displayed facts) is the ONLY input -- the model is never given outside
knowledge to draw on, and the system prompt explicitly forbids treating a
WHAT_TO_WATCH point as a stated future fact.

Reuse identity (compute_input_hash) covers report_date_kst + prompt_version
+ output_schema_version + the configured model + the exact item payload --
a prompt/schema/model change, or ANY item change (add/remove/reorder/edit),
produces a different hash and therefore a fresh LLM call, while an
identical call on the same day reuses the prior row's output with zero LLM
calls (same idempotency contract as report.ai_synthesis.synthesize_news).
"""

import hashlib
import json
import re
from datetime import datetime, timezone

from config import get_optional_env
from report.text_quality import is_malformed_synthesis_text, unsupported_fact_tokens

CATEGORY = "NEWS_INTELLIGENCE_V2"
PROMPT_VERSION = "v1"
OUTPUT_SCHEMA_VERSION = "v1"

# Duplicated from report/llm_anthropic.py's own DEFAULT_MODEL rather than
# imported -- matches report/producer_synthesis.py's established
# anti-drift-coupling convention (this module's cache-versioning contract
# must never silently change just because llm_anthropic.py's does). Only
# used as a cache-versioning HINT (folded into input_hash so a real model
# change can't silently reuse a stale synthesis) -- never used to select or
# construct the actual LLM client, which stays entirely report.
# llm_interface.build_llm()'s responsibility.
_DEFAULT_LLM_MODEL_FALLBACK = "claude-opus-5"

MAX_ITEMS_PER_CALL = 20
MAX_FIELD_LEN = 400

_FIELDS = ("what_happened", "why_it_matters", "what_to_watch")

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "what_happened": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "what_to_watch": {"type": "string"},
    },
    "required": ["id", "what_happened", "why_it_matters", "what_to_watch"],
    "additionalProperties": False,
}

_SCHEMA = {
    # NOTE: no "maxItems" on the items array -- Anthropic's real Structured
    # Outputs (output_config.format=json_schema) rejects it with a real
    # 400 invalid_request_error ("For 'array' type, property 'maxItems' is
    # not supported"), confirmed against the live API in the Phase 3B.2
    # smoke test. The MAX_ITEMS_PER_CALL application-level contract this
    # was meant to express is enforced in Python instead -- see
    # synthesize_news_intelligence's own guard below -- so the cap is not
    # lost, just moved out of the (unsupported) schema keyword.
    "type": "object",
    "properties": {"items": {"type": "array", "items": _ITEM_SCHEMA}},
    "required": ["items"],
    "additionalProperties": False,
}

_TAG_RE = re.compile(r"<[^>]+>")


def canonical_json(obj):
    """Same canonicalization contract as report.ai_synthesis.canonical_json
    (sorted keys, fixed separators, explicit UTF-8) -- kept as its own copy
    here rather than a shared import, matching report.producer_synthesis.
    canonical_json's own stated precedent, so this module's input-hash
    contract can never silently drift just because ai_synthesis's does."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _configured_model_hint():
    return get_optional_env("LLM_MODEL", _DEFAULT_LLM_MODEL_FALLBACK)


def _hashable_items(items):
    """Only real, already-collected evidence: title, snippet, and
    source_count (corroboration) -- never anything the model wasn't
    actually given. Order preserved exactly as given (never re-sorted
    here) -- the caller (report.news_intelligence_orchestrator) is
    responsible for deterministic ordering, matching report.ai_synthesis
    and report.producer_synthesis's own "never re-sorts" convention."""
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "snippet": item.get("snippet") or "",
            "source_count": item.get("source_count", 1),
        }
        for item in items
    ]


def compute_input_hash(report_date_kst, prompt_version, output_schema_version, model_hint, items):
    payload = {
        "report_date_kst": report_date_kst,
        "prompt_version": prompt_version,
        "output_schema_version": output_schema_version,
        "model_hint": model_hint,
        "items": _hashable_items(items),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def find_reusable_interpretation(conn, input_hash):
    return conn.execute(
        "SELECT * FROM llm_interpretations WHERE input_hash = ? AND category = ? ORDER BY id DESC LIMIT 1",
        (input_hash, CATEGORY),
    ).fetchone()


def _find_valid_reusable_interpretation(conn, input_hash, items_by_id):
    """Phase 3C.1 -> 3C.2 -> 3C.3: an interpretation is a reusable cache HIT
    only if its persisted output_text parses AND validate_news_
    intelligence() succeeds for EVERY current item -- not merely a
    non-empty subset. A PARTIAL result (some but not all expected ids
    validated -- e.g. the model dropped one item) is real, useful content
    for TODAY's display (see report.web_data_v2._attach_news_intelligence,
    which already degrades per-item and never hides real news either way),
    but it must never become a permanently reusable cache entry: the
    missing item(s) deserve a real future retry, not silence forever. Only
    a COMPLETE result (validated covers every id in items_by_id, order
    irrelevant) is cache-terminal. Searches every row sharing this
    (input_hash, category) tuple NEWEST to OLDEST (never just the single
    newest row, unlike find_reusable_interpretation above) so one newer
    partial/malformed row can never hide an older genuinely-COMPLETE one.
    Returns (row, validated_dict) for the first COMPLETE candidate found,
    or (None, None) if none is -- callers must then perform a fresh
    synthesis, never treat "no complete candidate" as a terminal failure
    by itself (a partial historical row is silently skipped here, not
    treated as evidence of anything). Malformed/partial rows are never
    deleted, updated, or otherwise mutated by this function -- purely a
    read-only search that skips over them."""
    expected_ids = set(items_by_id.keys())
    rows = conn.execute(
        "SELECT * FROM llm_interpretations WHERE input_hash = ? AND category = ? ORDER BY id DESC",
        (input_hash, CATEGORY),
    ).fetchall()
    for row in rows:
        try:
            parsed = json.loads(row["output_text"])
        except (ValueError, TypeError):
            continue  # malformed JSON itself -- not a valid candidate, try the next-oldest row
        validated = validate_news_intelligence(parsed, items_by_id)
        if validated and set(validated.keys()) == expected_ids:
            return row, parsed
    return None, None


def _build_prompts(items):
    system_prompt = (
        "You are a careful news analyst writing structured intelligence notes "
        "in Korean for a Korean-audience news dashboard. For EACH item below, "
        "using ONLY the title/snippet/source_count evidence given for that "
        "item -- never any outside knowledge, never information about any "
        "other item -- write three short Korean-language fields. "
        "what_happened: the verifiable fact, stated plainly, drawn only from "
        "the given title/snippet. why_it_matters: a reasonable, evidence-"
        "grounded implication of that fact -- never an unsupported market "
        "prediction, never a firm claim about something that has not "
        "happened yet. what_to_watch: a concrete follow-up point worth "
        "monitoring, phrased as an open question or a thing to watch, NEVER "
        "as a stated fact about the future. Never copy the title or snippet "
        "verbatim into any field -- each field must add real interpretive "
        "value, not repeat the input. No HTML or markup in any field. If the "
        "evidence for an item is too thin to responsibly fill a field, write "
        "a short, honest sentence saying so rather than inventing detail. "
        "Return exactly one object per item id, using ONLY the ids given "
        "below -- never invent an id, never omit an id that was given."
    )
    user_prompt = canonical_json({"items": _hashable_items(items)})
    return system_prompt, user_prompt


def _valid_field(value, forbidden_texts, evidence_text=""):
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > MAX_FIELD_LEN:
        return False
    if _TAG_RE.search(text):
        return False
    normalized = text.casefold()
    for forbidden in forbidden_texts:
        if forbidden and normalized == forbidden.strip().casefold():
            return False
    # NATIVE KOREAN TEXT QUALITY + FACT GROUNDING (quality-hardening
    # phase): reuses report.text_quality's shared, deterministic
    # gibberish/refusal-output detector (same real defect class report/
    # translation_validation.py already catches for translated text) plus
    # its evidence-grounding check -- every explicit YEAR/PERCENTAGE/
    # VERSION/CURRENCY-MAGNITUDE token this field asserts must be
    # traceable to the item's own real title/snippet evidence.
    if is_malformed_synthesis_text(text):
        return False
    if unsupported_fact_tokens(text, evidence_text):
        return False
    return True


def validate_news_intelligence(parsed_output, items_by_id):
    """Returns dict[id -> {"what_happened", "why_it_matters",
    "what_to_watch"}] for items whose EVERY field passed all checks
    (non-empty, length-bounded, no HTML/markup tags, not a verbatim copy of
    that item's own title/snippet). An item with any invalid/missing field
    is simply absent from the result -- never partially populated, never
    raises. A malformed root (not a dict with an 'items' list) returns an
    empty dict. An id that appears MORE THAN ONCE in the raw output is
    treated as unreliable for that id specifically -- excluded from the
    result entirely (never "last one wins") -- while every other,
    unambiguous id is still validated normally; this is the same
    per-item-isolation principle as an ordinary invalid field, just keyed
    on duplication instead of content. The result can structurally never
    contain more entries than `items_by_id` (it's built by looking up each
    output id against that real input set), so "output item count exceeds
    input item count" is already impossible by construction, independent of
    this duplicate check. Callers must treat "id missing from this result"
    and "malformed root" identically: render that item's AI intelligence as
    unavailable/pending, and keep showing the real news item (title/source/
    snippet) exactly as if this function had never been called -- news
    itself must never be hidden by a synthesis or validation failure."""
    result = {}
    if not isinstance(parsed_output, dict) or not isinstance(parsed_output.get("items"), list):
        return result
    seen_ids = set()
    duplicate_ids = set()
    for entry in parsed_output["items"]:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        entry_id = entry["id"]
        if entry_id in seen_ids:
            duplicate_ids.add(entry_id)
        seen_ids.add(entry_id)
    for entry in parsed_output["items"]:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        entry_id = entry["id"]
        if entry_id in duplicate_ids:
            continue  # unreliable -- the model returned this id more than once
        item = items_by_id.get(entry_id)
        if item is None:
            continue
        forbidden = (item.get("title"), item.get("snippet"))
        evidence_text = f"{item.get('title') or ''} {item.get('snippet') or ''}"
        fields = {}
        ok = True
        for field in _FIELDS:
            value = entry.get(field)
            if not _valid_field(value, forbidden, evidence_text):
                ok = False
                break
            fields[field] = value.strip()
        if ok:
            result[entry_id] = fields
    return result


def synthesize_news_intelligence(conn, llm, items, report_date_kst,
                                  prompt_version=PROMPT_VERSION,
                                  output_schema_version=OUTPUT_SCHEMA_VERSION):
    """items: the real, already-selected/displayed V2 news items (caller is
    responsible for scoping to LEAD/STANDARD tier + intelligence-eligible
    categories -- see report.news_intelligence_orchestrator). Returns None
    if `items` is empty (no LLM call is made, nothing persisted). Otherwise
    returns a dict ready for persist_news_intelligence: {"input_hash",
    "prompt_version", "model_used", "output_text", "input_tokens",
    "output_tokens", "estimated_cost", "parsed", "reused"}. Callers MUST
    run validate_news_intelligence on `parsed` EVERY time, whether `reused`
    is True or False, before persisting or displaying it -- reuse means
    "skip the LLM call," never "skip validation" (same rule report.
    producer_orchestrator already documents for Producer Intelligence).

    Raises ValueError if len(items) > MAX_ITEMS_PER_CALL -- this cap used
    to live in _SCHEMA's own "maxItems" keyword, but Anthropic's real
    Structured Outputs rejects that keyword on an array type (confirmed
    against the live API), so the cap is enforced here in Python instead;
    it is a caller-contract violation (the orchestrator is responsible for
    scoping items before calling this), not a normal degraded-service
    outcome, so it fails loud rather than silently truncating the batch."""
    if not items:
        return None
    if len(items) > MAX_ITEMS_PER_CALL:
        raise ValueError(
            f"synthesize_news_intelligence received {len(items)} items, "
            f"exceeding MAX_ITEMS_PER_CALL={MAX_ITEMS_PER_CALL}."
        )

    model_hint = _configured_model_hint()
    input_hash = compute_input_hash(report_date_kst, prompt_version, output_schema_version, model_hint, items)
    items_by_id = {item["id"]: item for item in items}
    existing, existing_parsed = _find_valid_reusable_interpretation(conn, input_hash, items_by_id)
    if existing is not None:
        return {
            "input_hash": input_hash, "prompt_version": prompt_version,
            "model_used": existing["model_used"], "output_text": existing["output_text"],
            "input_tokens": existing["input_tokens"], "output_tokens": existing["output_tokens"],
            "estimated_cost": existing["estimated_cost"], "parsed": existing_parsed,
            "reused": True,
        }

    system_prompt, user_prompt = _build_prompts(items)
    response = llm.generate_structured(system_prompt, user_prompt, _SCHEMA)
    return {
        "input_hash": input_hash, "prompt_version": prompt_version,
        "model_used": response.model_used, "output_text": response.raw_text,
        "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
        "estimated_cost": None, "parsed": response.parsed, "reused": False,
    }


def persist_news_intelligence(conn, runs_row_id, synthesis_result):
    """Writes ONE llm_interpretations row under CATEGORY. Deliberately
    self-contained here rather than added to report/persistence.py: V1's
    orchestrator.py imports persistence.py directly, and keeping every
    News-Intelligence write path in this module (never touching
    persistence.py at all) makes the "this file is never on any V1 code
    path" guarantee structural, not just a matter of which functions happen
    to get called. Caller commits (matches report.persistence.
    persist_producer_intelligence's same transaction-ownership contract:
    this function issues the INSERT but does not commit/rollback itself)."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, input_tokens,
            output_tokens, estimated_cost, output_text, confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'MEDIUM', ?)""",
        (runs_row_id, CATEGORY, synthesis_result["model_used"], synthesis_result["prompt_version"],
         synthesis_result["input_hash"], synthesis_result["input_tokens"], synthesis_result["output_tokens"],
         synthesis_result["estimated_cost"], synthesis_result["output_text"], now),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
